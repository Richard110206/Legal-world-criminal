"""Infer the furthest safely completed case state from persisted artifacts — 纯刑事。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

STATE_PROGRESS_ORDER = [
    "空闲",
    "等待前台接待",
    "委托洽谈中",
    "侦查阶段",
    "审查起诉阶段",
    "起诉书已递交",
    "辩护词起草中",
    "辩护词已递交",
    "等待刑事一审开庭",
    "刑事一审庭审中",
    "刑事一审判决",
    "刑事上诉决策中",
    "刑事上诉状起草中",
    "刑事上诉状已递交",
    "等待刑事二审开庭",
    "刑事二审庭审中",
    "刑事终审判决",
    "已结案",
]

_STATE_RANK = {state: idx for idx, state in enumerate(STATE_PROGRESS_ORDER)}


def normalize_case_id(case_id: Any) -> str:
    """Normalize raw case id into ``case_x`` form."""
    case_key = str(case_id or "").strip()
    if not case_key:
        return ""
    return case_key if case_key.startswith("case_") else f"case_{case_key}"


def _case_output_dir(base_dir: str | Path, case_id: Any) -> Path:
    return Path(base_dir) / "output" / normalize_case_id(case_id)


def _state_rank(state: str) -> int:
    return _STATE_RANK.get(str(state or "").strip(), -1)


def normalize_case_state(raw_state: Any, default: str = "空闲") -> str:
    state = str(raw_state or "").strip()
    if state in _STATE_RANK:
        return state
    return default


def infer_case_state_from_artifacts(base_dir: str | Path, config: dict[str, Any]) -> str:
    """Infer the most advanced recoverable state from outputs and summaries."""
    current_state = normalize_case_state(config.get("case_state", "空闲"))
    party_role = str(config.get("party_role", "plaintiff") or "plaintiff").lower()
    output_dir = _case_output_dir(base_dir, config.get("case_id", ""))

    def stage_done(stage_name: str) -> bool:
        del stage_name
        return False

    candidates = [current_state]

    def add_candidate(state: str) -> None:
        if state:
            candidates.append(state)

    # ── 刑事阶段产物推断 ──
    if (output_dir / "CRA_result.json").exists():
        add_candidate("刑事终审判决")
    elif (output_dir / "CR_result.json").exists():
        add_candidate("刑事一审判决")
    elif (output_dir / "DS_result.json").exists():
        add_candidate("辩护词已递交")
    elif (output_dir / "PR_result.json").exists():
        add_candidate("起诉书已递交")
    elif (output_dir / "INV_result.json").exists():
        add_candidate("审查起诉阶段")

    if (output_dir / "LC_result.json").exists():
        add_candidate("委托洽谈中")

    return max(candidates, key=_state_rank)
