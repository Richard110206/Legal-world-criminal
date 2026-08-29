"""Auth endpoints: register / login / refresh / me."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from src.core.models import User
from src.core.user_service import (
    InvalidAuthInputError,
    InvalidCredentialsError,
    UserAlreadyExistsError,
    authenticate_user,
    register_user,
)

from .deps import (
    _build_auth_response,
    _db_session_dependency,
    _get_current_user,
    _serialize_user,
)
from .schemas import AuthRequest

router = APIRouter(tags=["auth"])


# ── REST 端点（少量查询用） ──

@router.post("/api/auth/register")
async def register_auth(payload: AuthRequest, session=Depends(_db_session_dependency)):
    try:
        user = register_user(session=session, email=payload.email, password=payload.password)
    except InvalidAuthInputError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except UserAlreadyExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return _build_auth_response(user)


@router.post("/api/auth/login")
async def login_auth(payload: AuthRequest, session=Depends(_db_session_dependency)):
    try:
        user = authenticate_user(session=session, email=payload.email, password=payload.password)
    except InvalidAuthInputError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except InvalidCredentialsError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    return _build_auth_response(user)


@router.post("/api/auth/refresh")
async def refresh_auth(current_user: User = Depends(_get_current_user)):
    return _build_auth_response(current_user)


@router.get("/api/auth/me")
async def auth_me(current_user: User = Depends(_get_current_user)):
    return _serialize_user(current_user)
