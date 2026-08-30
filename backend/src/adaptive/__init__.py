"""Adaptive review module (EduBrain planner integration).

Vendors the EduBrain adaptive-learning planner (authored by the adaptive
sub-team, package `edubrain_adaptive`) and wires it into the SimLaw stack:

    GET  /api/adaptive/plan    -> ranked quiz recommendations + reasons
    POST /api/adaptive/answer  -> grade submission, return rationale/citations
    GET  /api/adaptive/history -> per-knowledge mastery evidence
    GET  /api/adaptive/status  -> item bank overview

Data flow (matches the product blueprint's adaptive loop):

    精学案件评分(LearningEvent.knowledge_gaps) ──┐
                                                  ├→ planner priority → 推荐 → 作答 → 历史
    复习页答题记录(item_id, correct) ─────────────┘

The planner itself is kept verbatim in `edubrain_planner.py`; cross-module
signals (case-derived knowledge weaknesses) are applied as a transparent
post-ranking boost instead of polluting its evidence model.
"""

from .service import (
    answer_item,
    bank_status,
    history_summary,
    load_history,
    plan_for_student,
)

__all__ = [
    "answer_item",
    "bank_status",
    "history_summary",
    "load_history",
    "plan_for_student",
]
