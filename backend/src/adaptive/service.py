"""Adaptive quiz service: item bank loading, planning, grading, history.

Storage layout::

    backend/adaptive_data/                      # vendored EduBrain bank
        approved_items.jsonl                    # 30 teacher-approved items
        q_matrix.jsonl                          # item -> knowledge edges
        knowledge_nodes.jsonl                   # 10 criminal-law knowledge nodes
    backend/sandbox_data/adaptive/{sid}/history.jsonl   # answer events

Answer events are the only mutable state (append-only JSONL); the planner
input `history` is derived as ``[{item_id, correct}]``.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .edubrain_planner import plan_path

logger = logging.getLogger(__name__)

ADAPTIVE_DATA_DIR = Path(__file__).resolve().parents[2] / "adaptive_data"
DEFAULT_HISTORY_ROOT = Path(__file__).resolve().parents[2] / "sandbox_data" / "adaptive"

ITEMS_PATH = ADAPTIVE_DATA_DIR / "approved_items.jsonl"
QMATRIX_PATH = ADAPTIVE_DATA_DIR / "q_matrix.jsonl"
NODES_PATH = ADAPTIVE_DATA_DIR / "knowledge_nodes.jsonl"

_BANK_CACHE: dict[str, Any] | None = None
_BANK_CACHE_MTIME: float = 0.0

# case-derived weakness boost: how much rank score a matching knowledge gets
WEAKNESS_BOOST_MISSING = 45.0
WEAKNESS_BOOST_PARTIAL = 18.0


def _history_root() -> Path:
    return Path(os.environ.get("SIMLAW_ADAPTIVE_DATA_DIR") or DEFAULT_HISTORY_ROOT).resolve()


def _safe_student_id(student_id: str) -> str:
    return "".join(ch for ch in str(student_id or "anonymous").strip() if ch.isalnum() or ch in "_-") or "anonymous"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# ── item bank (cached, mtime-invalidated) ────────────────────────────
def _load_bank() -> dict[str, Any]:
    global _BANK_CACHE, _BANK_CACHE_MTIME
    newest = max(
        (p.stat().st_mtime for p in (ITEMS_PATH, QMATRIX_PATH, NODES_PATH) if p.exists()),
        default=0.0,
    )
    if _BANK_CACHE is not None and newest == _BANK_CACHE_MTIME:
        return _BANK_CACHE

    approved = _read_jsonl(ITEMS_PATH)
    q_edges = _read_jsonl(QMATRIX_PATH)
    nodes = _read_jsonl(NODES_PATH)
    by_item = {str(row.get("candidate_id")): row for row in approved}
    _BANK_CACHE = {
        "approved": approved,
        "q_edges": q_edges,
        "nodes": nodes,
        "by_item": by_item,
        "item_to_knowledge": {
            str(row.get("item_id")): str(row.get("knowledge_id")) for row in q_edges
        },
    }
    _BANK_CACHE_MTIME = newest
    return _BANK_CACHE


def bank_status() -> dict[str, Any]:
    bank = _load_bank()
    knowledge_names = sorted({str(n.get("canonical_name")) for n in bank["nodes"]})
    return {
        "available": bool(bank["approved"]),
        "items": len(bank["approved"]),
        "knowledge_nodes": len(bank["nodes"]),
        "knowledge_names": knowledge_names,
        "schema_version": "law-parallel-diagnostic-item-v1.0",
    }


# ── history ──────────────────────────────────────────────────────────
def _history_path(student_id: str) -> Path:
    return _history_root() / _safe_student_id(student_id) / "history.jsonl"


def load_history(student_id: str) -> list[dict[str, Any]]:
    return _read_jsonl(_history_path(student_id))


def _append_history(student_id: str, event: dict[str, Any]) -> None:
    path = _history_path(student_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")


def history_summary(student_id: str) -> dict[str, Any]:
    events = load_history(student_id)
    by_knowledge: dict[str, dict[str, Any]] = {}
    bank = _load_bank()
    for event in events:
        knowledge_id = bank["item_to_knowledge"].get(str(event.get("item_id")), "")
        row = by_knowledge.setdefault(knowledge_id, {"events": 0, "correct": 0})
        row["events"] += 1
        row["correct"] += int(bool(event.get("correct")))
    for row in by_knowledge.values():
        row["accuracy"] = round(row["correct"] / row["events"], 3) if row["events"] else 0.0
    return {
        "student_id": student_id,
        "total_answers": len(events),
        "total_correct": sum(int(bool(e.get("correct"))) for e in events),
        "by_knowledge": by_knowledge,
    }


# ── case-derived weakness signal (精学 → 复习闭环) ───────────────────
def _load_case_weaknesses(student_id: str) -> dict[str, str]:
    """Map planner knowledge names -> weakness level from the learner profile.

    Exact name match first, then substring containment (精学知识点命名自由度
    高于题库 canonical_name). Returns {} when no teaching profile exists —
    the planner then runs purely on quiz history.
    """
    from src.teaching.learner import _profiles_dir

    profile_path = _profiles_dir() / f"{_safe_student_id(student_id)}.json"
    if not profile_path.exists():
        return {}
    try:
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("[adaptive] profile load failed for %s: %s", student_id, exc)
        return {}

    state = profile.get("knowledge_state") or {}
    levels = {"missing": [], "partial": []}
    for name, row in state.items():
        latest = str((row or {}).get("latest") or "").lower()
        if latest in levels:
            levels[latest].append(str(name))

    bank = _load_bank()
    weaknesses: dict[str, str] = {}
    for node in bank["nodes"]:
        canonical = str(node.get("canonical_name") or "")
        matched = "none"
        if any(canonical == w or canonical in w or w in canonical for w in levels["missing"]):
            matched = "missing"
        elif any(canonical == w or canonical in w or w in canonical for w in levels["partial"]):
            matched = "partial"
        if matched != "none":
            weaknesses[canonical] = matched
    return weaknesses


def _apply_case_boost(
    plan: dict[str, Any],
    weaknesses: dict[str, str],
) -> dict[str, Any]:
    """Post-ranking boost: re-rank recommendations from case-derived weaknesses.

    Transparent and auditable — each boosted item keeps its planner reason and
    gains a `case_weakness` annotation; planner evidence model is untouched.
    """
    if not weaknesses:
        return plan
    recs = plan.get("recommendations") or []
    for rec in recs:
        level = weaknesses.get(str(rec.get("knowledge_name") or ""))
        if level == "missing":
            rec["score"] = round(float(rec.get("score") or 0) + WEAKNESS_BOOST_MISSING, 4)
            rec["case_weakness"] = "missing"
        elif level == "partial":
            rec["score"] = round(float(rec.get("score") or 0) + WEAKNESS_BOOST_PARTIAL, 4)
            rec["case_weakness"] = "partial"
    recs.sort(key=lambda r: float(r.get("score") or 0), reverse=True)
    for idx, rec in enumerate(recs, start=1):
        rec["rank"] = idx
    return plan


# ── public operations ────────────────────────────────────────────────
def plan_for_student(
    student_id: str,
    *,
    mode: str = "review",
    limit: int = 5,
) -> dict[str, Any]:
    """Recommend next items.

    mode=diagnostic (预习): planner only — no case signal, cold-start diagnosis.
    mode=review (复习): planner + case-derived weakness boost.
    """
    bank = _load_bank()
    history = [
        {"item_id": str(e.get("item_id")), "correct": 1 if e.get("correct") else 0}
        for e in load_history(student_id)
    ]
    plan = plan_path(
        approved=bank["approved"],
        q_edges=bank["q_edges"],
        nodes=bank["nodes"],
        history=history,
        limit=max(1, min(int(limit), 10)),
    )
    plan["mode"] = str(mode or "review").strip().lower() or "review"
    plan["student_id"] = student_id
    if plan["mode"] == "review":
        weaknesses = _load_case_weaknesses(student_id)
        if weaknesses:
            plan = _apply_case_boost(plan, weaknesses)
            plan["case_weakness_signals"] = weaknesses
    return plan


def answer_item(
    student_id: str,
    item_id: str,
    selected: str,
) -> dict[str, Any]:
    """Grade one submission, persist the event, return teaching feedback."""
    bank = _load_bank()
    record = bank["by_item"].get(str(item_id or "").strip())
    if record is None:
        return {"ok": False, "error": "unknown item_id"}

    item = record.get("item") or {}
    answer = [str(a) for a in (item.get("answer") or [])]
    chosen = str(selected or "").strip().upper()
    correct = chosen in answer and len(answer) == 1

    hit_misconceptions = []
    for miss in item.get("misconceptions") or []:
        triggers = [str(t).upper() for t in miss.get("trigger_options") or []]
        if chosen in triggers:
            hit_misconceptions.append(str(miss.get("description") or ""))

    event = {
        "schema_version": "adaptive-answer-v1",
        "student_id": student_id,
        "item_id": str(item_id),
        "selected": chosen,
        "correct": bool(correct),
        "answered_at": datetime.now(UTC).isoformat(),
        "epoch_ms": int(time.time() * 1000),
    }
    _append_history(student_id, event)

    return {
        "ok": True,
        "item_id": str(item_id),
        "selected": chosen,
        "correct": bool(correct),
        "answer": answer,
        "rationale": str(item.get("rationale") or ""),
        "misconceptions_hit": hit_misconceptions,
        "legal_basis": [
            {
                "law_name": str(c.get("law_name") or ""),
                "article": str(c.get("article") or ""),
                "text": str(c.get("text") or ""),
            }
            for c in item.get("legal_basis") or []
        ],
        "knowledge_name": str(record.get("item", {}).get("knowledge_name") or ""),
    }
