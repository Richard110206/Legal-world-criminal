"""System endpoints: status, agent list, tech catalog, debug console."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

from src.core.models import User
from src.runtime_tech_catalog import build_runtime_tech_catalog
from src.version import BACKEND_VERSION, BACKEND_VERSION_LABEL, BACKEND_VERSION_TIME

from . import app_state
from .agent_status import _get_sandbox_manager, _serialize_registry_agents
from .app_state import DEBUG_UI_DIR
from .deps import (
    _db_session_dependency,
    _get_optional_current_user,
)

router = APIRouter(tags=["system"])


@router.get("/api/agents")
async def list_agents(
    current_user: User | None = Depends(_get_optional_current_user),
    session=Depends(_db_session_dependency),
):
    """返回 Agent 列表，优先使用当前用户 sandbox 的 runtime app_state.registry。"""
    runtime_registry = app_state.registry
    runtime_storage = app_state.storage_manager

    if current_user is not None:
        sandbox = app_state.sandbox_service.get_or_create_user_sandbox(session=session, user_id=current_user.id)
        context = _get_sandbox_manager().get_or_create_context(sandbox)
        runtime_registry = getattr(context, "app_state.registry", None) or runtime_registry
        runtime_storage = getattr(context, "app_state.storage_manager", None) or runtime_storage

    return _serialize_registry_agents(runtime_registry, runtime_storage)


@router.get("/api/status")
async def server_status():
    """服务器状态。"""
    return {
        "status": "running",
        "backend_version": BACKEND_VERSION,
        "backend_version_time": BACKEND_VERSION_TIME,
        "backend_version_label": BACKEND_VERSION_LABEL,
        "clients_connected": len(app_state.engine.clients) if app_state.engine else 0,
        "simulation_running": app_state._simulation_task is not None and not app_state._simulation_task.done(),
    }


@router.get("/api/runtime-tech-catalog")
async def runtime_tech_catalog():
    """返回前端展示用的运行时 Tool / Skill 能力目录。"""
    return build_runtime_tech_catalog()

@router.get("/debug")
async def debug_console_page():
    return FileResponse(DEBUG_UI_DIR / "index.html")
