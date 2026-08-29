"""FastAPI dependencies: auth, DB session, sandbox resolution, teaching storage."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import Depends, Header, HTTPException, Request

from src.core.auth import AuthError, create_access_token, decode_access_token, get_access_token_expires_at
from src.core.database import get_db_session
from src.core.models import Sandbox, User
from src.core.user_service import (
    UserNotFoundError,
    get_user_by_id,
)

from . import app_state
from .app_state import _get_session_factory

logger = logging.getLogger("ws_server")


def _teaching_storage_for_request(request: Request) -> tuple[Path, str]:
    """Resolve (storage_root, user_id) for the authenticated user's sandbox."""
    from src.core.auth import AuthError
    from src.core.user_service import UserNotFoundError

    from .agent_status import _get_sandbox_manager  # deferred: breaks dep cycle

    token = _extract_bearer_token(request.headers.get("authorization"))
    try:
        with get_db_session(_get_session_factory()) as session:
            current_user = _get_user_from_access_token(token, session)
            sandbox = _require_user_sandbox(session, current_user)
    except (AuthError, UserNotFoundError) as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    storage_root = str(sandbox.storage_root or "")
    if not storage_root:
        context = _get_sandbox_manager().get_or_create_context(sandbox)
        storage_root = str(
            getattr(context, "storage_root", None) or getattr(context, "sandbox_data_dir", "")
        )
    return Path(storage_root), str(getattr(current_user, "id", ""))
def _serialize_user(user: User) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "status": user.status,
        "token_version": user.token_version,
    }


def _build_auth_response(user: User) -> dict:
    return {
        "user": _serialize_user(user),
        "access_token": create_access_token(user_id=user.id, token_version=user.token_version),
        "token_type": "bearer",
        "expires_at": get_access_token_expires_at().isoformat(),
    }
def _extract_bearer_token(authorization: str | None) -> str:
    raw_value = str(authorization or "").strip()
    if not raw_value.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = raw_value[7:].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing bearer token")
    return token


def _db_session_dependency():
    with get_db_session(_get_session_factory()) as session:
        yield session


def _get_current_user(
    authorization: str | None = Header(default=None),
    session=Depends(_db_session_dependency),
) -> User:
    token = _extract_bearer_token(authorization)
    try:
        claims = decode_access_token(token)
        user = get_user_by_id(session=session, user_id=claims["user_id"])
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except UserNotFoundError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    if user.token_version != claims["token_version"]:
        raise HTTPException(status_code=401, detail="Token version mismatch")
    return user


def _get_optional_current_user(
    authorization: str | None = Header(default=None),
    session=Depends(_db_session_dependency),
) -> User | None:
    if not str(authorization or "").strip():
        return None

    token = _extract_bearer_token(authorization)
    try:
        claims = decode_access_token(token)
        user = get_user_by_id(session=session, user_id=claims["user_id"])
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except UserNotFoundError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    if user.token_version != claims["token_version"]:
        raise HTTPException(status_code=401, detail="Token version mismatch")
    return user


def _update_sandbox_from_runtime_status(session, sandbox: Sandbox, runtime_status: dict) -> None:
    app_state.sandbox_service.update_sandbox_status(
        session=session,
        sandbox=sandbox,
        sandbox_status=str(runtime_status.get("status") or sandbox.status),
        simulation_status=str(runtime_status.get("status") or sandbox.status),
        active_cases=int(runtime_status.get("active_cases", 0) or 0),
        clients_connected=int(runtime_status.get("clients_connected", 0) or 0),
    )


def _require_user_sandbox(session, user: User) -> Sandbox:
    sandbox = app_state.sandbox_service.get_user_sandbox(session=session, user_id=user.id)
    if sandbox is None:
        raise HTTPException(status_code=404, detail="Sandbox not created")
    return sandbox
def _get_user_from_access_token(token: str, session) -> User:
    claims = decode_access_token(token)
    user = get_user_by_id(session=session, user_id=claims["user_id"])
    if user.token_version != claims["token_version"]:
        raise AuthError("Token version mismatch")
    return user


def _resolve_sandbox_context_by_id(sandbox_id: str):
    from .agent_status import _get_sandbox_manager  # deferred: breaks dep cycle

    manager = _get_sandbox_manager()
    if hasattr(manager, "_contexts"):
        return getattr(manager, "_contexts").get(sandbox_id)
    if hasattr(manager, "contexts"):
        return getattr(manager, "contexts").get(sandbox_id)
    return None
