"""Simulation lifecycle: runtime init/reset, case launch, run & resume loops."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import random
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from src.core.checkpoint_manager import CheckpointManager
from src.core.event_bus import EventBus, EventType
from src.core.file_storage_manager import FileStorageManager
from src.core.sandbox_manager import SandboxRuntimeContext
from src.data.data_loader import DataLoader
from src.orchestration.agent_registry import AgentRegistry
from src.orchestration.case_fsm import CaseStateMachine
from src.orchestration.scenario_orchestrator import ScenarioOrchestrator
from src.prompts.prompt_assembler import PromptAssembler
from src.scenarios.legal_consultation import LegalConsultationScenario
from src.simulation.location_registry import load_registry_from_map
from src.simulation.ws_frontend_engine import WebSocketFrontendEngine
from src.utils.case_progress import infer_case_state_from_artifacts, normalize_case_state
from src.utils.memory_initializer import initialize_client_memory, initialize_lawyer_memory

from . import app_state
from .case_catalog import _normalize_case_identifier
from .deps import _resolve_sandbox_context_by_id
from .player_gateway_admin import _player_lawyer_mode_for_engine
from .schemas import _CaseLaunchRequest

logger = logging.getLogger("ws_server")

_AGENT_DISCOVERY_ROOT_DIRS = ("cases", "law_firms", "court_system")


def _is_user_scoped_sandbox_root(storage_root: Path) -> bool:
    root = Path(storage_root)
    has_users_dir = (root / "users").is_dir()
    has_agent_roots = any((root / dirname).exists() for dirname in _AGENT_DISCOVERY_ROOT_DIRS)
    return has_users_dir and not has_agent_roots
async def _broadcast_sandbox_event(sandbox_id: str, payload: dict) -> None:
    context = _resolve_sandbox_context_by_id(sandbox_id)
    if context is None:
        return

    disconnected_clients = []
    for client in list(getattr(context, "connected_clients", set())):
        try:
            await client.send_json(payload)
        except Exception:
            disconnected_clients.append(client)

    for client in disconnected_clients:
        context.connected_clients.discard(client)


async def _close_sandbox_realtime_clients(
    context: SandboxRuntimeContext,
    *,
    code: int = 1012,
    reason: str = "sandbox runtime reset",
) -> None:
    """Close stale WebSocket clients before replacing a sandbox runtime context."""
    runtime_engine = getattr(context, "engine", None)
    clients = set(getattr(context, "connected_clients", set()) or set())
    engine_clients = getattr(runtime_engine, "clients", None)
    if engine_clients is not None:
        clients.update(set(engine_clients))

    for client in list(clients):
        try:
            await client.close(code=code, reason=reason)
        except Exception:
            pass
        context.connected_clients.discard(client)
        if engine_clients is not None:
            engine_clients.discard(client)

    supported_clients = getattr(runtime_engine, "_dialogue_gate_supported_clients", None)
    if supported_clients is not None:
        for client in clients:
            supported_clients.discard(client)


def _reset_runtime_engine_state(runtime_engine: WebSocketFrontendEngine | None) -> None:
    if runtime_engine is None:
        return

    runtime_engine._paused = False
    resumed_event = getattr(runtime_engine, "_resumed_event", None)
    if resumed_event is not None and hasattr(resumed_event, "set"):
        resumed_event.set()
    ack_events = getattr(runtime_engine, "_ack_events", None)
    if hasattr(ack_events, "clear"):
        ack_events.clear()
    dialogue_gate_events = getattr(runtime_engine, "_dialogue_gate_events", None)
    if isinstance(dialogue_gate_events, dict):
        for event in list(dialogue_gate_events.values()):
            if hasattr(event, "set"):
                event.set()
        dialogue_gate_events.clear()
    agent_states = getattr(runtime_engine, "_agent_states", None)
    if hasattr(agent_states, "clear"):
        agent_states.clear()
    runtime_engine._active_dialogue_gate_id = None
    runtime_engine._active_dialogue_gate_payload = None
    runtime_engine._buffered_dialogue_message = None


async def _cancel_sandbox_simulation_task(
    context: SandboxRuntimeContext,
    timeout_seconds: float = 2.0,
) -> bool:
    task = context.simulation_task
    if task is not None and not task.done():
        task.cancel()
        try:
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=timeout_seconds)
        except TimeoutError:
            return False

    context.simulation_task = None
    return True


def _initialize_runtime_state(
    *,
    existing_engine: WebSocketFrontendEngine | None = None,
    sandbox_data_dir: Path | None = None,
    set_global_engine: bool = True,
) -> tuple[EventBus, AgentRegistry, CheckpointManager, FileStorageManager, CaseStateMachine]:
    """(Re)build runtime state for startup and full simulation restarts."""
    runtime_data_dir = sandbox_data_dir or app_state.SANDBOX_DATA_DIR

    loc_registry = load_registry_from_map(app_state.MAP_JSON_PATH)
    runtime_engine = existing_engine
    if runtime_engine is None:
        runtime_engine = WebSocketFrontendEngine(
            loc_registry,
            fallback_speed=0.5,
            backend_authoritative=True,
            move_speed_px_per_second=150.0,
            map_json_path=app_state.MAP_JSON_PATH,
            frontend_mode=app_state.SIMLAW_FRONTEND_MODE,
            turn_mode=app_state.SIMLAW_TURN_MODE,
        )
    else:
        runtime_engine.registry = loc_registry
        runtime_engine.loc_registry = loc_registry
        runtime_engine._ack_events.clear()
        runtime_engine._agent_states.clear()
        runtime_engine._paused = False
        runtime_engine._resumed_event.set()
    if set_global_engine:
        app_state.engine = runtime_engine

    storage = FileStorageManager(base_dir=runtime_data_dir)
    runtime_event_bus = EventBus()
    fsm = CaseStateMachine(
        runtime_event_bus,
        storage,
        state_change_notifier=getattr(runtime_engine, "broadcast_state_change", None),
    )

    runtime_registry = AgentRegistry(runtime_data_dir, runtime_event_bus, storage, map_engine=runtime_engine)
    skip_global_registry_discovery = sandbox_data_dir is None and _is_user_scoped_sandbox_root(runtime_data_dir)
    if skip_global_registry_discovery:
        logger.info(
            "[Registry] Global registry discovery skipped: user-scoped sandbox root detected at %s",
            runtime_data_dir,
        )
    else:
        runtime_registry.discover_all()

    runtime_engine.agent_registry = runtime_registry
    runtime_engine.storage = storage
    runtime_registry.map_engine = runtime_engine
    for receptionist in runtime_registry.get_agents_by_type("receptionist"):
        receptionist.map_engine = runtime_engine

    runtime_checkpoint_mgr = CheckpointManager(runtime_data_dir / "checkpoints")
    runtime_checkpoint_mgr.set_event_bus(runtime_event_bus)

    orchestrator = ScenarioOrchestrator(
        runtime_registry,
        runtime_event_bus,
        fsm,
        storage,
        runtime_data_dir,
        map_engine=runtime_engine,
        checkpoint_manager=runtime_checkpoint_mgr,
    )
    runtime_engine.orchestrator = orchestrator

    for evt in EventType:
        sim_engine = runtime_engine

        async def _broadcast_event(payload: dict, event_name: str = evt.value) -> None:
            await sim_engine.broadcast_state_change(
                case_id=payload.get("case_id", ""),
                event=event_name,
            )

        runtime_event_bus.subscribe(evt.value, _broadcast_event)

    runtime_engine.restore_state_from_configs()
    return runtime_event_bus, runtime_registry, runtime_checkpoint_mgr, storage, fsm


def _set_engine_paused(paused: bool) -> None:
    if app_state.engine is None:
        return
    app_state.engine._paused = paused
    if paused:
        app_state.engine._resumed_event.clear()
    else:
        app_state.engine._resumed_event.set()


async def _sleep_respecting_pause(sim_engine: WebSocketFrontendEngine, duration: float) -> None:
    """Sleep while honoring pause/resume even in tests with lightweight engine doubles."""
    sleep_with_pause = getattr(sim_engine, "_sleep_with_pause", None)
    if callable(sleep_with_pause):
        await sleep_with_pause(duration)
        return

    remaining = max(float(duration), 0.0)
    while remaining > 0:
        if getattr(sim_engine, "_paused", False):
            resumed_event = getattr(sim_engine, "_resumed_event", None)
            if resumed_event is not None:
                await resumed_event.wait()
                continue
        step = min(remaining, 0.01)
        await asyncio.sleep(step)
        remaining -= step


def _get_closed_case_count(sim_event_bus: EventBus) -> int:
    getter = getattr(sim_event_bus, "get_closed_case_count", None)
    if callable(getter):
        return int(getter())
    return len(getattr(sim_event_bus, "_closed_cases", set()))


async def _wait_for_case_close_since(sim_event_bus: EventBus, previous_count: int) -> int:
    waiter = getattr(sim_event_bus, "wait_for_case_close_since", None)
    if callable(waiter):
        return int(await waiter(previous_count))

    while _get_closed_case_count(sim_event_bus) <= previous_count:
        await asyncio.sleep(0.05)
    return _get_closed_case_count(sim_event_bus)


async def _dispatch_case_launch_requests(
    *,
    requests: list[_CaseLaunchRequest],
    sim_engine: WebSocketFrontendEngine,
    sim_event_bus: EventBus,
) -> None:
    if not requests:
        return

    max_concurrent_cases = max(int(app_state.MAX_CONCURRENT_CASES or 0), 0)
    if max_concurrent_cases > 0:
        logger.info("案件全局并发上限已启用: %d", max_concurrent_cases)

    launched_case_ids: set[str] = set()
    closed_case_count = _get_closed_case_count(sim_event_bus)

    for index, request in enumerate(requests):
        while max_concurrent_cases > 0:
            active_case_count = max(0, len(launched_case_ids) - _get_closed_case_count(sim_event_bus))
            if active_case_count < max_concurrent_cases:
                break

            logger.info(
                "案件并发已达上限 %d，等待已有案件结案后继续投放",
                max_concurrent_cases,
            )
            closed_case_count = await _wait_for_case_close_since(sim_event_bus, closed_case_count)

        launch_result = await request.launch()
        if launch_result is False:
            logger.warning("案件 %s 启动失败，本次不占用并发槽", request.case_id)
            continue

        launched_case_ids.add(request.case_id)
        closed_case_count = _get_closed_case_count(sim_event_bus)

        if request.post_launch_delay > 0 and index < len(requests) - 1:
            logger.info(
                "案件 %s 已进入待出生队列，%.0f 秒后投放下一案",
                request.case_id.removeprefix("case_"),
                request.post_launch_delay,
            )
            await _sleep_respecting_pause(sim_engine, request.post_launch_delay)
            closed_case_count = _get_closed_case_count(sim_event_bus)


def _count_active_cases(storage: FileStorageManager | None, sim_registry: AgentRegistry | None) -> int:
    if storage is None or sim_registry is None:
        return 0

    active_cases = 0
    counted_case_ids: set[str] = set()
    for client in sim_registry.get_agents_by_type("client"):
        if not client.config_path:
            continue
        try:
            config = storage.load_agent_config(client.config_path)
        except FileNotFoundError:
            continue
        case_id = str(config.get("case_id") or "").strip()
        if not case_id:
            continue
        normalized_case_id = case_id if case_id.startswith("case_") else f"case_{case_id}"
        if normalized_case_id in counted_case_ids:
            continue
        counted_case_ids.add(normalized_case_id)
        try:
            case_runtime = storage.load_case_runtime(normalized_case_id)
        except FileNotFoundError:
            if config.get("party_role") != "plaintiff":
                continue
            case_state = str(config.get("case_state") or "").strip()
        else:
            case_state = str(case_runtime.get("overall_state") or "").strip()
        if case_state not in {"", "空闲", "已结案"}:
            active_cases += 1
    return active_cases


def _build_simulation_status() -> dict:
    session_state = app_state.checkpoint_mgr.load_session_state() if app_state.checkpoint_mgr else None
    persisted_status = str((session_state or {}).get("simulation_status") or "").strip().lower()
    task_running = app_state._simulation_task is not None and not app_state._simulation_task.done()
    paused = bool(app_state.engine and app_state.engine._paused)

    if task_running:
        status = "paused" if paused else "running"
    elif persisted_status in {"paused", "running"}:
        status = "paused"
    elif persisted_status == "completed":
        status = "completed"
    else:
        status = "idle"

    return {
        "status": status,
        "session_status": persisted_status or "idle",
        "session_id": (session_state or {}).get("session_id"),
        "paused": status == "paused",
        "simulation_running": task_running,
        "clients_connected": len(app_state.engine.clients) if app_state.engine else 0,
        "active_cases": _count_active_cases(getattr(app_state.engine, "storage", None), app_state.registry),
        "can_start": status in {"idle", "paused", "completed"},
        "can_pause": status == "running",
        "can_restart": True,
    }


def _reset_case_client_config(storage: FileStorageManager, agent_dir: Path) -> None:
    config = storage.load_agent_config(agent_dir)
    config.pop("chat_history_summary", None)
    config.pop("long_term_memory", None)
    designated_lawyer_id = str(
        config.get("designated_lawyer_id", "") or config.get("assigned_lawyer_id", "") or ""
    ).strip()

    config["case_state"] = "空闲"
    config["map_state"] = None
    config["designated_lawyer_id"] = designated_lawyer_id
    config["assigned_lawyer_id"] = ""
    storage.save_agent_config(agent_dir, config)
    initialize_client_memory(storage, str(agent_dir))
def _reset_case_runtime(storage: FileStorageManager, case_dir: Path) -> None:
    storage.save_case_runtime(
        case_dir.name,
        {
            "case_id": case_dir.name,
            "overall_state": "空闲",
            "plaintiff_state": "空闲",
            "defendant_state": "空闲",
            "active_party_role": "plaintiff",
        },
    )


def _reset_closed_case_for_restart(storage_root: Path, case_id: str) -> None:
    """Clear closed-case state so it can be restarted without a full sandbox reset."""
    from src.core.file_storage_manager import FileStorageManager

    storage = FileStorageManager(storage_root)
    case_dir = storage_root / "cases" / case_id

    # Reset case-level runtime
    _reset_case_runtime(storage, case_dir)

    # Reset plaintiff / defendant config case_state
    for party in ("plaintiff", "defendant"):
        agent_dir = case_dir / party
        if agent_dir.exists():
            _reset_case_client_config(storage, agent_dir)

    # Remove stale output artifacts for this case
    output_dir = storage_root / "output" / case_id
    if output_dir.exists():
        shutil.rmtree(output_dir)

    logger.info("[Start] 已重置已结案案件 %s，允许重新启动", case_id)


def _reset_lawyer_config(storage: FileStorageManager, agent_dir: Path) -> None:
    config = storage.load_agent_config(agent_dir)
    config.pop("chat_history_summary", None)
    config.pop("long_term_memory", None)
    config["current_handling_case"] = None
    config["case_queue"] = []
    config["map_state"] = None
    storage.save_agent_config(agent_dir, config)
    initialize_lawyer_memory(storage, str(agent_dir))
def _reset_judge_config(storage: FileStorageManager, agent_dir: Path) -> None:
    config = storage.load_agent_config(agent_dir)
    config.pop("chat_history_summary", None)
    config["current_handling_case"] = None
    config["case_queue"] = []
    config["map_state"] = None
    storage.save_agent_config(agent_dir, config)


def _reset_receptionist_config(storage: FileStorageManager, firm_dir: Path) -> None:
    config = storage.load_agent_config(firm_dir)
    config["map_state"] = None
    storage.save_agent_config(firm_dir, config)


def _reset_simulation_storage(storage: FileStorageManager) -> None:
    cases_dir = app_state.SANDBOX_DATA_DIR / "cases"
    if cases_dir.exists():
        for case_dir in sorted(cases_dir.iterdir()):
            if not case_dir.is_dir():
                continue
            for party_role in ("plaintiff", "defendant"):
                party_dir = case_dir / party_role
                if (party_dir / "config.yaml").exists():
                    _reset_case_client_config(storage, party_dir)
            _reset_case_runtime(storage, case_dir)

    firms_dir = app_state.SANDBOX_DATA_DIR / "law_firms"
    if firms_dir.exists():
        for firm_dir in sorted(firms_dir.iterdir()):
            if not firm_dir.is_dir():
                continue
            if (firm_dir / "config.yaml").exists():
                _reset_receptionist_config(storage, firm_dir)
            lawyers_dir = firm_dir / "lawyers"
            if not lawyers_dir.exists():
                continue
            for lawyer_dir in sorted(lawyers_dir.iterdir()):
                if (lawyer_dir / "config.yaml").exists():
                    _reset_lawyer_config(storage, lawyer_dir)

    court_dir = app_state.SANDBOX_DATA_DIR / "court_system"
    if court_dir.exists():
        for court_level_dir in sorted(court_dir.iterdir()):
            judges_dir = court_level_dir / "judges"
            if not judges_dir.exists():
                continue
            for judge_dir in sorted(judges_dir.iterdir()):
                if (judge_dir / "config.yaml").exists():
                    _reset_judge_config(storage, judge_dir)

    checkpoint_dir = app_state.SANDBOX_DATA_DIR / "checkpoints"
    if checkpoint_dir.exists():
        for checkpoint_file in checkpoint_dir.glob("*.yaml"):
            checkpoint_file.unlink()

    output_dir = app_state.SANDBOX_DATA_DIR / "output"
    if output_dir.exists():
        for child in output_dir.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()


async def _cancel_simulation_task(timeout_seconds: float = 2.0) -> bool:

    if app_state._simulation_task and not app_state._simulation_task.done():
        app_state._simulation_task.cancel()
        try:
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.wait_for(app_state._simulation_task, timeout=timeout_seconds)
        except TimeoutError:
            return False
    app_state._simulation_task = None
    return True


async def _start_or_resume_simulation() -> dict:

    if (
        app_state.engine is None
        or app_state.event_bus is None
        or app_state.registry is None
        or app_state.checkpoint_mgr is None
        or app_state.storage_manager is None
        or app_state.case_fsm is None
    ):
        raise HTTPException(status_code=503, detail="Simulation app_state.engine not ready")

    if app_state._simulation_task and not app_state._simulation_task.done():
        _set_engine_paused(False)
        app_state.checkpoint_mgr.mark_session_running()
        return _build_simulation_status()

    session_state = app_state.checkpoint_mgr.load_session_state()
    _set_engine_paused(False)

    if session_state and session_state.get("simulation_status") in {"running", "paused"}:
        app_state.checkpoint_mgr.mark_session_running()
        app_state._simulation_task = asyncio.create_task(
            resume_simulation(app_state.engine, app_state.event_bus, app_state.registry, app_state.storage_manager, app_state.case_fsm, app_state.checkpoint_mgr)
        )
    else:
        app_state.checkpoint_mgr.create_new_session()
        app_state._simulation_task = asyncio.create_task(
            run_simulation(app_state.engine, app_state.event_bus, app_state.registry, app_state.storage_manager, app_state.case_fsm, app_state.checkpoint_mgr)
        )
    app_state._simulation_task.add_done_callback(_log_task_result)
    return _build_simulation_status()
# ── 模拟流程 ──

def _log_task_result(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        return
    if exc is not None:
        logger.error("Simulation task crashed: %s", exc, exc_info=exc)


def _find_case_client_agent(sim_registry, storage, case_id: str, party_role: str):
    for client in sim_registry.get_agents_by_type("client"):
        if not client.config_path:
            continue
        try:
            config = storage.load_agent_config(client.config_path)
        except Exception:
            continue
        if f"case_{config.get('case_id', '')}" != case_id:
            continue
        if config.get("party_role", "plaintiff") != party_role:
            continue
        return client, client.config_path, config
    return None, "", {}




def _get_available_firms(sim_registry) -> list[str]:
    firms = [str(firm_id) for firm_id in sim_registry._firms.keys() if str(firm_id)]
    return firms or ["law_firm_A", "law_firm_B"]


def _resolve_map_prefix_from_firm(firm_id: str) -> str:
    key = str(firm_id or "").strip().lower()
    if key in {"law_firm_b", "lawfirmb"}:
        return "lawfirmB"
    return "lawfirmA"


def _resolve_birth_location_for_firm(firm_id: str) -> str:
    return "birth_locationB" if _resolve_map_prefix_from_firm(firm_id).lower().endswith("b") else "birth_locationA"


def _find_case_party_client(
    sim_registry: AgentRegistry,
    storage: FileStorageManager,
    case_id: str,
    party_role: str,
):
    normalized_case_id = str(case_id or "").removeprefix("case_")
    target_role = str(party_role or "").strip().lower()
    if not normalized_case_id or not target_role:
        return None

    for client in sim_registry.get_agents_by_type("client"):
        config_path = getattr(client, "config_path", None)
        if not config_path:
            continue

        try:
            config = storage.load_agent_config(config_path)
        except Exception:
            continue

        if str(config.get("case_id", "") or "").strip() != normalized_case_id:
            continue
        if str(config.get("party_role", "") or "").strip().lower() != target_role:
            continue
        return client

    return None


def _get_or_assign_character_name(agent, storage: FileStorageManager | None = None) -> str:
    configured = str(getattr(agent, "character_name", "") or "").strip()
    if configured:
        return configured

    config_path = getattr(agent, "config_path", None)
    if storage and config_path:
        try:
            config = storage.load_agent_config(config_path)
            configured = str(config.get("character_name", "") or "").strip()
            if configured:
                setattr(agent, "character_name", configured)
                return configured
        except Exception:
            pass

    configured = random.choice(app_state.CHARACTER_POOL)
    setattr(agent, "character_name", configured)
    if storage and config_path:
        try:
            storage.update_agent_field(config_path, "character_name", configured)
        except Exception as exc:
            logger.debug("Failed to persist character name for %s: %s", getattr(agent, "agent_id", ""), exc)
    return configured


def _choose_initial_target_firm(sim_registry, case_state: str, config: dict) -> str:
    firms = _get_available_firms(sim_registry)
    preferred_firm = str(config.get("assigned_firm", "") or "").strip()
    if preferred_firm in firms:
        return preferred_firm

    if case_state in ("空闲", "等待前台接待"):
        return random.choice(firms)

    return firms[0]


async def _launch_plaintiff_case(
    *,
    client,
    config: dict,
    case_state: str,
    sim_engine: WebSocketFrontendEngine,
    sim_event_bus: EventBus,
    sim_registry: AgentRegistry,
    storage: FileStorageManager,
) -> None:
    case_id = f"case_{config.get('case_id', '1')}"
    target_firm = _choose_initial_target_firm(sim_registry, case_state, config)
    map_prefix = _resolve_map_prefix_from_firm(target_firm)
    birth_loc_id = _resolve_birth_location_for_firm(target_firm)

    if client.config_path:
        try:
            storage.update_agent_field(client.config_path, "assigned_firm", target_firm)
            config["assigned_firm"] = target_firm
        except Exception as exc:
            logger.warning("Failed to persist assigned firm for %s: %s", case_id, exc)

    char_name = _get_or_assign_character_name(client, storage)

    await sim_engine.spawn_agent(
        agent_id=client.agent_id,
        name=client.name,
        character_name=char_name,
        birth_loc_id=birth_loc_id,
        role="plaintiff",
    )

    if case_state in ("空闲", "等待前台接待"):
        await sim_event_bus.publish(EventType.PLAINTIFF_ARRIVED, {
            "client_id": client.agent_id,
            "case_id": case_id,
            "target_firm": target_firm,
            "map_prefix": map_prefix,
            "party_role": "plaintiff",
            "client_path": client.config_path,
        })
        return

    if case_state == "委托洽谈中":
        logger.info("案件 %s 状态为 '委托洽谈中'，恢复委托洽谈流程...", case_id)
        lawyer_id = config.get("assigned_lawyer_id")
        if not lawyer_id:
            lawyers = sim_registry.get_agents_by_type("lawyer")
            lawyer_id = lawyers[0].agent_id if lawyers else ""
            logger.info("未找到 assigned_lawyer_id，使用默认律师 %s", lawyer_id)

        if lawyer_id:
            lawyer = sim_registry.get_agent(lawyer_id)
            if lawyer and lawyer.config_path:
                try:
                    storage.update_agent_field(lawyer.config_path, "current_handling_case", None)
                    logger.info("已清空律师 %s 的当前案件，准备恢复委托洽谈", lawyer_id)
                except Exception as exc:
                    logger.error("清空律师状态失败: %s", exc)

        await sim_event_bus.publish(EventType.CASE_ASSIGNED, {
            "client_id": client.agent_id,
            "case_id": case_id,
            "target_firm": target_firm,
            "map_prefix": map_prefix,
            "party_role": "plaintiff",
            "client_path": client.config_path,
            "lawyer_id": lawyer_id,
        })


async def run_simulation(
    sim_engine: WebSocketFrontendEngine,
    sim_event_bus: EventBus,
    sim_registry: AgentRegistry,
    storage: FileStorageManager,
    fsm: CaseStateMachine,
    checkpoint_mgr: CheckpointManager,
    *,
    selected_case_id: str = "",
):
    """运行法律全流程模拟（与 sandbox_main.py 逻辑一致）。"""
    normalized_selected_case_id = _normalize_case_identifier(selected_case_id)

    # 等待前端连接（最多 60 秒，超时则以 fallback 模式运行）
    logger.info("Waiting for frontend connection...")
    for _ in range(120):
        if not sim_engine._fallback_mode:
            break
        await _sleep_respecting_pause(sim_engine, 0.5)

    if sim_engine._fallback_mode:
        logger.info("No frontend connected, running in fallback (mock) mode")
    else:
        logger.info("Frontend connected, running in real-time mode")

    # 重置状态 & 统计活跃案件
    active_cases = []
    for client in sim_registry.get_agents_by_type("client"):
        if not client.config_path:
            continue
        config = storage.load_agent_config(client.config_path)
        stored_case_state = config.get("case_state", "空闲")
        case_state = normalize_case_state(stored_case_state)
        if case_state != stored_case_state:
            storage.update_agent_field(client.config_path, "case_state", case_state)
            config["case_state"] = case_state
        case_id = _normalize_case_identifier(config.get("case_id"))
        if normalized_selected_case_id and case_id != normalized_selected_case_id:
            continue
        if config.get("party_role") == "plaintiff" and case_state != "已结案":
            active_cases.append((client, config, case_state))

    if normalized_selected_case_id and not active_cases:
        logger.warning("未找到可启动的案件 %s，本轮模拟直接结束", normalized_selected_case_id)
        if checkpoint_mgr:
            checkpoint_mgr.mark_session_completed()
        return

    # Reset lawyer queues
    for lawyer in sim_registry.get_agents_by_type("lawyer"):
        if not lawyer.config_path:
            continue
        try:
            storage.update_agent_field(lawyer.config_path, "current_handling_case", None)
            storage.update_agent_field(lawyer.config_path, "case_queue", [])
        except FileNotFoundError:
            pass

    sim_event_bus.set_expected_cases(len(active_cases))

    logger.info("=" * 60)
    logger.info("法律AI小镇模拟启动")
    logger.info("发现 %d 个活跃案件", len(active_cases))
    logger.info("=" * 60)

    idle_case_entries = [
        (client, config, case_state)
        for client, config, case_state in active_cases
        if case_state in ("空闲", "等待前台接待")
    ]
    resumed_case_entries = [
        (client, config, case_state)
        for client, config, case_state in active_cases
        if case_state not in ("空闲", "等待前台接待")
    ]

    launch_requests: list[_CaseLaunchRequest] = []

    for client, config, case_state in resumed_case_entries:
        case_id = f"case_{config.get('case_id', '1')}"

        async def _launch_resumed_case(
            client=client,
            config=config,
            case_state=case_state,
        ) -> None:
            await _launch_plaintiff_case(
                client=client,
                config=config,
                case_state=case_state,
                sim_engine=sim_engine,
                sim_event_bus=sim_event_bus,
                sim_registry=sim_registry,
                storage=storage,
            )

        launch_requests.append(_CaseLaunchRequest(case_id=case_id, launch=_launch_resumed_case))

    for index, (client, config, case_state) in enumerate(idle_case_entries):
        case_id = f"case_{config.get('case_id', '1')}"
        post_launch_delay = app_state.CASE_SPAWN_INTERVAL_SECONDS if index < len(idle_case_entries) - 1 else 0.0

        async def _launch_idle_case(
            client=client,
            config=config,
            case_state=case_state,
        ) -> None:
            await _launch_plaintiff_case(
                client=client,
                config=config,
                case_state=case_state,
                sim_engine=sim_engine,
                sim_event_bus=sim_event_bus,
                sim_registry=sim_registry,
                storage=storage,
            )

        launch_requests.append(
            _CaseLaunchRequest(
                case_id=case_id,
                launch=_launch_idle_case,
                post_launch_delay=post_launch_delay,
            )
        )

    await _dispatch_case_launch_requests(
        requests=launch_requests,
        sim_engine=sim_engine,
        sim_event_bus=sim_event_bus,
    )

    # 等待所有案件结案（带防卡死检查）
    await sim_event_bus.spin_until_all_closed(
        storage_manager=storage,
        agent_registry=sim_registry,
        check_interval=15.0,
    )

    # 标记会话完成
    if checkpoint_mgr:
        checkpoint_mgr.mark_session_completed()

    logger.info("=" * 60)
    logger.info("模拟结束")
    logger.info("=" * 60)


async def resume_simulation(
    sim_engine: WebSocketFrontendEngine,
    sim_event_bus: EventBus,
    sim_registry: AgentRegistry,
    storage: FileStorageManager,
    fsm: CaseStateMachine,
    checkpoint_mgr: CheckpointManager,
    *,
    selected_case_id: str = "",
):
    """从检查点恢复模拟，扫描沙盒数据恢复所有未结案案件。"""
    normalized_selected_case_id = _normalize_case_identifier(selected_case_id)

    logger.info("=" * 60)
    logger.info("从检查点恢复模拟")
    logger.info("=" * 60)

    # 等待前端连接
    logger.info("Waiting for frontend connection...")
    for _ in range(120):
        if not sim_engine._fallback_mode:
            break
        await _sleep_respecting_pause(sim_engine, 0.5)

    if sim_engine._fallback_mode:
        logger.info("No frontend connected, running in fallback (mock) mode")
    else:
        logger.info("Frontend connected, running in real-time mode")

    # 加载会话状态
    session_state = checkpoint_mgr.load_session_state()
    if not session_state:
        logger.warning("无法加载会话状态，启动新模拟")
        return await run_simulation(
            sim_engine,
            sim_event_bus,
            sim_registry,
            storage,
            fsm,
            checkpoint_mgr,
            selected_case_id=normalized_selected_case_id,
        )

    # 恢复活跃场景状态到 EventBus
    active_scenario_details = session_state.get("active_scenario_details", {})
    if active_scenario_details:
        if max(int(app_state.MAX_CONCURRENT_CASES or 0), 0) == 1:
            logger.info(
                "串行模式启用，跳过预恢复 %d 个活跃场景，改由案件调度顺序恢复",
                len(active_scenario_details),
            )
        else:
            logger.info(f"从检查点恢复 {len(active_scenario_details)} 个活跃场景状态")
            sim_event_bus.restore_active_scenarios(active_scenario_details)
    else:
        logger.info("检查点中没有活跃场景详情，可能是旧版本检查点")

    # 扫描沙盒数据，收集所有未结案的案件
    logger.info("扫描沙盒数据，收集所有未结案案件...")
    active_cases = []
    active_case_ids = set()  # 用于去重，按案件 ID 统计

    clients = sim_registry.get_agents_by_type("client")
    logger.info(f"找到 {len(clients)} 个当事人 Agent")

    for client in clients:
        logger.info(f"检查当事人: {client.name} (ID: {client.agent_id})")
        if not client.config_path:
            logger.info("  跳过: 没有 config_path")
            continue

        try:
            config = storage.load_agent_config(client.config_path)
            if not isinstance(config, dict):
                logger.warning(f"  Invalid config shape, skipping resume: {client.config_path}")
                continue

            case_state = infer_case_state_from_artifacts(storage.base_dir, config)
            if case_state != config.get("case_state", "空闲"):
                storage.update_agent_field(client.config_path, "case_state", case_state)
                config["case_state"] = case_state
            normalized_case_id = _normalize_case_identifier(config.get("case_id"))
            if normalized_selected_case_id and normalized_case_id != normalized_selected_case_id:
                logger.info("  跳过: 不在本轮选定案件内 (%s)", normalized_selected_case_id)
                continue
            party_role = config.get("party_role", "plaintiff")  # 从配置中读取 party_role
            map_state = config.get("map_state") or {}
            is_seated = map_state.get("sitting") is not None
            logger.info(f"  状态: {case_state}, 角色: {party_role}, 就座: {is_seated}")

            # 将所有非空闲且非结案的当事人都视为活跃（无论原告被告）
            # 或者如果是原告且状态为空闲（代表尚未开始的新案件）
            # 或者已经在座位上（代表可能处于咨询中，即使状态被错误复位）
            should_resume = (case_state not in ["空闲", "已结案"]) or (party_role == "plaintiff" and case_state == "空闲") or is_seated

            if should_resume:
                active_cases.append((client, config, case_state))
                case_id = config.get('case_id', '')
                if case_id:
                    active_case_ids.add(f"case_{case_id}")
                logger.info(f"  ✓ 发现未结案案件角色: case_id={case_id}, role={party_role}, state={case_state}, client={client.name}")
            else:
                logger.info("  跳过: 不需要恢复")
        except Exception as e:
            logger.error(f"  加载配置失败: {e}", exc_info=True)
            continue

    # 关键修复：按案件数而不是角色数统计
    num_cases = len(active_case_ids)
    sim_event_bus.set_expected_cases(num_cases)
    logger.info(f"共发现 {len(active_cases)} 个未结案角色，对应 {num_cases} 个案件")
    if normalized_selected_case_id and num_cases == 0:
        logger.warning("未找到可恢复的案件 %s，本轮恢复直接结束", normalized_selected_case_id)
        checkpoint_mgr.mark_session_completed()
        return

    # 获取检查点中未完成的场景，并先清理缺文件的脏检查点
    raw_incomplete_scenarios = checkpoint_mgr.get_incomplete_scenarios()
    incomplete_scenarios: list[dict] = []
    incomplete_case_ids: set[str] = set()
    for scenario_info in raw_incomplete_scenarios:
        scenario_id = scenario_info["scenario_id"]
        checkpoint_file = scenario_info["checkpoint_file"]
        checkpoint_data = checkpoint_mgr.load_scenario_checkpoint(checkpoint_file)
        if not checkpoint_data:
            checkpoint_data = _build_player_lawyer_lc_checkpoint_from_request(storage, scenario_info)
            if not checkpoint_data:
                logger.warning(
                    "检查点场景 %s 缺少可用检查点文件 %s，按脏检查点清理并回落到案件状态恢复",
                    scenario_id,
                    checkpoint_file,
                )
                checkpoint_mgr.mark_scenario_completed(scenario_id)
                continue

        enriched_scenario_info = dict(scenario_info)
        enriched_scenario_info["_checkpoint_data"] = checkpoint_data
        checkpoint_case_id = str(enriched_scenario_info.get("case_id", "") or "")
        if normalized_selected_case_id and _normalize_case_identifier(checkpoint_case_id) != normalized_selected_case_id:
            continue
        incomplete_scenarios.append(enriched_scenario_info)
        if checkpoint_case_id:
            incomplete_case_ids.add(checkpoint_case_id)

    logger.info(f"检查点中有 {len(incomplete_scenarios)} 个未完成场景: {incomplete_case_ids}")


    launch_requests: list[_CaseLaunchRequest] = []

    # 1. 先恢复检查点中未完成的场景
    for scenario_info in incomplete_scenarios:
        scenario_id = scenario_info["scenario_id"]
        checkpoint_file = scenario_info["checkpoint_file"]
        party_role = scenario_info.get("party_role", "plaintiff")
        checkpoint_case_id = str(scenario_info.get("case_id", "") or "")
        checkpoint_data = scenario_info["_checkpoint_data"]
        if not checkpoint_case_id:
            logger.warning("检查点场景 %s 缺少 case_id，跳过", scenario_id)
            continue

        async def _resume_incomplete_scenario(
            scenario_id=scenario_id,
            checkpoint_file=checkpoint_file,
            party_role=party_role,
            checkpoint_case_id=checkpoint_case_id,
            checkpoint_data=checkpoint_data,
        ) -> None:
            logger.info(f"恢复检查点场景: {scenario_id}")

            case_id = _normalize_case_identifier(checkpoint_data.get("case_id", "") or checkpoint_case_id)
            client_id = checkpoint_data.get("client_id") or scenario_info.get("client_id")
            lawyer_id = checkpoint_data.get("lawyer_id") or scenario_info.get("lawyer_id")

            client = sim_registry.get_agent(client_id)
            lawyer = sim_registry.get_agent(lawyer_id)

            if not client or not lawyer:
                logger.error(f"无法找到 agent: client={client_id}, lawyer={lawyer_id}")
                return False

            if client_id not in sim_engine._agent_states:
                birth_loc_id = "birth_locationB" if party_role == "defendant" else "birth_locationA"
                await sim_engine.spawn_agent(
                    agent_id=client_id,
                    name=client.name,
                    character_name=_get_or_assign_character_name(client, storage),
                    birth_loc_id=birth_loc_id,
                    role=party_role,
                )

            if lawyer_id not in sim_engine._agent_states:
                lawyer_birth = "birth_locationB" if getattr(lawyer, "firm_id", "") == "law_firm_B" else "birth_locationA"
                await sim_engine.spawn_agent(
                    agent_id=lawyer_id,
                    name=lawyer.name,
                    character_name=_get_or_assign_character_name(lawyer, storage),
                    birth_loc_id=lawyer_birth,
                    role="lawyer",
                )

            data_loader, case, client_config = _load_case_data_for_resume(client.config_path, storage)
            scenario_data = checkpoint_data.get("scenario_data", {})
            extracted_profile = (
                data_loader.extract_plaintiff_profile(case)
                if party_role == "plaintiff"
                else data_loader.extract_defendant_profile(case)
            )

            lawyer_scenario = PromptAssembler.build_scenario_prompt("lawyer", "LC", scenario_data)
            lawyer_config = storage.load_agent_config(lawyer.config_path) if lawyer.config_path else {}
            lawyer_prompt = PromptAssembler.build(
                profile={"name": lawyer.name, "law_firm": lawyer.law_firm, "specialty": lawyer.specialty_areas},
                scenario_prompt=lawyer_scenario,
            )

            client_scenario = PromptAssembler.build_scenario_prompt("client", "LC", scenario_data)
            client_prompt = PromptAssembler.build(
                profile=ScenarioOrchestrator._build_client_prompt_profile(client, extracted_profile),
                scenario_prompt=client_scenario,
            )

            lawyer.activate(lawyer_prompt)
            client.activate(client_prompt)

            try:
                display_stage_code = ScenarioOrchestrator._consultation_display_stage_code(party_role)
                sim_event_bus.register_active_scenario(
                    case_id=case_id,
                    scenario_type="LC",
                    participant_ids=[client_id, lawyer_id],
                )

                output_path = str(Path(storage.base_dir) / "output" / case_id / f"{display_stage_code}_result.json")
                scenario = LegalConsultationScenario(
                    client_agent=client,
                    lawyer_agent=lawyer,
                    max_turns=ScenarioOrchestrator._resolve_lc_max_turns(
                        len(extracted_profile.get("questions") or []),
                        player_lawyer_enabled=_player_lawyer_mode_for_engine(sim_engine) == "plaintiff",
                    ),
                    output_path=output_path,
                    verbose=app_state.SCENARIO_VERBOSE,
                    map_engine=sim_engine,
                    checkpoint_manager=checkpoint_mgr,
                    scenario_id=scenario_id,
                    trace_stage_code=display_stage_code,
                    trace_stage_key=f"{display_stage_code}_{party_role}".upper(),
                )

                result = await scenario.resume_from_checkpoint(checkpoint_data)

                output_dir = Path(storage.base_dir) / "output" / case_id
                output_dir.mkdir(parents=True, exist_ok=True)
                result_file = output_dir / f"{display_stage_code}_result.json"
                with open(result_file, 'w', encoding='utf-8') as f:
                    import json
                    json.dump(result, f, ensure_ascii=False, indent=2)
                if display_stage_code == "PLC":
                    compat_file = output_dir / "LC_result.json"
                    with open(compat_file, 'w', encoding='utf-8') as f:
                        import json
                        json.dump(result, f, ensure_ascii=False, indent=2)

                checkpoint_mgr.mark_scenario_completed(scenario_id)
                sim_event_bus.unregister_active_scenario(case_id)
                checkpoint_mgr.sync_active_scenarios_from_event_bus()

                completion_event = EventType.PLAINTIFF_CONSULTATION_COMPLETED
                await sim_event_bus.publish(completion_event, {
                    "case_id": case_id,
                    "client_path": client.config_path,
                    "client_id": client.agent_id,
                    "lawyer_id": lawyer.agent_id,
                    "party_role": party_role or "plaintiff",
                    "firm_id": getattr(lawyer, "firm_id", "law_firm_A"),
                })
                return True

            except Exception as e:
                logger.error(f"恢复场景失败: {e}", exc_info=True)
                sim_event_bus.unregister_active_scenario(case_id)
                checkpoint_mgr.sync_active_scenarios_from_event_bus()
                lawyer.recover_from_error()
                client.recover_from_error()
                return False
            finally:
                if lawyer.is_active:
                    lawyer.deactivate()
                if client.is_active:
                    client.deactivate()

        launch_requests.append(
            _CaseLaunchRequest(case_id=checkpoint_case_id, launch=_resume_incomplete_scenario)
        )

    # 2. 启动检查点中没有的案件（根据状态恢复到相应流程）
    # 状态到恢复事件的映射（纯刑事）
    from src.core.event_bus import EventType
    STATE_TO_EVENT_MAP = {
        "空闲": EventType.PLAINTIFF_ARRIVED,
        "等待前台接待": EventType.PLAINTIFF_ARRIVED,
        "委托洽谈中": EventType.CASE_ASSIGNED,
        # ── 刑事流程恢复 ──
        "侦查阶段": EventType.INVESTIGATION_STARTED,
        "审查起诉阶段": EventType.PROSECUTION_REVIEW_STARTED,
        "辩护词起草中": EventType.ENTER_DEFENSE_OPINION_DRAFTING,
        "辩护词已递交": EventType.ENTER_CRIMINAL_TRIAL,
        "起诉书已递交": EventType.ENTER_CRIMINAL_TRIAL,
        "等待刑事一审开庭": EventType.ENTER_CRIMINAL_TRIAL,
        "刑事一审庭审中": EventType.ENTER_CRIMINAL_TRIAL,
        "刑事一审判决": EventType.CRIMINAL_TRIAL_COMPLETED,
        "刑事上诉决策中": EventType.ENTER_CRIMINAL_APPEAL_TRIAL,
        "刑事上诉状起草中": EventType.ENTER_CRIMINAL_APPEAL_TRIAL,
        "刑事上诉状已递交": EventType.ENTER_CRIMINAL_APPEAL_TRIAL,
        "等待刑事二审开庭": EventType.ENTER_CRIMINAL_APPEAL_TRIAL,
        "刑事二审庭审中": EventType.ENTER_CRIMINAL_APPEAL_TRIAL,
        "刑事终审判决": EventType.CRIMINAL_FINAL_VERDICT_ISSUED,
    }

    shared_case_resumed = set()
    for client, config, case_state in active_cases:
        case_id = f"case_{config.get('case_id', '1')}"
        party_role = config.get("party_role", "plaintiff")
        map_state = config.get("map_state") or {}
        is_seated = map_state.get("sitting") is not None

        # 如果已经在检查点中恢复过，跳过
        if case_id in incomplete_case_ids:
            logger.info(f"案件 {case_id} 已从检查点恢复，跳过")
            continue

        if case_state == "空闲" and party_role == "defendant" and is_seated:
            case_state = "委托洽谈中"
            logger.info(f"案件 {case_id} ({party_role}) 启发式修补状态: 空闲 -> {case_state}")

        # 共享阶段修复：如果同案另一方已经进入刑事共享阶段，则当前角色直接对齐。
        shared_resume_states = [
            "侦查阶段", "审查起诉阶段", "辩护词起草中", "辩护词已递交", "起诉书已递交",
            "等待刑事一审开庭", "刑事一审庭审中", "刑事一审判决", "刑事上诉决策中",
            "等待刑事二审开庭", "刑事二审庭审中", "刑事终审判决",
        ]
        counterpart_role = "defendant" if party_role == "plaintiff" else "plaintiff"
        counterpart_path = Path(client.config_path).parent / counterpart_role / "config.yaml"
        if counterpart_path.exists():
            try:
                counterpart_conf = storage.load_yaml(counterpart_path)
                counterpart_state = counterpart_conf.get("case_state", "空闲")
                if counterpart_state in shared_resume_states and case_state not in shared_resume_states:
                    case_state = counterpart_state
                    logger.info(
                        f"案件 {case_id} ({party_role}) 共享阶段修复: 对齐另一方状态 -> {case_state}"
                    )
            except Exception as e:
                logger.warning(f"案件 {case_id} 共享阶段修复失败: {e}")

        # 获取恢复事件
        if case_state in ["等待刑事一审开庭", "刑事一审庭审中"]:
            if case_id in shared_case_resumed:
                logger.info(f"Case {case_id} criminal first-instance resume already triggered, skip role {party_role}")
                continue
            shared_case_resumed.add(case_id)
            logger.info(
                f"Resume shared case: {case_id}, shared_state={case_state} "
                f"-> event={EventType.ENTER_CRIMINAL_TRIAL}"
            )
            await sim_event_bus.publish(EventType.ENTER_CRIMINAL_TRIAL, {
                "case_id": case_id,
                "client_path": str(Path(client.config_path).parent / "defendant"),
            })
            continue

        if case_state == "刑事一审判决":
            if case_id in shared_case_resumed:
                logger.info(f"Case {case_id} post-verdict resume already triggered, skip role {party_role}")
                continue
            shared_case_resumed.add(case_id)
            logger.info(
                f"Resume shared case: {case_id}, shared_state={case_state} "
                f"-> event={EventType.CRIMINAL_TRIAL_COMPLETED}"
            )
            await sim_event_bus.publish(EventType.CRIMINAL_TRIAL_COMPLETED, {
                "case_id": case_id,
                "client_path": str(Path(client.config_path).parent / "defendant"),
            })
            continue

        if case_state in ["刑事上诉决策中", "刑事上诉状起草中", "刑事上诉状已递交", "等待刑事二审开庭", "刑事二审庭审中"]:
            if case_id in shared_case_resumed:
                logger.info(f"Case {case_id} criminal second-instance resume already triggered, skip role {party_role}")
                continue
            shared_case_resumed.add(case_id)
            logger.info(
                f"Resume shared case: {case_id}, shared_state={case_state} "
                f"-> event={EventType.ENTER_CRIMINAL_APPEAL_TRIAL}"
            )
            await sim_event_bus.publish(EventType.ENTER_CRIMINAL_APPEAL_TRIAL, {
                "case_id": case_id,
                "client_path": str(Path(client.config_path).parent / "defendant"),
            })
            continue

        recovery_event = STATE_TO_EVENT_MAP.get(case_state)

        logger.info(f"恢复角色执行: {case_id}, client={client.name}, role={party_role}, state={case_state} -> event={recovery_event}")

        async def _resume_case_action(
            client=client,
            config=config,
            case_id=case_id,
            case_state=case_state,
            party_role=party_role,
            recovery_event=recovery_event,
        ) -> None:
            firms = list(sim_registry._firms.keys())
            target_firm = config.get("assigned_firm", firms[0] if firms else "law_firm_A")
            map_prefix = _resolve_map_prefix_from_firm(target_firm)
            birth_loc_id = _resolve_birth_location_for_firm(target_firm)
            char_name = _get_or_assign_character_name(client, storage)

            await sim_engine.spawn_agent(
                agent_id=client.agent_id,
                name=client.name,
                character_name=char_name,
                birth_loc_id=birth_loc_id,
                role=party_role,
            )

            if recovery_event == EventType.PLAINTIFF_ARRIVED:
                await sim_event_bus.publish(recovery_event, {
                    "client_id": client.agent_id,
                    "client_path": client.config_path,
                    "case_id": case_id,
                    "target_firm": target_firm,
                    "map_prefix": map_prefix,
                    "party_role": party_role,
                })
                return

            if recovery_event == EventType.CASE_ASSIGNED:
                lawyer_id = config.get("assigned_lawyer_id", "")
                if lawyer_id:
                    lawyer = sim_registry.get_agent(lawyer_id)
                    if lawyer and lawyer.config_path:
                        try:
                            storage.update_agent_field(lawyer.config_path, "current_handling_case", None)
                            logger.info(f"已清空律师 {lawyer_id} 的当前案件，准备恢复委托洽谈")
                        except Exception as e:
                            logger.error(f"清空律师状态失败: {e}")

                    await sim_event_bus.publish(recovery_event, {
                        "client_id": client.agent_id,
                        "client_path": client.config_path,
                        "case_id": case_id,
                        "lawyer_id": lawyer_id,
                        "target_firm": target_firm,
                        "map_prefix": map_prefix,
                        "party_role": party_role,
                    })
                else:
                    logger.warning(f"案件 {case_id} 没有分配律师，无法恢复委托洽谈")
                return

            if recovery_event:
                await sim_event_bus.publish(recovery_event, {
                    "case_id": case_id,
                    "client_path": client.config_path,
                    "client_id": client.agent_id,
                    "lawyer_id": config.get("assigned_lawyer_id", ""),
                    "party_role": party_role,
                    "firm_id": target_firm,
                    "target_firm": target_firm,
                    "map_prefix": map_prefix,
                })
            else:
                logger.info(f"案件 {case_id} ({party_role}) 状态 {case_state} 无需恢复事件，跳过")

        launch_requests.append(_CaseLaunchRequest(case_id=case_id, launch=_resume_case_action))

    await _dispatch_case_launch_requests(
        requests=launch_requests,
        sim_engine=sim_engine,
        sim_event_bus=sim_event_bus,
    )

    # 等待所有案件结案（带防卡死检查）
    await sim_event_bus.spin_until_all_closed(
        storage_manager=storage,
        agent_registry=sim_registry,
        check_interval=15.0,
    )

    # 标记会话完成
    checkpoint_mgr.mark_session_completed()

    logger.info("=" * 60)
    logger.info("模拟恢复完成")
    logger.info("=" * 60)


def _build_player_lawyer_lc_checkpoint_from_request(
    storage: FileStorageManager,
    scenario_info: dict[str, Any],
) -> dict[str, Any] | None:
    """Build a minimal LC checkpoint from a persisted player-lawyer turn."""
    if str(scenario_info.get("scenario_type") or "").upper() != "LC":
        return None
    case_id = str(scenario_info.get("case_id") or "").strip()
    if not case_id:
        return None

    request_dir = Path(storage.base_dir) / "output" / case_id / "_player_lawyer"
    if not request_dir.exists():
        return None

    candidates: list[dict[str, Any]] = []
    for path in sorted(request_dir.glob("*.json")):
        try:
            with open(path, encoding="utf-8") as fh:
                payload = json.load(fh)
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        if str(payload.get("case_id") or "") != case_id:
            continue
        if str(payload.get("stage") or "").upper() != "LC":
            continue
        if str(payload.get("status") or "") not in {"pending", "submitted"}:
            continue
        if not str(payload.get("prompt") or "").strip():
            continue
        candidates.append(payload)

    if not candidates:
        return None

    candidates.sort(key=lambda item: str(item.get("resolved_at") or item.get("created_at") or ""))
    request_payload = candidates[-1]
    created_at = str(request_payload.get("created_at") or datetime.now().isoformat())
    logger.info(
        "使用玩家输入请求恢复 LC 检查点: scenario=%s request=%s status=%s",
        scenario_info.get("scenario_id"),
        request_payload.get("request_id"),
        request_payload.get("status"),
    )
    return {
        "scenario_type": "LC",
        "case_id": case_id,
        "client_id": scenario_info.get("client_id"),
        "lawyer_id": scenario_info.get("lawyer_id"),
        "dialog_history": [
            {
                "turn": 0,
                "role": "client",
                "content": str(request_payload.get("prompt") or ""),
                "timestamp": created_at,
            }
        ],
        "turn_count": 0,
        "completed": False,
        "finish_reason": "max_turns",
    }


def _load_case_data_for_resume(client_config_path: str, storage):
    """加载案件数据用于恢复。"""

    config = storage.load_agent_config(client_config_path)
    dataset_path = config.get("dataset_path", "")

    data_loader = DataLoader(dataset_path)
    case = data_loader.resolve_case_for_config(config)
    return data_loader, case, config
