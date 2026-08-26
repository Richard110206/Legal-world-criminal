# -*- coding: utf-8 -*-
"""Deterministic capability scoring — replace LLM subjectivity where evidence exists.

`rule_retrieval`（规范检索）是客观能力：引用存不存在（citation_check 层）、
引用是否支撑论断（NLI 对齐层）都是可确定性计算的证据。本模块用透明公式
直接算分，不再依赖 judge 的自由裁量：

    base     = 10 × (valid 引用数 / 引用总数)          ← 条号存在性
    semantic = 10 × Σ(supports=1, neutral=0.5, contradicts=0) / 对齐总数
    score    = round(0.4 × base + 0.6 × semantic)      ← 语义权重更高：
               张冠李戴（条号对但方向反）是更严重的错误

弃权（返回 None，回退 judge 评分）：
  - 学生全程零显式引用 —— "未引用法条"需结合发言整体判断，judge 更合适
  - 引用全部为无效法源 —— 无对齐可言，judge 结合语境判断

部分降级：
  - 对齐层缺席（NLI+judge 都没跑成）→ 只用 base，rationale 注明
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

CAPABILITY_CODE = "rule_retrieval"
BASE_WEIGHT = 0.4
SEMANTIC_WEIGHT = 0.6
_NLI_CREDIT = {"supports": 1.0, "neutral": 0.5, "contradicts": 0.0}

SOURCE_DETERMINISTIC = "deterministic"
SOURCE_JUDGE = "judge"


def score_rule_retrieval(
    law_citations: list[dict[str, Any]],
    alignment_result: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Compute the deterministic rule_retrieval score entry.

    Returns a capability entry dict (same shape as judge entries, plus
    `source`/`formula` audit fields), or None to abstain → judge fallback.
    """
    citations = [c for c in (law_citations or []) if isinstance(c, dict)]
    if not citations:
        return None

    total = len(citations)
    valid = [c for c in citations if c.get("status") == "valid"]
    invalid = [c for c in citations if c.get("status") != "valid"]
    if not valid:
        return None

    base = 10.0 * len(valid) / total

    alignment_items = [
        item
        for item in ((alignment_result or {}).get("items") or [])
        if isinstance(item, dict) and item.get("verdict") in _NLI_CREDIT
    ]

    if alignment_items:
        credit = sum(_NLI_CREDIT[item["verdict"]] for item in alignment_items)
        semantic = 10.0 * credit / len(alignment_items)
        score = round(BASE_WEIGHT * base + SEMANTIC_WEIGHT * semantic)
        rationale, formula, evidence = _build_rationale(
            total, valid, invalid, alignment_items, semantic
        )
    else:
        # alignment layer unavailable → degrade to base-only (still deterministic)
        score = round(base)
        rationale = (
            f"共引用 {total} 处法条，{len(valid)} 处核验通过、"
            f"{len(invalid)} 处无效（条号或法源错误）。"
            "本次引用语义对齐层未运行，仅按条号核验计分。"
        )
        formula = f"score = base = 10×{len(valid)}/{total} = {base:.1f}"
        evidence = "；".join(
            f"{c.get('citation')}（{c.get('status')}）" for c in citations[:6]
        ) or "（无）"

    return {
        "score": round(score / 10.0, 3),
        "raw": score,
        "weight": None,  # filled by scorer from stage matrix
        "rationale": rationale,
        "evidence_quote": evidence[:300],
        "source": SOURCE_DETERMINISTIC,
        "formula": formula,
    }


def _build_rationale(
    total: int,
    valid: list[dict[str, Any]],
    invalid: list[dict[str, Any]],
    alignment_items: list[dict[str, Any]],
    semantic: float,
) -> tuple[str, str, str]:
    verdict_counts = {"supports": 0, "contradicts": 0, "neutral": 0}
    for item in alignment_items:
        verdict_counts[item["verdict"]] += 1

    parts = [f"共引用 {total} 处法条，{len(valid)} 处核验通过、{len(invalid)} 处无效。"]
    if invalid:
        bad_refs = "、".join(str(c.get("citation") or "") for c in invalid[:3])
        parts.append(f"无效引用：{bad_refs}。")
    parts.append(
        f"语义对齐：{verdict_counts['supports']} 处支持 / "
        f"{verdict_counts['contradicts']} 处反向 / "
        f"{verdict_counts['neutral']} 处凑数（语义分 {semantic:.1f}）。"
    )
    if verdict_counts["contradicts"]:
        parts.append("存在引用方向相反的论断，属规范检索严重错误。")
    rationale = "".join(parts)

    formula = (
        f"score = 0.4×10×{len(valid)}/{total} + 0.6×{semantic:.1f}"
    )

    evidence_bits = [f"{c.get('citation')}（{c.get('status')}）" for c in citations_head(valid, invalid)]
    evidence = "；".join(evidence_bits)

    return rationale, formula, evidence


def citations_head(
    valid: list[dict[str, Any]], invalid: list[dict[str, Any]], limit: int = 6
) -> list[dict[str, Any]]:
    """Audit-friendly citation sample: invalid first, then valid."""
    return (invalid + valid)[:limit]


def merge_deterministic_score(
    capability_scores: dict[str, Any],
    deterministic_entry: dict[str, Any] | None,
) -> dict[str, Any]:
    """Overwrite the judge's rule_retrieval entry with the deterministic one.

    Keeps `judge_raw_score` for cross-validation; flags divergence in the
    rationale so the discrepancy is visible to students/teachers.
    """
    if not deterministic_entry:
        return capability_scores

    judge_entry = capability_scores.get(CAPABILITY_CODE)
    merged = dict(deterministic_entry)
    if isinstance(judge_entry, dict):
        judge_raw = judge_entry.get("raw")
        merged["judge_raw_score"] = judge_raw
        try:
            divergence = abs(float(judge_entry.get("score") or 0.0) * 10 - float(merged["raw"]))
        except (TypeError, ValueError):
            divergence = None
        if divergence is not None and divergence >= 2:
            merged["rationale"] = (
                f"{merged['rationale']}"
                f"（注：裁判主观分 {judge_raw} 与确定性分 {merged['raw']} 分差较大，以确定性分准。）"
            )
    if merged.get("weight") is None:
        weight = judge_entry.get("weight") if isinstance(judge_entry, dict) else None
        merged["weight"] = weight
    capability_scores[CAPABILITY_CODE] = merged
    return capability_scores


__all__ = [
    "CAPABILITY_CODE",
    "SOURCE_DETERMINISTIC",
    "SOURCE_JUDGE",
    "merge_deterministic_score",
    "score_rule_retrieval",
]
