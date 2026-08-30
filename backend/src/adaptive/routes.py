"""REST routes for the adaptive review module (EduBrain integration).

Mounts under ``/api/adaptive``:

  GET  /status           item bank overview
  GET  /plan             ranked recommendations (mode=diagnostic|review)
  POST /answer           grade one submission + teaching feedback
  GET  /history          mastery evidence summary
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.core.models import User

from ..api.deps import _get_optional_current_user
from . import service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/adaptive", tags=["adaptive"])


class AnswerBody(BaseModel):
    item_id: str
    selected: str = Field(min_length=1, max_length=4)


def _student_of(user: User | None) -> str:
    return str(getattr(user, "id", "") or "").strip() or "anonymous"


@router.get("/status")
async def adaptive_status() -> dict[str, Any]:
    return service.bank_status()


@router.get("/plan")
async def adaptive_plan(
    mode: str = "review",
    limit: int = 5,
    user: User | None = Depends(_get_optional_current_user),
) -> dict[str, Any]:
    if mode not in {"diagnostic", "review"}:
        raise HTTPException(status_code=400, detail="mode must be diagnostic or review")
    return service.plan_for_student(_student_of(user), mode=mode, limit=limit)


@router.post("/answer")
async def adaptive_answer(
    body: AnswerBody,
    user: User | None = Depends(_get_optional_current_user),
) -> dict[str, Any]:
    result = service.answer_item(_student_of(user), body.item_id, body.selected)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=str(result.get("error") or "unknown item"))
    return result


@router.get("/history")
async def adaptive_history(
    user: User | None = Depends(_get_optional_current_user),
) -> dict[str, Any]:
    return service.history_summary(_student_of(user))
