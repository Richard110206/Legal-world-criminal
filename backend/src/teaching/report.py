"""Teaching report builder + practice recommendations.

- build_report(student_id)  → radar data + top errors + knowledge gaps + growth
- recommend(profile)       → quiz questions matched by knowledge gaps (quiz_bank)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .rubrics import CAPABILITIES  # noqa: E402

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
QUIZ_BANK_PATH = PROJECT_ROOT / "dataset" / "quiz_bank.json"

from . import learner  # noqa: E402


def _load_quiz_bank() -> list[dict[str, Any]]:
    if not QUIZ_BANK_PATH.exists():
        return []
    try:
        payload = json.loads(QUIZ_BANK_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("[Report] failed to load quiz bank: %s", exc)
        return []
    return payload if isinstance(payload, list) else []


def build_report(student_id: str) -> dict[str, Any]:
    profile = learner.get_profile(student_id)

    capabilities = []
    for code, spec in CAPABILITIES.items():
        capabilities.append(
            {
                "code": code,
                "name": spec["name"],
                "score": round(float(profile.get("capability_means", {}).get(code, 0.0)), 3),
            }
        )

    knowledge_gaps = sorted(
        (
            {
                "kp": kp,
                "exposed": int(state.get("exposed") or 0),
                "latest": state.get("latest") or "",
            }
            for kp, state in (profile.get("knowledge_state") or {}).items()
            if (state.get("latest") or "") == "missing"
        ),
        key=lambda item: item["exposed"],
        reverse=True,
    )

    top_errors = sorted(
        (
            {"tag": tag, "count": count}
            for tag, count in (profile.get("error_tag_counts") or {}).items()
        ),
        key=lambda item: item["count"],
        reverse=True,
    )[:10]

    return {
        "student_id": student_id,
        "capability_radar": capabilities,
        "knowledge_gaps": knowledge_gaps,
        "top_errors": top_errors,
        "growth_curve": profile.get("growth_curve") or [],
        "cases_played": profile.get("cases_played") or [],
        "recommendations": recommend(profile, limit=6),
        "updated_at": profile.get("updated_at", ""),
    }


def recommend(profile: dict[str, Any], limit: int = 6) -> list[dict[str, Any]]:
    """Recommend quiz questions matching the learner's knowledge gaps."""
    gaps = {
        kp
        for kp, state in (profile.get("knowledge_state") or {}).items()
        if (state.get("latest") or "") in {"missing", "partial"}
    }
    if not gaps:
        return []

    matched: list[dict[str, Any]] = []
    for item in _load_quiz_bank():
        points = {str(p) for p in (item.get("knowledge_points") or [])}
        if not (points & gaps):
            continue
        matched.append(
            {
                "chapter": item.get("chapter", ""),
                "question_no": item.get("question_no"),
                "question": item.get("question", "")[:120],
                "knowledge_points": sorted(points & gaps),
                "question_type": item.get("question_type", ""),
                "source": item.get("source", ""),
            }
        )
        if len(matched) >= limit:
            break
    return matched


__all__ = ["build_report", "recommend"]
