"""Shared pytest fixtures.

Test isolation contract:
  - NLI local model never loads (would download GBs / hang CI)
  - learner profiles / skill cards / scoring DB all point at tmp dirs
  - no real LLM calls (judge layers are faked per-test)
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


@pytest.fixture(autouse=True, scope="session")
def _isolated_teaching_env(tmp_path_factory: pytest.TempPathFactory) -> None:
    """Redirect every teaching-side-effect path away from sandbox_data."""
    import os

    base = tmp_path_factory.mktemp("teaching_isolation")
    os.environ["SIMLAW_NLI_MODEL_DISABLED"] = "1"
    os.environ["SIMLAW_TEACHING_PROFILES_DIR"] = str(base / "profiles")
    os.environ["SIMLAW_TEACHING_SKILL_CARDS_DIR"] = str(base / "skill_cards")
    yield
    for key in (
        "SIMLAW_NLI_MODEL_DISABLED",
        "SIMLAW_TEACHING_PROFILES_DIR",
        "SIMLAW_TEACHING_SKILL_CARDS_DIR",
    ):
        os.environ.pop(key, None)
