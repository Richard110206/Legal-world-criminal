"""对抗质询的检索 grounding：检察官追问前确定性预检索法条 + 类案。

设计取舍：不让检察官 LLM 自己调检索工具（意愿不可控、shared/cn_law 向量
索引缺失），而是场景代码在注入质询指令时同步完成检索、把结果拼进 prompt。
保证每一次质询必然携带可核验的法条/类案依据，且检索结果随 CR_result.json
落盘可审计（防检察官凭空捏造"法律依据"）。

类案语料直接复用 dataset/criminal_case_dataset.json（124 案），检索前排除
当前正在审理的案件——当前案的 guiding_points/一审判决属于评分金标准，
喂给检察官等于泄漏答案。
"""

from __future__ import annotations

import json
import math
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATASET_PATH = PROJECT_ROOT / "dataset" / "criminal_case_dataset.json"

_STATUTE_CONTENT_LIMIT = 200
_CASE_FACTS_LIMIT = 160
_CASE_OPINION_LIMIT = 150


def _law_corpus():
    from ..teaching import law_corpus

    return law_corpus


def statutes_for_challenge(
    defense_statement: str,
    charge: str = "",
    top_k: int = 3,
) -> list[dict[str, Any]]:
    """针对辩护发言检索可用法条（BM25+dense 混合检索，离线时退化为 BM25）。"""
    query = f"{str(defense_statement or '')[:400]} {str(charge or '')}".strip()
    if not query:
        return []
    try:
        hits = _law_corpus().search_law(query, top_k=top_k)
    except Exception:
        return []
    return [
        {
            "source_title": hit.get("source_title") or "",
            "article_ref": hit.get("article_ref") or "",
            "content": str(hit.get("content") or "")[:_STATUTE_CONTENT_LIMIT],
        }
        for hit in hits
        if hit.get("content")
    ]


@lru_cache(maxsize=1)
def _load_case_corpus() -> list[dict[str, Any]]:
    """加载类案语料并预计算 n-gram 词频（与法条检索相同的分词策略）。"""
    if not DATASET_PATH.is_file():
        return []
    try:
        payload = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    cases = payload if isinstance(payload, list) else (payload.get("cases") or [])

    tokenize = _law_corpus()._tokenize
    corpus: list[dict[str, Any]] = []
    for case in cases:
        info = case.get("extracted_info") or {}
        title = str(info.get("source_title") or "").strip()
        charge = str(info.get("charge") or info.get("case_cause") or "").strip()
        facts = str(info.get("case_background") or "").strip()
        if not title or not facts:
            continue
        first = info.get("first_instance") or {}
        opinion = str(first.get("court_opinion") or "").strip()
        sentence = str(first.get("main_sentence") or "").strip()
        doc_text = " ".join(part for part in (title, charge, facts) if part)
        corpus.append(
            {
                "title": title,
                "charge": charge,
                "facts": facts,
                "opinion": opinion,
                "sentence": sentence,
                "_tokens": tokenize(doc_text),
            }
        )
    return corpus


def _idf_for(corpus: list[dict[str, Any]]) -> dict[str, float]:
    df: Counter = Counter()
    for case in corpus:
        for term in case["_tokens"]:
            df[term] += 1
    total = len(corpus) or 1
    return {
        term: math.log((total - freq + 0.5) / (freq + 0.5) + 1.0)
        for term, freq in df.items()
    }


def similar_cases(
    query: str,
    exclude_title: str = "",
    top_k: int = 2,
) -> list[dict[str, Any]]:
    """BM25 类案检索；exclude_title 命中的当前案直接剔除（防金标准泄漏）。"""
    query_text = str(query or "").strip()
    if not query_text:
        return []
    corpus = _load_case_corpus()
    if not corpus:
        return []

    exclude = str(exclude_title or "").strip()
    if exclude:
        corpus = [case for case in corpus if case["title"] != exclude]
    if not corpus:
        return []

    idf = _idf_for(corpus)
    query_tokens = _law_corpus()._tokenize(query_text[:400])
    avgdl = sum(sum(c["_tokens"].values()) for c in corpus) / len(corpus)

    scored: list[tuple[float, dict[str, Any]]] = []
    for case in corpus:
        tokens: Counter = case["_tokens"]
        doc_len = sum(tokens.values()) or 1
        score = 0.0
        for term in query_tokens:
            if term not in idf or term not in tokens:
                continue
            tf = tokens[term]
            score += idf[term] * tf * 2.5 / (tf + 1.5 * (0.25 + 0.75 * doc_len / avgdl))
        if score > 0.0:
            scored.append((score, case))
    scored.sort(key=lambda item: item[0], reverse=True)

    results: list[dict[str, Any]] = []
    for _score, case in scored[: max(1, top_k)]:
        summary = f"{case['facts'][:_CASE_FACTS_LIMIT]}"
        verdict = "；".join(
            part for part in (case["sentence"], case["opinion"][:_CASE_OPINION_LIMIT]) if part
        )
        results.append(
            {
                "title": case["title"],
                "charge": case["charge"],
                "facts": summary,
                "verdict": verdict,
            }
        )
    return results


def build_challenge_block(
    defense_statement: str,
    charge: str = "",
    exclude_title: str = "",
) -> tuple[str, dict[str, Any]]:
    """生成注入检察官 prompt 的质询指令块；返回 (prompt 块, 审计 dict)。"""
    statutes = statutes_for_challenge(defense_statement, charge=charge)
    cases = similar_cases(
        f"{str(defense_statement or '')[:400]} {charge}".strip(),
        exclude_title=exclude_title,
    )

    statute_lines = [
        f"- 《{s['source_title']}》{s['article_ref']}：{s['content']}"
        for s in statutes
    ]
    case_lines = [
        f"- {c['title']}（{c['charge']}）：{c['facts']}… 判决：{c['verdict'] or '（略）'}"
        for c in cases
    ]

    parts = [
        "[对抗质询指令]",
        "辩护人刚才的发言已由书记员记录。请你以公诉人身份对其发起质询：",
        "1. 指出辩护发言中至少一处具体漏洞（事实认定、证据采信、法律适用或量刑情节任一方面）；",
        "2. 必须至少引用一条下列法条作为依据（可节引关键句）；",
        "3. 如下列类案支持控方立场，可援引其裁判结果佐证；与质询无关的类案不得强行引用；",
        "4. 全部质询 4-8 句，以向辩护人发问收尾；不得捏造下列清单之外的条文或案例。",
    ]
    if statute_lines:
        parts.append("\n[可供援引的法条（检索自查）]\n" + "\n".join(statute_lines))
    else:
        parts.append("\n[可供援引的法条] 本次检索未命中相关条文，质询只能围绕在案事实与证据展开，不得引用来路不明的条文。")
    if case_lines:
        parts.append("\n[可供参考的类案（检索自查，非本案）]\n" + "\n".join(case_lines))
    prompt_block = "\n".join(parts)

    audit = {
        "statutes": [
            {"source_title": s["source_title"], "article_ref": s["article_ref"]}
            for s in statutes
        ],
        "cases": [c["title"] for c in cases],
    }
    return prompt_block, audit


__all__ = [
    "build_challenge_block",
    "similar_cases",
    "statutes_for_challenge",
]
