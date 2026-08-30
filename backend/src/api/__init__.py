"""SimLaw Town API package.

Replaces the former 4.5k-line ws_server.py monolith with domain modules:

    app_state            process-wide singletons & path constants
    runtime_issues       log-capture issue ring buffer + payload builders
    agent_status         agent serialisation & sandbox runtime context
    case_catalog         case picker / documents / report transcripts
    deps                 auth, DB session, sandbox resolution dependencies
    player_gateway_admin player-lawyer gateway lifecycle & providers
    runtime_config       debug runtime config persistence
    simulation_runtime   simulation lifecycle (init/reset/run/resume loops)
    ws_endpoint          the /ws WebSocket endpoint
    *_routes             REST routers per domain
    lifecycle            startup/shutdown hooks & signal handlers

The app object is created by :func:`create_app`; ``ws_server.py`` remains the
ASGI entrypoint (``uvicorn ws_server:app``) for full backward compatibility.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

_backend_dir = Path(__file__).resolve().parents[2]
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

from dotenv import load_dotenv  # noqa: E402

ROOT_ENV_PATH = _backend_dir.parent / ".env"


def _should_override_dotenv() -> bool:
    import os

    return str(os.getenv("SIMLAW_DOTENV_OVERRIDE", "") or "").strip().lower() in {"1", "true", "yes", "on"}


load_dotenv(ROOT_ENV_PATH, override=_should_override_dotenv())

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
    force=True,
)
# CAMEL library logs are noisy at INFO; keep warnings only.
logging.getLogger("camel.base_model").setLevel(logging.WARNING)
logging.getLogger("camel.camel.agents.chat_agent").setLevel(logging.WARNING)

from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

from src.human_eval.routes import create_human_eval_router  # noqa: E402
from src.player_lawyer.routes import (  # noqa: E402
    router as player_lawyer_router,
)
from src.player_lawyer.routes import (
    set_gateway_provider as _set_player_gw_provider,
)
from src.player_lawyer.routes import (
    set_response_assist_provider as _set_player_response_assist_provider,
)
from src.player_lawyer.routes import (
    set_status_provider as _set_player_status_provider,
)
from src.teaching.routes import router as teaching_router  # noqa: E402
from src.teaching.routes import set_storage_provider  # noqa: E402

from . import lifecycle as _lifecycle  # noqa: E402
from . import system_routes, ws_endpoint  # noqa: E402
from .auth_routes import router as auth_router  # noqa: E402
from .debug_routes import router as debug_router  # noqa: E402
from .deps import _teaching_storage_for_request  # noqa: E402
from .player_gateway_admin import (  # noqa: E402
    _get_player_gateway_for_request,
    _get_player_response_assist_for_request,
    _player_lawyer_status_for_request,
)
from .sandbox_routes import router as sandbox_router  # noqa: E402
from .simulation_routes import router as simulation_router  # noqa: E402


def create_app() -> FastAPI:
    app = FastAPI(title="SimLaw Town WebSocket Server")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # feature subsystems
    app.include_router(player_lawyer_router)
    set_storage_provider(_teaching_storage_for_request)
    app.include_router(teaching_router)
    app.include_router(
        create_human_eval_router(
            current_user_dependency=_get_current_user_dep(),
            session_dependency=_db_session_dep(),
        )
    )

    # domain routers
    app.include_router(ws_endpoint.router)
    app.include_router(auth_router)
    app.include_router(sandbox_router)
    app.include_router(system_routes.router)
    app.include_router(debug_router)
    app.include_router(simulation_router)

    # lifecycle hooks (startup/shutdown) via router events
    app.include_router(_lifecycle.router)

    return app


def _get_current_user_dep():
    from .deps import _get_current_user

    return _get_current_user


def _db_session_dep():
    from .deps import _db_session_dependency

    return _db_session_dependency


# Inject player-lawyer request providers (called back per request).
_set_player_gw_provider(_get_player_gateway_for_request)
_set_player_status_provider(_player_lawyer_status_for_request)
_set_player_response_assist_provider(_get_player_response_assist_for_request)

app = create_app()
