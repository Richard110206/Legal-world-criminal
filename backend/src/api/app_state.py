"""Centralised runtime state & path constants for the API layer.

Every mutable process-wide singleton lives here and is accessed from other
modules as ``app_state.<name>`` (never ``from app_state import <name>``,
which would freeze a copy). Constants are safe to import directly.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from sqlalchemy.orm import sessionmaker

from src.core.checkpoint_manager import CheckpointManager
from src.core.database import Base, create_database_engine, create_session_factory
from src.core.event_bus import EventBus
from src.core.file_storage_manager import FileStorageManager
from src.core.sandbox_manager import SandboxManager
from src.core.sandbox_service import SandboxService
from src.orchestration.agent_registry import AgentRegistry
from src.orchestration.case_fsm import CaseStateMachine
from src.simulation.ws_frontend_engine import WebSocketFrontendEngine
from src.utils.runtime_flags import scenario_verbose_enabled

logger = logging.getLogger("ws_server")

_backend_dir = Path(__file__).resolve().parents[2]  # backend/
ROOT_ENV_PATH = _backend_dir.parent / ".env"
SANDBOX_DATA_DIR = _backend_dir / "sandbox_data"
SANDBOX_SEED_DIR = _backend_dir / "sandbox_seed_data"

SCENARIO_VERBOSE = scenario_verbose_enabled()
SIMLAW_FRONTEND_MODE = str(os.getenv("SIMLAW_FRONTEND_MODE", "auto") or "auto").strip().lower().replace("-", "_")
SIMLAW_TURN_MODE = str(os.getenv("SIMLAW_TURN_MODE", "auto") or "auto").strip().lower().replace("-", "_")


def _read_non_negative_int_env(name: str, default: int = 0) -> int:
    raw = str(os.getenv(name, "") or "").strip()
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        logger.warning("Invalid %s=%r, fallback to %d", name, raw, default)
        return default


def _read_non_negative_int_env(name: str, default: int = 0) -> int:
    raw = str(os.getenv(name, "") or "").strip()
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        logger.warning("Invalid %s=%r, fallback to %d", name, raw, default)
        return default
SANDBOX_DATA_DIR = _backend_dir / "sandbox_data"
SANDBOX_SEED_DIR = _backend_dir / "sandbox_seed_data"
CASE_PICKER_METADATA_PATH = SANDBOX_SEED_DIR / "case_picker_metadata.yaml"
DEBUG_UI_DIR = _backend_dir / "debug_ui"
RUNTIME_CONFIG_KEYS = (
    "SIMLAW_PROMPT_PROFILE",
    "OPENAI_API_KEY",
    "OPENAI_MODEL_NAME",
    "OPENAI_API_BASE_URL",
)
MAP_JSON_PATH = _backend_dir.parent / "assets" / "map" / "new_ailaw_town.json"
CASE_SPAWN_INTERVAL_SECONDS = 15.0
MAX_CONCURRENT_CASES = _read_non_negative_int_env("MAX_CONCURRENT_CASES", 1)
CHARACTER_POOL = [
    "Adam",
    "Alex",
    "Amelia",
    "Ash",
    "Bob",
    "Bruce",
    "Conference_man",
    "Conference_woman",
    "Dan",
    "Edward",
    "Lucy",
    "Molly",
    "Pier",
    "Rob",
    "Roki",
    "Samuel",
]


# ── mutable runtime singletons (access via app_state.<name>) ──────────
engine: WebSocketFrontendEngine | None = None
event_bus: EventBus | None = None
registry: AgentRegistry | None = None
checkpoint_mgr: CheckpointManager | None = None
storage_manager: FileStorageManager | None = None
case_fsm: CaseStateMachine | None = None
_simulation_task: Any = None  # asyncio.Task | None
_db_engine = None
_session_factory: sessionmaker | None = None
sandbox_service = SandboxService(base_dir=SANDBOX_DATA_DIR, seed_source_dir=SANDBOX_SEED_DIR)
sandbox_manager: SandboxManager | None = None


def _get_session_factory():
    global _db_engine, _session_factory
    if _session_factory is None:
        _db_engine = create_database_engine()
        Base.metadata.create_all(_db_engine)
        _session_factory = create_session_factory(_db_engine)
    return _session_factory
