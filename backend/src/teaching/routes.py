"""REST routes for the teaching subsystem.

Mounts under ``/api/teaching``:

  POST /api/teaching/score            manual scoring trigger
  GET  /api/teaching/event/{case_id}/{stage}
  GET  /api/teaching/profile/{student_id}
  GET  /api/teaching/report/{student_id}

The storage resolver (``set_storage_provider``) is injected from ws_server.py to
avoid circular imports.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/teaching",
    tags=["teaching"],
)


class ScoreBody(BaseModel):
    case_id: str
    stage: str
    student_id: str = ""


# ── injected dependency: (storage_root, user_id) for a request ─────
_storage_provider: Callable[[Request], tuple[Path, str]] | None = None


def set_storage_provider(fn: Callable[[Request], tuple[Path, str]]) -> None:
    global _storage_provider
    _storage_provider = fn


def _require_storage(request: Request) -> tuple[Path, str]:
    if _storage_provider is None:
        raise HTTPException(status_code=500, detail="teaching storage provider not configured")
    return _storage_provider(request)


def _case_output_dir(storage_root: Path, case_id: str) -> Path:
    return Path(storage_root) / "output" / str(case_id or "").strip()


def _case_has_player_data(case_output_dir: Path) -> bool:
    return (
        (case_output_dir / "_player_lawyer" / "player_run_ledger.json").exists()
        or any(case_output_dir.glob("*_result.json"))
    )


@router.post("/score")
async def score_case(body: ScoreBody, request: Request) -> dict[str, Any]:
    from .scorer import TeachingScorer

    storage_root, default_student_id = _require_storage(request)
    case_output_dir = _case_output_dir(storage_root, body.case_id)
    if not case_output_dir.exists() or not _case_has_player_data(case_output_dir):
        raise HTTPException(status_code=404, detail="case output not found for scoring")

    student_id = (body.student_id or default_student_id or "anonymous").strip() or "anonymous"
    event = TeachingScorer().score_stage(
        case_id=body.case_id,
        stage=body.stage,
        case_output_dir=case_output_dir,
        student_id=student_id,
    )
    if event is None:
        raise HTTPException(status_code=400, detail="评分失败或该阶段没有学生发言")

    from . import learner

    learner.update_profile(student_id, event)
    return {"success": True, "event": event, "student_id": student_id}


@router.get("/event/{case_id}/{stage}")
async def get_event(case_id: str, stage: str, request: Request) -> dict[str, Any]:
    storage_root, _ = _require_storage(request)
    event_path = (
        _case_output_dir(storage_root, case_id) / "teaching" / f"{stage.upper()}_learning_event.json"
    )
    if not event_path.exists():
        raise HTTPException(status_code=404, detail="learning event not found")
    return json.loads(event_path.read_text(encoding="utf-8"))


@router.get("/profile/{student_id}")
async def get_profile(student_id: str) -> dict[str, Any]:
    from . import learner

    return learner.get_profile(student_id)


@router.get("/report/{student_id}")
async def get_report(student_id: str) -> dict[str, Any]:
    from .report import build_report

    report = build_report(student_id)
    try:
        from .skill_card import list_skill_cards

        report["skill_cards"] = list_skill_cards(student_id)
    except Exception:
        report["skill_cards"] = []
    return report


@router.get("/skill-cards/{student_id}")
async def get_skill_cards(student_id: str) -> dict[str, Any]:
    from .skill_card import list_skill_cards

    return {"student_id": student_id, "cards": list_skill_cards(student_id)}


@router.get("/skill-cards/{student_id}/{slug}")
async def get_skill_card_detail(student_id: str, slug: str) -> dict[str, Any]:
    from .skill_card import read_skill_card

    card = read_skill_card(student_id, slug)
    if card is None:
        raise HTTPException(status_code=404, detail="skill card not found")
    return card


@router.get("/corpus")
async def corpus_status() -> dict[str, Any]:
    from . import citation_check, law_corpus

    return {
        "available": citation_check.corpus_available(),
        "stats": law_corpus.corpus_stats(),
    }
