"""Teaching module — 刑法教学评分（精学闭环）。

学生扮演辩护律师 → AI 陪练质询 → 阶段结束后 LLM 裁判按 CJ-Bench 8 能力评分
→ LearningEvent（找漏洞 + 法条溯源 + 知识点欠缺）→ 学习者画像 / 报告。
"""

from __future__ import annotations

from .citation_check import (
    check_submission_citations,
    collect_law_citations,
    corpus_available,
    extract_and_verify_citations,
)
from .learner import get_profile, update_profile
from .report import build_report, recommend
from .rubrics import (
    CAPABILITIES,
    STAGE_CAPABILITY_MATRIX,
    build_judge_eval_prompt,
    build_judge_system_prompt,
    stage_capability_weights,
    validate_rubrics,
)
from .scorer import TeachingScorer, score_stage_sync
from .transcript import (
    build_scoring_input,
    extract_student_utterances,
    load_gold,
)

__all__ = [
    "CAPABILITIES",
    "STAGE_CAPABILITY_MATRIX",
    "TeachingScorer",
    "build_judge_eval_prompt",
    "build_judge_system_prompt",
    "build_report",
    "build_scoring_input",
    "check_submission_citations",
    "collect_law_citations",
    "corpus_available",
    "extract_and_verify_citations",
    "extract_student_utterances",
    "get_profile",
    "load_gold",
    "recommend",
    "score_stage_sync",
    "stage_capability_weights",
    "update_profile",
    "validate_rubrics",
]
