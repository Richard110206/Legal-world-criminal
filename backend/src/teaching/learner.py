"""Cross-case learner profile accumulation.

Each LearningEvent updates a learner profile at
`sandbox_data/teaching/profiles/{student_id}.json`.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from .rubrics import stage_capability_weights

logger = logging.getLogger(__name__)

PROFILE_SCHEMA = "learner-profile-v1"
DEFAULT_PROFILES_DIR = (
    Path(__file__).resolve().parents[2] / "sandbox_data" / "teaching" / "profiles"
)


def _profiles_dir() -> Path:
    return Path(
        os.environ.get("SIMLAW_TEACHING_PROFILES_DIR") or DEFAULT_PROFILES_DIR
    ).resolve()


def _profile_path(student_id: str) -> Path:
    safe_id = "".join(
        ch for ch in str(student_id or "anonymous").strip() if ch.isalnum() or ch in "_-"
    )
    return _profiles_dir() / f"{safe_id}.json"


def _load_profile(student_id: str) -> dict[str, Any]:
    path = _profile_path(student_id)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("[Learner] failed to load profile %s: %s", student_id, exc)
    return {
        "schema_version": PROFILE_SCHEMA,
        "student_id": student_id,
        "capability_means": {},
        "knowledge_state": {},
        "error_tag_counts": {},
        "growth_curve": [],
        "cases_played": [],
        "updated_at": "",
    }


def update_profile(student_id: str, event: dict[str, Any]) -> dict[str, Any]:
    """Fold one LearningEvent into the learner profile; returns updated profile."""
    student_id = str(student_id or "anonymous").strip() or "anonymous"
    profile = _load_profile(student_id)
    stage = str(event.get("stage") or "").strip().upper()
    case_id = str(event.get("case_id") or "")
    scored_at = str(event.get("scored_at") or datetime.now().isoformat(timespec="seconds"))

    # capability means (stage-weighted average: sum(score*weight)/sum(weight))
    weights = stage_capability_weights(stage)
    means = profile.setdefault("capability_means", {})
    weighted_sums = profile.setdefault("_capability_weighted_sums", {})
    weighted_totals = profile.setdefault("_capability_weighted_totals", {})
    for code, entry in (event.get("capability_scores") or {}).items():
        if not isinstance(entry, dict):
            continue
        score = float(entry.get("score") or 0.0)
        weight = weights.get(code, 0.5)
        weighted_sums[code] = float(weighted_sums.get(code, 0.0)) + score * weight
        weighted_totals[code] = float(weighted_totals.get(code, 0.0)) + weight
        means[code] = round(weighted_sums[code] / weighted_totals[code], 3)

    # knowledge state
    knowledge_state = profile.setdefault("knowledge_state", {})
    for verdict in event.get("knowledge_verdicts") or []:
        kp = str(verdict.get("kp") or "").strip()
        if not kp:
            continue
        status = str(verdict.get("status") or "partial")
        entry = knowledge_state.setdefault(kp, {"exposed": 0, "latest": "", "history": []})
        entry["exposed"] = int(entry.get("exposed") or 0) + 1
        entry["history"].append(status)
        entry["latest"] = status

    # knowledge gaps
    for gap in event.get("knowledge_gaps") or []:
        kp = str(gap or "").strip()
        if kp:
            entry = knowledge_state.setdefault(kp, {"exposed": 0, "latest": "", "history": []})
            entry["history"].append("missing")
            entry["latest"] = "missing"
            entry["exposed"] = int(entry.get("exposed") or 0) + 1

    # error tags
    error_counts = profile.setdefault("error_tag_counts", {})
    for tag in event.get("error_tags") or []:
        name = str(tag or "").strip()
        if not name:
            continue
        # group "法条引用错误-264与266混淆" -> "法条引用错误"
        base = name.split("-")[0] if "-" in name else name
        error_counts[base] = int(error_counts.get(base, 0)) + 1

    # growth curve + cases
    scores = [float(e.get("score") or 0.0) for e in (event.get("capability_scores") or {}).values()]
    mean = round(sum(scores) / len(scores), 3) if scores else 0.0
    growth = profile.setdefault("growth_curve", [])
    growth.append(
        {
            "at": str(scored_at)[:10],
            "stage": stage,
            "case_id": case_id,
            "mean": mean,
        }
    )
    cases_played = profile.setdefault("cases_played", [])
    if case_id and case_id not in cases_played:
        cases_played.append(case_id)

    profile["updated_at"] = datetime.now().isoformat(timespec="seconds")
    path = _profile_path(student_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return profile


def get_profile(student_id: str) -> dict[str, Any]:
    return _load_profile(student_id)


__all__ = ["get_profile", "update_profile"]
