"""Simulation control endpoints: status / start / pause / restart."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from . import app_state
from .simulation_runtime import (
    _build_simulation_status,
    _cancel_simulation_task,
    _initialize_runtime_state,
    _reset_simulation_storage,
    _set_engine_paused,
    _start_or_resume_simulation,
)

router = APIRouter(tags=["simulation"])


@router.get("/api/simulation/status")
async def simulation_status():
    """模拟状态。"""
    return _build_simulation_status()


@router.post("/api/simulation/start")
async def start_simulation():
    """开始或恢复模拟。"""
    status = await _start_or_resume_simulation()
    return {"success": True, "simulation": status}


@router.post("/api/simulation/pause")
async def pause_simulation():
    """暂停模拟。"""
    if app_state.checkpoint_mgr is None:
        raise HTTPException(status_code=503, detail="Simulation engine not ready")

    _set_engine_paused(True)
    app_state.checkpoint_mgr.mark_session_paused()
    return {"success": True, "simulation": _build_simulation_status()}


@router.post("/api/simulation/restart")
async def restart_simulation():
    """重置模拟进度并回到待启动状态。"""

    if app_state.storage_manager is None or app_state.checkpoint_mgr is None:
        raise HTTPException(status_code=503, detail="Simulation app_state.engine not ready")

    cancelled = await _cancel_simulation_task()
    if not cancelled:
        raise HTTPException(
            status_code=409,
            detail="当前仍有模型调用未退出，请先暂停并等待几秒后再重跑",
        )
    _set_engine_paused(False)
    _reset_simulation_storage(app_state.storage_manager)
    if app_state.engine is None:
        raise HTTPException(status_code=503, detail="Simulation app_state.engine not ready")

    (
        app_state.event_bus,
        app_state.registry,
        app_state.checkpoint_mgr,
        app_state.storage_manager,
        app_state.case_fsm,
    ) = _initialize_runtime_state(existing_engine=app_state.engine)
    app_state.engine.agent_registry = app_state.registry
    app_state.engine.storage = app_state.storage_manager
    app_state.engine._agent_states.clear()
    app_state.engine._ack_events.clear()

    return {
        "success": True,
        "reload_required": True,
        "simulation": _build_simulation_status(),
    }
