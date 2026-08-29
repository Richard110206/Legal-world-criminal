"""Player-lawyer gateway lifecycle & request-scoped providers."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request

from src.core.auth import AuthError
from src.core.database import get_db_session
from src.core.event_bus import EventType
from src.core.models import Sandbox
from src.core.sandbox_manager import SandboxRuntimeContext
from src.core.user_service import (
    UserNotFoundError,
)
from src.player_lawyer.agent import is_player_defendant_mode as _is_player_defendant_mode
from src.player_lawyer.input_gateway import PlayerInputGateway as _PlayerInputGateway
from src.player_lawyer.routes import (
    set_gateway_provider as _set_player_gw_provider,
)
from src.player_lawyer.routes import (
    set_response_assist_provider as _set_player_response_assist_provider,
)
from src.player_lawyer.routes import (
    set_status_provider as _set_player_status_provider,
)
from src.utils.runtime_flags import player_lawyer_mode_for_frontend

from .agent_status import _get_sandbox_manager
from .app_state import _get_session_factory
from .case_catalog import _normalize_case_identifier
from .deps import (
    _extract_bearer_token,
    _get_user_from_access_token,
    _require_user_sandbox,
)

# Per-sandbox gateway instances, keyed by sandbox_id
_player_gateways: dict[str, _PlayerInputGateway] = {}

logger = logging.getLogger("ws_server")


def _player_lawyer_mode_for_engine(runtime_engine: Any | None) -> str:
    frontend_mode = getattr(runtime_engine, "_frontend_mode", None)
    supports_player_v2 = False
    supports_fn = getattr(runtime_engine, "supports_player_v2_runtime", None)
    if callable(supports_fn):
        supports_player_v2 = bool(supports_fn())
    return player_lawyer_mode_for_frontend(
        frontend_mode=frontend_mode,
        has_player_v2_client=supports_player_v2,
    )


def _player_lawyer_mode_for_context(context: Any | None) -> str:
    return _player_lawyer_mode_for_engine(getattr(context, "engine", None))


def _player_lawyer_status_for_request(request: Request) -> dict[str, Any]:
    if not _is_player_defendant_mode():
        return {"player_mode": "off", "enabled": False}

    token = _extract_bearer_token(request.headers.get("authorization"))
    try:
        with get_db_session(_get_session_factory()) as session:
            current_user = _get_user_from_access_token(token, session)
            sandbox = _require_user_sandbox(session, current_user)
    except (AuthError, UserNotFoundError):
        return {"player_mode": "off", "enabled": False}

    context = _get_sandbox_manager().get_or_create_context(sandbox)
    mode = _player_lawyer_mode_for_context(context)
    return {"player_mode": mode or "off", "enabled": mode == "defendant"}


def _get_player_gateway_for_request(request: Request) -> _PlayerInputGateway:
    """Resolve the player gateway for the authenticated user's sandbox."""
    token = _extract_bearer_token(request.headers.get("authorization"))
    try:
        with get_db_session(_get_session_factory()) as session:
            current_user = _get_user_from_access_token(token, session)
            sandbox = _require_user_sandbox(session, current_user)
    except (AuthError, UserNotFoundError) as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    context = _get_sandbox_manager().get_or_create_context(sandbox)
    if _player_lawyer_mode_for_context(context) != "defendant":
        raise HTTPException(status_code=403, detail="Player-lawyer mode is not enabled")

    gateway = getattr(context, "player_gateway", None)
    if gateway is None:
        gateway = get_or_create_player_gateway(sandbox.id, Path(sandbox.storage_root))
    orchestrator = getattr(context, "orchestrator", None)
    if orchestrator is not None:
        setattr(orchestrator, "_player_gateway", gateway)
        setattr(orchestrator, "_sandbox_id", sandbox.id)
        setattr(orchestrator, "_teaching_student_id", str(sandbox.user_id))
    return gateway


def _get_player_response_assist_for_request(request: Request):
    """Resolve the response assist service for the authenticated user's sandbox."""
    token = _extract_bearer_token(request.headers.get("authorization"))
    try:
        with get_db_session(_get_session_factory()) as session:
            current_user = _get_user_from_access_token(token, session)
            sandbox = _require_user_sandbox(session, current_user)
    except (AuthError, UserNotFoundError) as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    _ensure_player_lawyer_runtime(sandbox)
    from src.player_lawyer.response_assist import PlayerResponseAssistService

    return PlayerResponseAssistService(storage_root=Path(sandbox.storage_root))


async def _publish_player_document_completion_if_unmanaged(
    context: SandboxRuntimeContext,
    draft: Any,
) -> bool:
    """Publish the document completion event when no live scenario task can do it."""

    completion_by_document_type = {
        "defense_opinion": (EventType.DEFENSE_OPINION_DRAFTING_COMPLETED, "DS", "defendant"),
    }
    event_info = completion_by_document_type.get(str(getattr(draft, "document_type", "") or "").strip())
    if not event_info:
        return False

    event_type, scenario_type, preferred_party_role = event_info
    case_id = _normalize_case_identifier(getattr(draft, "case_id", ""))
    if not case_id:
        return False

    event_bus = getattr(context, "event_bus", None)
    storage = getattr(context, "storage_manager", None)
    if event_bus is None or storage is None:
        return False

    active_snapshot = {}
    get_snapshot = getattr(event_bus, "get_active_scenarios_snapshot", None)
    if callable(get_snapshot):
        active_snapshot = get_snapshot() or {}
    active_scenario = active_snapshot.get(case_id) or active_snapshot.get(case_id.removeprefix("case_"))
    task = getattr(context, "simulation_task", None)
    task_running = bool(task is not None and not task.done())
    if task_running and str((active_scenario or {}).get("scenario_type") or "").upper() == scenario_type:
        return False

    client_path = storage.get_case_agent_path(case_id, preferred_party_role)
    if not (client_path / "config.yaml").exists() and preferred_party_role == "defendant":
        client_path = storage.get_case_agent_path(case_id, "plaintiff")
    if not (client_path / "config.yaml").exists():
        logger.warning("无法补发文书完成事件，缺少当事人配置: case=%s role=%s", case_id, preferred_party_role)
        return False

    config = storage.load_agent_config(client_path)
    await event_bus.publish(event_type, {
        "case_id": case_id,
        "client_path": str(client_path),
        "client_id": f"{case_id}_{config.get('party_role', preferred_party_role)}",
        "lawyer_id": config.get("assigned_lawyer_id", ""),
        "party_role": config.get("party_role", preferred_party_role),
        "firm_id": config.get("assigned_firm", ""),
    })
    return True


def get_or_create_player_gateway(sandbox_id: str | int, storage_root=None) -> _PlayerInputGateway:
    """Get or lazily create a player gateway for a specific sandbox."""
    key = str(sandbox_id)
    if key not in _player_gateways:
        from src.player_lawyer.input_gateway import make_json_persister, restore_json_requests
        from src.player_lawyer.run_ledger import PlayerRunLedger

        output_root = Path(storage_root) / "output" if storage_root else None
        ledger = PlayerRunLedger(storage_root=Path(storage_root)) if storage_root else None
        persist_fn = make_json_persister(output_root, ledger=ledger) if output_root else None
        gateway = _PlayerInputGateway(
            sandbox_id=int(sandbox_id) if str(sandbox_id).isdigit() else 0,
            persist_fn=persist_fn,
            ledger=ledger,
            storage_root=Path(storage_root) if storage_root else None,
        )
        if output_root is not None:
            restored = restore_json_requests(gateway, output_root)
            if restored:
                logger.info(
                    "[PlayerGateway] Restored %d pending request(s) for sandbox %s",
                    restored,
                    sandbox_id,
                )
        _player_gateways[key] = gateway
    return _player_gateways[key]


def reset_player_gateway(sandbox_id: str | int) -> int:
    """Cancel and drop the in-memory player gateway for a sandbox reset."""
    gateway = _player_gateways.pop(str(sandbox_id), None)
    if gateway is None:
        return 0
    try:
        cancelled = gateway.cancel_all_pending()
    except Exception as exc:
        logger.warning("[PlayerGateway] Failed to reset gateway for sandbox %s: %s", sandbox_id, exc)
        return 0
    return len(cancelled)


_set_player_gw_provider(_get_player_gateway_for_request)
_set_player_status_provider(_player_lawyer_status_for_request)
_set_player_response_assist_provider(_get_player_response_assist_for_request)
def _ensure_player_lawyer_runtime(sandbox: Sandbox) -> tuple[SandboxRuntimeContext, _PlayerInputGateway]:
    context = _get_sandbox_manager().get_or_create_context(sandbox)
    if _player_lawyer_mode_for_context(context) != "defendant":
        raise HTTPException(status_code=403, detail="Player-lawyer mode is not enabled")

    gateway = getattr(context, "player_gateway", None)
    if gateway is None:
        gateway = get_or_create_player_gateway(sandbox.id, Path(sandbox.storage_root))
        setattr(context, "player_gateway", gateway)
    orchestrator = getattr(context, "orchestrator", None)
    if orchestrator is not None:
        setattr(orchestrator, "_player_gateway", gateway)
        setattr(orchestrator, "_sandbox_id", sandbox.id)
        setattr(orchestrator, "_teaching_student_id", str(sandbox.user_id))
    # 玩家输入未决时挂起对话 gate：防止「轮到你了」面板出现后案情继续自动推进
    engine = getattr(context, "engine", None)
    if engine is not None and callable(getattr(engine, "set_player_pending_check", None)):
        engine.set_player_pending_check(lambda: len(gateway.list_pending()) > 0)
    return context, gateway
