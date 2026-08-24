"""Local legal-law retrieval + citation verification for the teaching module.

Ported from the legal-rag-poc approach (lexical n-gram cosine + keyword fusion),
adapted to read the project's `legal_corpus/processed/*.jsonl` (刑法 / 刑诉法)
so it works fully offline with zero heavy dependencies.

Responsibilities:
  - `search_law(query, top_k)`   → semantic-ish statute retrieval (法条溯源/查漏)
  - `verify_citation(title, article_ref)` → exact article verification
  - `resolve_article(title, article_ref)` → full text of one article
"""

from __future__ import annotations

import json
import logging
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


def _cosine_score(left: Counter, right: Counter) -> float:
    overlap = set(left) & set(right)
    if not overlap:
        return 0.0
    dot = sum(left[token] * right[token] for token in overlap)
    left_norm = sum(value * value for value in left.values()) ** 0.5
    right_norm = sum(value * value for value in right.values()) ** 0.5
    if not left_norm or not right_norm:
        return 0.0
    return dot / (left_norm * right_norm)


def _keyword_score(query: str, text: str) -> float:
    normalized_query = _normalize_text(query)
    normalized_text = _normalize_text(text)
    if not normalized_query:
        return 0.0

    terms = [term for term in re.split(r"[\s,，。；;：:、]+", normalized_query) if term]
    score = 0.0
    hits = 0
    for term in terms:
        if term in normalized_text:
            hits += 1
            if len(term) >= 6:
                score += 3.2
            elif len(term) >= 4:
                score += 2.2
            else:
                score += 0.8
    if hits >= 2:
        score += 2.0
    article_match = _ARTICLE_LABEL_RE.search(normalized_query)
    if article_match and article_match.group(0) in normalized_text:
        score += 8.0
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


def corpus_stats() -> dict[str, Any]:
    records = _load_corpus_records()
    counts: Counter = Counter(record.get("_source_title") or "" for record in records)
    return {
        "available": bool(records),
        "corpus_dir": str(LEGAL_CORPUS_DIR),
        "total_articles": len(records),
        "by_title": dict(counts),
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
    """Retrieve most relevant statute articles for a query (lexical RAG)."""
    query_text = str(query or "").strip()
    if not query_text:
        return []

    records = _load_corpus_records()
    if not records:
        return []

    query_tokens = _tokenize(query_text)
    scored: list[tuple[float, float, float, dict[str, Any]]] = []
    for record in records:
        if not _title_matches(record, title):
            continue
        text = f"{record.get('_article_ref') or ''} {record.get('_content') or ''}"
        vector_score = _cosine_score(query_tokens, _tokenize(text))
        keyword_score = _keyword_score(query_text, text)
        combined = vector_score * 2.0 + keyword_score
        article_label = _ARTICLE_LABEL_RE.search(record.get("_article_ref") or "")
        if article_label and article_label.group(0) in text:
            combined += 0.15
        scored.append((combined, keyword_score, vector_score, record))

    scored.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    results: list[dict[str, Any]] = []
    for combined, keyword_score, vector_score, record in scored[: max(1, top_k)]:
        results.append(
            {
                "source_title": record.get("_source_title") or "",
                "article_ref": record.get("_article_ref") or "",
                "content": record.get("_content") or "",
                "category": record.get("category") or "",
                "score": round(combined, 4),
                "vector_score": round(vector_score, 4),
                "keyword_score": round(keyword_score, 4),
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
