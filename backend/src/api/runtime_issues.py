"""Runtime issue capture: sanitising log handler + issue payload builders."""

from __future__ import annotations

import contextlib
import logging
import re
from collections import deque
from datetime import UTC, datetime
from typing import Any

from src.core.sandbox_manager import SandboxRuntimeContext

logger = logging.getLogger("ws_server")

_RUNTIME_ISSUE_LIMIT = 80
_runtime_issues: deque[dict[str, str]] = deque(maxlen=_RUNTIME_ISSUE_LIMIT)


def _sanitize_log_message(message: str) -> str:
    sanitized = re.sub(r"(token=)[^&\\s]+", r"\\1***", str(message))
    sanitized = re.sub(r"(Bearer\\s+)[A-Za-z0-9._-]+", r"\\1***", sanitized)
    return sanitized
class _RuntimeIssueHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno < logging.WARNING:
            return
        try:
            message = _sanitize_log_message(record.getMessage())
        except Exception:
            message = "日志解析失败"
        _runtime_issues.appendleft(
            {
                "level": record.levelname,
                "logger": record.name,
                "message": message,
                "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            }
        )


def _install_runtime_issue_handler() -> None:
    root_logger = logging.getLogger()
    for handler in list(root_logger.handlers):
        if getattr(handler, "_simlaw_runtime_issue_handler", False):
            root_logger.removeHandler(handler)
    handler = _RuntimeIssueHandler(level=logging.WARNING)
    handler._simlaw_runtime_issue_handler = True
    root_logger.addHandler(handler)


_install_runtime_issue_handler()
_RUNTIME_STAGE_LABELS = {
    "RECEPTION": "前台导引",
    "LC": "委托洽谈",
    # ── 刑事阶段 ──
    "INV": "侦查阶段",
    "PR": "审查起诉阶段",
    "DS": "辩护词起草",
    "CR": "刑事一审庭审",
    "CRA": "刑事二审庭审",
}
_MODEL_UNAVAILABLE_HINTS = (
    "503",
    "model_not_found",
    "no available channel for model",
    "service unavailable",
    "service temporarily unavailable",
)

def _resolve_stage_label(scenario_type: str, fallback: str = "") -> str:
    normalized = str(scenario_type or "").strip().upper()
    return _RUNTIME_STAGE_LABELS.get(normalized) or fallback or normalized or "未知阶段"


def _build_runtime_issue_payload(
    *,
    case_id: str,
    scenario_type: str,
    code: str,
    message: str,
    retryable: bool,
    stage_label: str = "",
) -> dict[str, Any]:
    return {
        "scope": "sandbox",
        "case_id": str(case_id or "").strip(),
        "scenario_type": str(scenario_type or "").strip(),
        "stage_label": _resolve_stage_label(scenario_type, fallback=stage_label),
        "code": str(code or "").strip(),
        "message": str(message or "").strip(),
        "retryable": bool(retryable),
        "occurred_at": datetime.now(UTC).isoformat(),
    }


def _normalize_runtime_issue_from_exception(
    *,
    case_id: str,
    scenario_type: str,
    exc: Exception,
    stage_label: str = "",
    event_type: str = "",
    handler_name: str = "",
) -> dict[str, Any] | None:
    raw_message = str(exc or "").strip()
    normalized = raw_message.lower()
    display_stage = _resolve_stage_label(scenario_type, fallback=stage_label)
    if not any(hint in normalized for hint in _MODEL_UNAVAILABLE_HINTS):
        detail = raw_message.rstrip("。.!? ") or f"{display_stage}发生未知运行异常"
        debug_parts = []
        exception_type = type(exc).__name__ if exc is not None else ""
        if exception_type:
            debug_parts.append(f"异常类型：{exception_type}")
        if event_type:
            debug_parts.append(f"事件：{event_type}")
        if handler_name:
            debug_parts.append(f"处理器：{handler_name}")
        if debug_parts:
            detail = f"{detail}（{'；'.join(debug_parts)}）"
        return _build_runtime_issue_payload(
            case_id=case_id,
            scenario_type=scenario_type,
            stage_label=display_stage,
            code="SCENARIO_RUNTIME_ERROR",
            message=f"{display_stage}运行失败：{detail}。已停止本轮模拟，请检查后端日志并修复后重新开始。",
            retryable=False,
        )

    return _build_runtime_issue_payload(
        case_id=case_id,
        scenario_type=scenario_type,
        stage_label=display_stage,
        code="MODEL_UNAVAILABLE",
        message=f"{display_stage}生成失败：当前模型不可用，已停止本轮模拟，请切换模型后重新开始。",
        retryable=True,
    )


def _reset_runtime_transient_state(context: SandboxRuntimeContext) -> None:
    _set_runtime_engine_paused(getattr(context, "engine", None), False)

    registry = getattr(context, "registry", None)
    storage = getattr(context, "storage_manager", None)
    event_bus = getattr(context, "event_bus", None)

    if registry is not None and storage is not None:
        for lawyer in registry.get_agents_by_type("lawyer"):
            if getattr(lawyer, "config_path", None):
                with contextlib.suppress(Exception):
                    storage.update_agent_field(lawyer.config_path, "current_handling_case", None)
                    storage.update_agent_field(lawyer.config_path, "case_queue", [])

        for judge in registry.get_agents_by_type("judge"):
            if getattr(judge, "config_path", None):
                with contextlib.suppress(Exception):
                    storage.update_agent_field(judge.config_path, "current_handling_case", None)

        for receptionist in registry.get_agents_by_type("receptionist"):
            front_desk_queue = getattr(receptionist, "_front_desk_queue", None)
            if hasattr(front_desk_queue, "clear"):
                front_desk_queue.clear()
            reserved_lawyers = getattr(receptionist, "_reserved_lawyers", None)
            if hasattr(reserved_lawyers, "clear"):
                reserved_lawyers.clear()
            queued_client_sofas = getattr(receptionist, "_queued_client_sofas", None)
            if hasattr(queued_client_sofas, "clear"):
                queued_client_sofas.clear()
            queued_client_wait_spots = getattr(receptionist, "_queued_client_wait_spots", None)
            if hasattr(queued_client_wait_spots, "clear"):
                queued_client_wait_spots.clear()
            setattr(receptionist, "_front_desk_busy", False)
            setattr(receptionist, "_last_assigned_lawyer_id", "")

    if event_bus is not None and hasattr(event_bus, "get_active_scenarios_snapshot"):
        active_scenarios = event_bus.get_active_scenarios_snapshot()
        for active_case_id in list(active_scenarios.keys()):
            with contextlib.suppress(Exception):
                event_bus.unregister_active_scenario(active_case_id)
        if context.checkpoint_mgr is not None and hasattr(context.checkpoint_mgr, "sync_active_scenarios_from_event_bus"):
            with contextlib.suppress(Exception):
                context.checkpoint_mgr.sync_active_scenarios_from_event_bus()

    orchestrator = getattr(context, "orchestrator", None)
    if orchestrator is not None:
        occupied_locations = getattr(orchestrator, "_occupied_locations", None)
        if hasattr(occupied_locations, "clear"):
            occupied_locations.clear()
        waiting_queues = getattr(orchestrator, "_waiting_queues", None)
        if hasattr(waiting_queues, "clear"):
            waiting_queues.clear()
        court_reservations = getattr(orchestrator, "_court_reservations", None)
        if hasattr(court_reservations, "clear"):
            court_reservations.clear()
        judge_reservations = getattr(orchestrator, "_judge_reservations", None)
        if hasattr(judge_reservations, "clear"):
            judge_reservations.clear()
        trial_queues = getattr(orchestrator, "_trial_queues", None)
        if isinstance(trial_queues, dict):
            for queue in trial_queues.values():
                if hasattr(queue, "clear"):
                    queue.clear()


async def _report_sandbox_runtime_issue(
    context: SandboxRuntimeContext,
    payload: dict[str, Any],
) -> bool:
    if not payload:
        return False

    context.last_error = dict(payload)
    _reset_runtime_transient_state(context)

    if context.checkpoint_mgr is not None and hasattr(context.checkpoint_mgr, "mark_session_paused"):
        with contextlib.suppress(Exception):
            context.checkpoint_mgr.mark_session_paused()

    runtime_engine = getattr(context, "engine", None)
    if runtime_engine is not None and hasattr(runtime_engine, "broadcast_case_runtime_issue"):
        await runtime_engine.broadcast_case_runtime_issue(
            case_id=payload.get("case_id", ""),
            scenario_type=payload.get("scenario_type", ""),
            stage_label=payload.get("stage_label", ""),
            code=payload.get("code", ""),
            message=payload.get("message", ""),
            retryable=bool(payload.get("retryable", False)),
            occurred_at=payload.get("occurred_at", ""),
        )

    task = context.simulation_task
    if task is not None and not task.done():
        task.cancel()

    return True


def get_runtime_issues() -> list[dict[str, str]]:
    """Snapshot of captured runtime issues (newest first)."""
    return list(_runtime_issues)


# bottom import to break the cycle agent_status <-> runtime_issues
from .agent_status import _set_runtime_engine_paused  # noqa: E402, F401
