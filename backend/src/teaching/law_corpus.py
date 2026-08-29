"""Local legal-law retrieval + citation verification for the teaching module.

Reads the project's `legal_corpus/processed/*.jsonl` (刑法 / 刑诉法)
and works fully offline with zero heavy dependencies.

Ranking is BM25 (k1=1.5, b=0.75) over character n-grams, with a field-weight
boost on article_ref hits (×4.0, mirroring the upstream case_retrieval_tool's
title/cause field weighting). IDF down-weights statutory boilerplate and the
length normalization stops long articles from winning on volume.

Responsibilities:
  - `search_law(query, top_k)`   → BM25 statute retrieval (法条溯源/查漏)
  - `verify_citation(title, article_ref)` → exact article verification
  - `resolve_article(title, article_ref)` → full text of one article
"""

from __future__ import annotations

import json
import logging
import math
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

LEGAL_CORPUS_DIR = Path(__file__).resolve().parents[2] / "legal_corpus" / "processed"

_ARTICLE_LABEL_RE = re.compile(r"第[一二三四五六七八九十百千万零〇两\d]+条(?:之[一二三四五六七八九十百千零〇\d]+)?")
_CJK_RUN_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+")

TRUE_ENV_VALUES = {"1", "true", "yes", "on", "enabled"}

# BM25 parameters: standard IR defaults, same as upstream case_retrieval_tool.
BM25_K1 = 1.5
BM25_B = 0.75
# Field weight for article_ref term hits: the article label ("第二百六十四条")
# is the strongest relevance signal in statute retrieval.
ARTICLE_REF_FIELD_WEIGHT = 4.0
# Exact article-label match dominance: when the query names a specific article
# ("刑法第二百六十四条") and a document's article_ref contains that label, the
# label is a primary key — body-text cross-references ("依照本法第二百六十四条
# 的规定定罪处罚") in other articles must not outrank the article itself.
EXACT_LABEL_BOOST = 3.0

# Hybrid fusion: BM25 owns exact/term matching, dense cosine owns semantic
# paraphrases ("喝酒撞死人" ≈ 交通肇事). BM25 stays dominant because statute
# queries are mostly label/term-driven.
HYBRID_BM25_WEIGHT = 0.7
HYBRID_DENSE_WEIGHT = 0.3


def _normalize_text(value: Any) -> str:
    return (
        str(value or "")
        .replace("（", "(")
        .replace("）", ")")
        .replace("《", "")
        .replace("》", "")
        .replace(" ", "")
        .replace("\u3000", "")
        .replace("\n", "")
        .strip()
    )


def _tokenize(text: str) -> Counter:
    """Bag of character n-grams (2/3/4) for CJK runs; whole tokens for ASCII."""
    tokens: list[str] = []
    for match in _CJK_RUN_RE.finditer(str(text or "")):
        value = match.group(0)
        tokens.append(value)
        if re.fullmatch(r"[\u4e00-\u9fff]+", value):
            for size in (2, 3, 4):
                if len(value) < size:
                    continue
                for idx in range(len(value) - size + 1):
                    tokens.append(value[idx : idx + size])
    return Counter(tokens)


@lru_cache(maxsize=1)
def _build_bm25_index() -> dict[str, Any] | None:
    """Precompute per-document term frequencies and corpus-level IDF.

    Two fields are indexed separately with BM25F-style additive fusion:
    content (weight 1.0) and article_ref (weight ARTICLE_REF_FIELD_WEIGHT).
    Additive fusion keeps body-text hits visible even when the label field
    dominates, unlike max/override fusion.
    """
    records = _load_corpus_records()
    if not records:
        return None

    content_terms: list[Counter] = []
    ref_terms: list[Counter] = []
    df: Counter = Counter()
    for record in records:
        content_counter = _tokenize(record.get("_content") or "")
        label_counter = _tokenize(record.get("_article_ref") or "")
        content_terms.append(content_counter)
        ref_terms.append(label_counter)
        for term in set(content_counter) | set(label_counter):
            df[term] += 1

    total_docs = len(records)
    doc_lengths = [
        sum(c.values()) + ARTICLE_REF_FIELD_WEIGHT * sum(r.values())
        for c, r in zip(content_terms, ref_terms)
    ]
    avgdl = (sum(doc_lengths) / total_docs) if total_docs else 1.0
    idf = {
        term: math.log((total_docs - doc_freq + 0.5) / (doc_freq + 0.5) + 1.0)
        for term, doc_freq in df.items()
    }
    return {
        "content_terms": content_terms,
        "ref_terms": ref_terms,
        "doc_lengths": doc_lengths,
        "avgdl": avgdl,
        "idf": idf,
        "total_docs": total_docs,
    }


def _field_score(
    term_freq: int,
    field_weight: float,
    doc_length: float,
    avgdl: float,
    term_idf: float,
) -> float:
    weighted_tf = field_weight * term_freq
    numerator = weighted_tf * (BM25_K1 + 1.0)
    denominator = weighted_tf + BM25_K1 * (1.0 - BM25_B + BM25_B * doc_length / avgdl)
    return term_idf * numerator / denominator


def _bm25_score(index: dict[str, Any], doc_idx: int, query_counter: Counter) -> float:
    content_terms: Counter = index["content_terms"][doc_idx]
    ref_terms: Counter = index["ref_terms"][doc_idx]
    doc_length = index["doc_lengths"][doc_idx]
    avgdl = index["avgdl"]
    idf = index["idf"]

    score = 0.0
    for term in query_counter:
        if term not in idf:
            continue
        term_idf = idf[term]
        if term in content_terms:
            score += _field_score(content_terms[term], 1.0, doc_length, avgdl, term_idf)
        if term in ref_terms:
            score += _field_score(ref_terms[term], ARTICLE_REF_FIELD_WEIGHT, doc_length, avgdl, term_idf)
    return score


@lru_cache(maxsize=1)
def _load_corpus_records() -> list[dict[str, Any]]:
    """Load every article record from legal_corpus/processed/*.jsonl."""
    if not LEGAL_CORPUS_DIR.is_dir():
        logger.warning("[LawCorpus] corpus dir not found: %s", LEGAL_CORPUS_DIR)
        return []

    records: list[dict[str, Any]] = []
    for path in sorted(LEGAL_CORPUS_DIR.glob("*.jsonl")):
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    text = line.strip()
                    if not text:
                        continue
                    record = json.loads(text)
                    source_title = str(record.get("source_title") or "").strip()
                    article_ref = _normalize_text(record.get("article_ref"))
                    content = str(record.get("content") or "").strip()
                    if not source_title or not article_ref or not content:
                        continue
                    record["_source_title"] = source_title
                    record["_article_ref"] = article_ref
                    record["_content"] = content
                    records.append(record)
        except Exception as exc:
            logger.warning("[LawCorpus] failed to load %s: %s", path, exc)
    return records


def clear_corpus_cache() -> None:
    _load_corpus_records.cache_clear()
    _build_bm25_index.cache_clear()


def corpus_stats() -> dict[str, Any]:
    records = _load_corpus_records()
    counts: Counter = Counter(record.get("_source_title") or "" for record in records)
    from . import law_embedding

    vector_index = law_embedding.get_vector_index()
    return {
        "available": bool(records),
        "corpus_dir": str(LEGAL_CORPUS_DIR),
        "total_articles": len(records),
        "by_title": dict(counts),
        "ranker": (
            f"hybrid bm25(k1={BM25_K1}, b={BM25_B})+dense({vector_index.model})"
            if vector_index.available
            else f"bm25(k1={BM25_K1}, b={BM25_B})"
        ),
    }


def _title_matches(record: dict[str, Any], title: str | None) -> bool:
    if not title:
        return True
    normalized = _normalize_text(title)
    source_title = _normalize_text(record.get("_source_title") or "")
    for candidate in (source_title, source_title.replace("中华人民共和国", "")):
        if candidate and (normalized in candidate or candidate in normalized):
            return True
    return False


def search_law(query: str, top_k: int = 5, title: str | None = None) -> list[dict[str, Any]]:
    """Hybrid statute retrieval: BM25 (exact/term) + dense cosine (semantic).

    Fusion: normalized BM25 ×0.7 + cosine ×0.3; exact article-label hits keep
    the BM25 boost. Falls back to pure BM25 when the embedding layer or its
    index is unavailable.
    """
    query_text = str(query or "").strip()
    if not query_text:
        return []

    records = _load_corpus_records()
    if not records:
        return []

    index = _build_bm25_index()
    if index is None:
        return []

    from . import law_embedding

    vector_index = law_embedding.get_vector_index()
    dense_sims: list[float] | None = None
    if vector_index.available:
        sims = vector_index.similarity_scores(query_text)
        if sims is not None and len(sims) == len(records):
            dense_sims = sims

    query_tokens = _tokenize(query_text)
    query_label = _ARTICLE_LABEL_RE.search(_normalize_text(query_text))

    raw_bm25: dict[int, float] = {}
    for doc_idx, record in enumerate(records):
        if not _title_matches(record, title):
            continue
        score = _bm25_score(index, doc_idx, query_tokens)
        if query_label:
            normalized_ref = _normalize_text(record.get("_article_ref") or "")
            if query_label.group(0) in normalized_ref:
                score *= EXACT_LABEL_BOOST
        if score > 0.0:
            raw_bm25[doc_idx] = score

    # candidate pool = BM25 hits ∪ top-50 dense hits (BM25 misses semantic
    # paraphrases; dense alone can float boilerplate — the union keeps both)
    candidates: set[int] = set(raw_bm25)
    if dense_sims is not None:
        dense_ranked = sorted(
            range(len(records)), key=lambda i: dense_sims[i], reverse=True
        )[:50]
        candidates.update(dense_ranked)
    if not candidates:
        return []

    bm25_values = [raw_bm25.get(i, 0.0) for i in candidates] or [0.0]
    bm25_max = max(bm25_values)

    scored: list[tuple[float, int, dict[str, Any], float]] = []
    for doc_idx in candidates:
        record = records[doc_idx]
        if not _title_matches(record, title):
            continue
        bm25_norm = (raw_bm25.get(doc_idx, 0.0) / bm25_max) if bm25_max > 0 else 0.0
        dense = dense_sims[doc_idx] if dense_sims is not None else 0.0
        score = HYBRID_BM25_WEIGHT * bm25_norm + HYBRID_DENSE_WEIGHT * max(dense, 0.0)
        if raw_bm25.get(doc_idx, 0.0) <= 0.0 and dense <= 0.0:
            continue
        scored.append((score, doc_idx, record, bm25_norm))

    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    backend = "hybrid_bm25_dense" if dense_sims is not None else "bm25"
    results: list[dict[str, Any]] = []
    for score, _doc_idx, record, bm25_norm in scored[: max(1, top_k)]:
        results.append(
            {
                "source_title": record.get("_source_title") or "",
                "article_ref": record.get("_article_ref") or "",
                "content": record.get("_content") or "",
                "category": record.get("category") or "",
                "score": round(score, 4),
                "bm25_score": round(bm25_norm, 4),
                "retrieval_backend": backend,
            }
        )
    return results


def resolve_article(title: str, article_ref: str) -> dict[str, Any] | None:
    """Exact article lookup by (title-ish, article_ref).

    Title resolution reuses the canonical alias resolver from citation_check_tool
    (handles 刑法 ↔ 中华人民共和国刑法 and avoids 刑法/刑事诉讼法 confusion).
    """
    from ..tools.legal.citation_check_tool import _normalize_article_ref, _resolve_title

    resolved_title, _match_mode = _resolve_title(str(title or "").strip())
    if not resolved_title:
        return None

    target = _normalize_article_ref(article_ref)
    if not target:
        return None

    for record in _load_corpus_records():
        if (
            record.get("_source_title") == resolved_title
            and record.get("_article_ref") == target
        ):
            return {
                "source_title": record.get("_source_title") or "",
                "article_ref": record.get("_article_ref") or "",
                "content": record.get("_content") or "",
                "category": record.get("category") or "",
            }
    return None


def verify_citation(title: str, article_ref: str) -> dict[str, Any]:
    """Verify one citation; returns status valid / invalid_title / invalid_article."""
    article = resolve_article(title, article_ref)
    if article is None:
        from ..tools.legal.citation_check_tool import _resolve_title

        resolved_title, _mode = _resolve_title(str(title or "").strip())
        if not resolved_title:
            return {"status": "invalid_title", "title": title, "article_ref": article_ref}
        return {"status": "invalid_article", "title": title, "article_ref": article_ref}
    return {"status": "valid", "title": article["source_title"], "article_ref": article_ref, "content": article["content"]}


__all__ = [
    "clear_corpus_cache",
    "corpus_stats",
    "resolve_article",
    "search_law",
    "verify_citation",
]
