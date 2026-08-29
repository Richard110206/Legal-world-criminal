"""Assemble scoring inputs for the teaching scorer.

Sources:
  - student utterances: `_player_lawyer/player_run_ledger.json` (submissions)
  - dialog context: `{stage}_result.json` `dialog_history`
  - gold standard: `dataset/criminal_case_dataset.json` per-stage fields
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATASET_PATH = PROJECT_ROOT / "dataset" / "criminal_case_dataset.json"

# stage → dataset field used as gold reference
GOLD_STAGE_FIELDS: dict[str, list[str]] = {
    "LC": ["investigation_stage"],
    "INV": ["investigation_stage"],
    "PR": ["prosecution_stage"],
    "DS": ["defense_stage", "guiding_points", "defense_hint"],
    "CR": ["trial_stage", "guiding_points"],
    "CRA": ["appeal_stage"],
}

STAGE_NAMES = {
    "LC": "委托洽谈",
    "INV": "侦查阶段",
    "PR": "审查起诉阶段",
    "DS": "辩护词起草",
    "CR": "刑事一审庭审",
    "CRA": "刑事二审庭审",
}

# role names that identify the defense lawyer (student) in dialog histories
STUDENT_ROLE_ALIASES = {
    "defendant_lawyer",
    "defense_lawyer",
    "defense",
    "lawyer",
    "plaintiff_lawyer",
    "appellant_lawyer",
}


@dataclass
class DialogTurn:
    role: str
    content: str
    timestamp: str = ""


@dataclass
class StudentUtterance:
    stage: str
    role: str
    speaker_label: str
    text: str
    timestamp: str = ""
    context: list[DialogTurn] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "role": self.role,
            "speaker_label": self.speaker_label,
            "text": self.text,
            "timestamp": self.timestamp,
            "context": [
                {"role": turn.role, "content": turn.content[:500]}
                for turn in self.context
            ],
        }


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return payload if isinstance(payload, dict) else None
    except Exception as exc:
        logger.warning("[TeachingTranscript] failed to load %s: %s", path, exc)
        return None


def _normalize_case_id(case_id: str) -> str:
    value = str(case_id or "").strip()
    if value.startswith("case_"):
        return value[5:]
    return value


def load_dataset_cases() -> list[dict[str, Any]]:
    if not DATASET_PATH.exists():
        return []
    try:
        payload = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("[TeachingTranscript] failed to load dataset: %s", exc)
        return []
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        return [record for record in payload.values() if isinstance(record, dict)]
    return []


def find_dataset_case(case_id: str) -> dict[str, Any] | None:
    normalized = _normalize_case_id(case_id)
    for record in load_dataset_cases():
        original_id = str(record.get("original_id") or "").strip()
        if original_id == normalized:
            return record
    return None


def _dialog_turns_from_result(case_output_dir: Path, stage: str) -> list[DialogTurn]:
    result_path = case_output_dir / f"{stage}_result.json"
    payload = _load_json(result_path)
    if not payload:
        return []
    history = payload.get("dialog_history") or []
    turns: list[DialogTurn] = []
    for item in history if isinstance(history, list) else []:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip()
        content = str(item.get("content") or "").strip()
        if not role or not content:
            continue
        turns.append(
            DialogTurn(
                role=role,
                content=content,
                timestamp=str(item.get("timestamp") or "").strip(),
            )
        )
    return turns


def extract_student_utterances(
    case_output_dir: Path,
    stage: str,
) -> list[StudentUtterance]:
    """Extract the student's own utterances for one stage, with short context."""
    case_output_dir = Path(case_output_dir)
    stage = str(stage or "").strip().upper()

    dialog_turns = _dialog_turns_from_result(case_output_dir, stage)

    ledger = _load_json(case_output_dir / "_player_lawyer" / "player_run_ledger.json")
    submissions: list[dict[str, Any]] = []
    if ledger:
        for item in ledger.get("submissions") or []:
            if not isinstance(item, dict):
                continue
            if str(item.get("stage") or "").strip().upper() != stage:
                continue
            if str(item.get("submission_type") or "").strip() != "dialogue":
                continue
            message = str(item.get("final_message") or "").strip()
            if not message:
                continue
            submissions.append(item)

    utterances: list[StudentUtterance] = []
    for item in submissions:
        text = str(item.get("final_message") or "").strip()
        if not text:
            continue
        # match context: find this utterance in dialog history, take preceding turns
        matched_index = _match_utterance_index(dialog_turns, text)
        context = []
        if matched_index is not None:
            for prior in dialog_turns[max(0, matched_index - 2) : matched_index]:
                context.append(prior)
        utterances.append(
            StudentUtterance(
                stage=stage,
                role=str(item.get("role") or "defendant_lawyer"),
                speaker_label=str(item.get("speaker_label") or "辩护律师"),
                text=text,
                timestamp=str(item.get("submitted_at") or "").strip(),
                context=context,
            )
        )

    # fallback: if ledger has no submissions, use dialog turns spoken by the student
    if not utterances:
        for turn in dialog_turns:
            if turn.role.lower() not in STUDENT_ROLE_ALIASES:
                continue
            utterances.append(
                StudentUtterance(
                    stage=stage,
                    role=turn.role,
                    speaker_label=turn.role,
                    text=turn.content,
                    timestamp=turn.timestamp,
                    context=[],
                )
            )

    return utterances


def _match_utterance_index(dialog_turns: list[DialogTurn], text: str) -> int | None:
    needle = str(text or "").strip()
    if not needle:
        return None
    for index, turn in enumerate(dialog_turns):
        candidate = str(turn.content or "").strip()
        if candidate == needle or (needle and needle in candidate and len(needle) >= 4):
            return index
    return None


def load_gold(case_id: str, stage: str) -> dict[str, Any]:
    """Load gold-standard fields for one stage from the case dataset."""
    stage = str(stage or "").strip().upper()
    record = find_dataset_case(case_id)
    if not record:
        return {"gold_incomplete": True, "reason": "case not found in dataset"}

    info = record.get("extracted_info") or {}
    gold: dict[str, Any] = {}
    missing = []
    for gold_field in GOLD_STAGE_FIELDS.get(stage, []):
        value = info.get(gold_field)
        if value is None or (isinstance(value, str) and not value.strip()):
            missing.append(gold_field)
            continue
        gold[gold_field] = value

    gold["case_cause"] = str(info.get("case_cause") or info.get("charge") or "")
    gold["charge"] = str(info.get("charge") or info.get("case_cause") or "")
    gold["gold_incomplete"] = bool(missing)
    gold["missing_fields"] = missing
    return gold


def build_scoring_input(
    case_id: str,
    stage: str,
    case_output_dir: Path,
) -> dict[str, Any]:
    """Assemble the full transcript payload consumed by the judge prompt."""
    utterances = extract_student_utterances(case_output_dir, stage)
    gold = load_gold(case_id, stage)
    record = find_dataset_case(case_id)
    info = (record or {}).get("extracted_info") or {}

    return {
        "case_id": case_id,
        "stage": str(stage or "").strip().upper(),
        "stage_name": STAGE_NAMES.get(str(stage or "").strip().upper(), stage),
        "charge": str(info.get("charge") or info.get("case_cause") or ""),
        "utterance_count": len(utterances),
        "utterances": [utterance.to_dict() for utterance in utterances],
        "gold_incomplete": bool(gold.get("gold_incomplete")),
        "gold": gold,
        "built_at": datetime.now().isoformat(timespec="seconds"),
    }


__all__ = [
    "DialogTurn",
    "StudentUtterance",
    "build_scoring_input",
    "extract_student_utterances",
    "find_dataset_case",
    "load_dataset_cases",
    "load_gold",
]
