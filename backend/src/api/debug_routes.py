"""Debug endpoints: runtime issues & live runtime config."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from src.core.models import User

from .deps import _get_current_user
from .runtime_config import (
    _apply_runtime_config,
    _normalize_runtime_config,
    _read_runtime_config,
    _schedule_backend_restart,
)
from .runtime_issues import get_runtime_issues
from .schemas import RuntimeConfigRequest

router = APIRouter(tags=["debug"])


@router.get("/api/debug/runtime-issues")
async def debug_runtime_issues():
    latest = get_runtime_issues()[0] if get_runtime_issues() else None
    return {
        "issues": list(get_runtime_issues()),
        "latest": latest,
        "has_errors": any(issue["level"] == "ERROR" for issue in get_runtime_issues()),
    }


@router.get("/api/debug/runtime-config")
async def debug_runtime_config(current_user: User = Depends(_get_current_user)):
    _ = current_user
    return {
        "success": True,
        "config": _read_runtime_config(),
        "restart_required": False,
        "restart_hint": "修改后如需整套后端环境按新配置重启，请点“保存并重启后端”。",
    }


@router.post("/api/debug/runtime-config")
async def update_debug_runtime_config(
    payload: RuntimeConfigRequest,
    current_user: User = Depends(_get_current_user),
):
    _ = current_user
    config = _normalize_runtime_config(payload)
    _apply_runtime_config(config)
    return {
        "success": True,
        "config": _read_runtime_config(),
        "restart_required": False,
        "restart_hint": "当前后端进程和 .env 已同步；如果你想按 Docker 环境重启一遍，请点“保存并重启后端”。",
    }


@router.post("/api/debug/runtime-config/restart")
async def update_debug_runtime_config_and_restart(
    payload: RuntimeConfigRequest,
    current_user: User = Depends(_get_current_user),
):
    _ = current_user
    config = _normalize_runtime_config(payload)
    _apply_runtime_config(config)
    _schedule_backend_restart()
    return {
        "success": True,
        "config": _read_runtime_config(),
        "restart_required": True,
        "restart_pending": True,
        "message": "配置已保存，后端正在重启。页面会短暂断开，几秒后刷新即可。",
    }
