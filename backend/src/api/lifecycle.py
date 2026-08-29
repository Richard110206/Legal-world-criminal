"""App lifecycle: startup / shutdown hooks and signal handlers."""

from __future__ import annotations

import asyncio
import logging
import sys

from fastapi import APIRouter

from . import app_state
from .simulation_runtime import _initialize_runtime_state, _set_engine_paused

logger = logging.getLogger("ws_server")
router = APIRouter()


# ── 启动事件 ──

@router.on_event("startup")
async def startup():

    (
        app_state.event_bus,
        app_state.registry,
        app_state.checkpoint_mgr,
        app_state.storage_manager,
        app_state.case_fsm,
    ) = _initialize_runtime_state()

    # 默认不自动开始模拟，等待前端显式控制
    session_state = app_state.checkpoint_mgr.load_session_state()
    app_state._simulation_task = None
    _set_engine_paused(False)

    if session_state and session_state.get("simulation_status") == "running":
        app_state.checkpoint_mgr.mark_session_paused()
        logger.info("检测到上次会话为 running，已切换为 paused，等待前端手动开始")
    elif session_state and session_state.get("simulation_status") == "paused":
        logger.info("检测到未完成会话，等待前端手动恢复")
    else:
        logger.info("当前为待启动状态，等待前端手动开始模拟")

    logger.info("WebSocket server started on ws://localhost:8000/ws")


@router.on_event("shutdown")
async def shutdown():
    """优雅关闭处理器。"""

    logger.info("Shutting down server...")

    # 标记会话为暂停状态（而非完成）
    if app_state.checkpoint_mgr:
        app_state.checkpoint_mgr.mark_session_paused()
        logger.info("Session marked as paused for recovery")

    # 取消模拟任务
    if app_state._simulation_task and not app_state._simulation_task.done():
        app_state._simulation_task.cancel()
        try:
            await app_state._simulation_task
        except asyncio.CancelledError:
            pass

    logger.info("Server shutdown complete")


def handle_sigterm(signum, frame):
    """处理 SIGTERM 信号（优雅关闭）。"""
    logger.info("Received SIGTERM, initiating graceful shutdown...")
    # FastAPI 会自动调用 shutdown 事件处理器


def handle_sigint(signum, frame):
    """处理 SIGINT 信号（Ctrl+C）。"""
    logger.info("Received SIGINT (Ctrl+C), initiating graceful shutdown...")
    # FastAPI 会自动调用 shutdown 事件处理器
    sys.exit(0)
