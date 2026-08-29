"""The /ws WebSocket endpoint (frontend engine bridge + player input)."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from src.core.auth import AuthError
from src.core.database import get_db_session
from src.core.user_service import UserNotFoundError

from .agent_status import _get_sandbox_manager
from .app_state import _get_session_factory
from .deps import (
    _get_user_from_access_token,
    _require_user_sandbox,
    _update_sandbox_from_runtime_status,
)
from .player_gateway_admin import (
    _player_lawyer_mode_for_context,
    get_or_create_player_gateway,
)
from .simulation_runtime import _broadcast_sandbox_event

logger = logging.getLogger("ws_server")
router = APIRouter()


# ── WebSocket 端点 ──

@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    token = str(websocket.query_params.get("token") or "").strip()
    if not token:
        auth_header = str(websocket.headers.get("authorization") or "").strip()
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
    if not token:
        await websocket.close(code=4401)
        return

    try:
        with get_db_session(_get_session_factory()) as session:
            current_user = _get_user_from_access_token(token, session)
            sandbox = _require_user_sandbox(session, current_user)
    except (AuthError, UserNotFoundError, HTTPException):
        await websocket.close(code=4401)
        return

    context = _get_sandbox_manager().get_or_create_context(sandbox)
    runtime_engine = getattr(context, "engine", None)

    await websocket.accept()
    context.connected_clients.add(websocket)
    add_client = getattr(runtime_engine, "add_client", None)
    if callable(add_client):
        await add_client(websocket)
    try:
        while True:
            data = await websocket.receive_json()
            if data.get("type") == "client_logout":
                runtime_status = _get_sandbox_manager().pause_sandbox(sandbox)
                try:
                    with get_db_session(_get_session_factory()) as session:
                        current_sandbox = _require_user_sandbox(session, current_user)
                        _update_sandbox_from_runtime_status(session, current_sandbox, runtime_status)
                except Exception as exc:
                    logger.warning("Failed to persist sandbox pause on websocket logout: %s", exc)
                await websocket.send_json({
                    "type": "client_logout_ack",
                    "status": runtime_status.get("status", "paused"),
                })
                continue

            if data.get("type") == "player_lawyer_response":
                if _player_lawyer_mode_for_context(context) != "defendant":
                    await websocket.send_json({
                        "type": "player_lawyer_error",
                        "error": "Player-lawyer mode is not enabled",
                    })
                    continue

                body = data.get("data") if isinstance(data.get("data"), dict) else data
                request_id = str(body.get("request_id", "") or "").strip()
                message = str(body.get("message", "") or "").strip()
                if not request_id or not message:
                    await websocket.send_json({
                        "type": "player_lawyer_error",
                        "error": "request_id and message are required",
                    })
                    continue

                gateway = getattr(context, "player_gateway", None)
                if gateway is None:
                    gateway = get_or_create_player_gateway(sandbox.id, Path(sandbox.storage_root))
                    setattr(context, "player_gateway", gateway)
                try:
                    resolved = gateway.resolve(request_id, message)
                except ValueError as exc:
                    await websocket.send_json({"type": "player_lawyer_error", "error": str(exc)})
                    continue
                except RuntimeError as exc:
                    await websocket.send_json({"type": "player_lawyer_error", "error": str(exc)})
                    continue

                citation_feedback = None
                try:
                    from src.teaching.citation_check import check_submission_citations

                    citation_feedback = check_submission_citations(message)
                except Exception as exc:
                    logger.warning("[WS] instant citation check failed: %s", exc)

                await _broadcast_sandbox_event(
                    str(sandbox.id),
                    {
                        "type": "player_lawyer_input_submitted",
                        "event": "player_lawyer_input_submitted",
                        "data": resolved.to_dict(),
                        **({"citation_feedback": citation_feedback} if citation_feedback else {}),
                    },
                )
                continue

            on_frontend_message = getattr(runtime_engine, "on_frontend_message", None)
            if callable(on_frontend_message):
                await on_frontend_message(data, websocket)
    except WebSocketDisconnect:
        pass
    finally:
        context.connected_clients.discard(websocket)
        remove_client = getattr(runtime_engine, "remove_client", None)
        if callable(remove_client):
            await remove_client(websocket)
