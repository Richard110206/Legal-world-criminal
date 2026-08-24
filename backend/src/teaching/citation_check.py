"""Citation extraction + verification for teaching feedback.

Uses the local law corpus (刑法/刑诉法) via `law_corpus`:
  - deterministic citation extraction (《刑法》第X条)
  - exact verification (valid / invalid_title / invalid_article)
  - for wrong article numbers, a lexical suggestion of the closest real article
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

from ..tools.legal.citation_check_tool import EXPLICIT_CITATION_RE  # noqa: E402
from . import law_corpus  # noqa: E402

INSTANT_CITATION_FLAG = "SIMLAW_TEACHING_INSTANT_CITATION"


def instant_citation_enabled() -> bool:
    return str(os.environ.get(INSTANT_CITATION_FLAG, "1")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
        "enabled",
    }


def corpus_available() -> bool:
    return bool(law_corpus.corpus_stats().get("available"))


def _suggestion_for(title: str, article_ref: str) -> str:
    query = f"{article_ref} {title}"
    results = law_corpus.search_law(query, top_k=2)
    parts = []
    for result in results:
        if result["article_ref"] == article_ref:
            continue
        parts.append(
            f"{result['source_title']} {result['article_ref']}（{result['content'][:40]}…）"
        )
    return "；".join(parts) if parts else ""


def extract_and_verify_citations(text: str) -> list[dict[str, Any]]:
    """Extract explicit 《X》第N条 citations and verify each against the corpus."""
    content = str(text or "")
    citations: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for match in EXPLICIT_CITATION_RE.finditer(content):
        raw_title = str(match.group("title") or "").strip()
        article_ref = str(match.group("article") or "").strip()
        key = (raw_title, article_ref)
        if key in seen:
            continue
        seen.add(key)

        result = law_corpus.verify_citation(raw_title, article_ref)
        item: dict[str, Any] = {
            "citation": match.group(0).strip(),
            "title": raw_title,
            "article_ref": article_ref,
            "status": result.get("status", "invalid_title"),
        }
        if result.get("status") == "valid":
            item["resolved_title"] = result.get("title", "")
            item["content"] = (result.get("content") or "")[:160]
            item["issue"] = ""
        elif result.get("status") == "invalid_title":
            item["issue"] = f"法源《{raw_title}》未在本地法条库中匹配到，请核对法规名称。"
        else:
            item["issue"] = (
                f"《{raw_title}》中未找到{article_ref}，请核对条号"
                + ("（刑法历经多个修正案，条号可能已变化）。" if "刑" in raw_title else "。")
            )
            suggestion = _suggestion_for(raw_title, article_ref)
            if suggestion:
                item["suggestion"] = f"相近法条：{suggestion}"
        citations.append(item)
    return citations


def check_submission_citations(text: str) -> dict[str, Any] | None:
    """Instant-feedback entry point.

    Returns {"status": "ok", ...} when every citation verifies,
    {"status": "warn", ...} when some fail, and None when the
    submission contains no explicit citations to check.
    """
    if not instant_citation_enabled():
        return None
    if not corpus_available():
        logger.warning("[Teaching] citation corpus unavailable; instant check disabled")
        return None

    citations = extract_and_verify_citations(text)
    if not citations:
        return None
    invalid = [item for item in citations if item["status"] != "valid"]

    if not invalid:
        valid_refs = "、".join(item["citation"] for item in citations)
        return {
            "status": "ok",
            "messages": [f"{valid_refs}：法条引用核验通过"],
            "details": citations,
            "corpus": law_corpus.corpus_stats(),
        }

    messages = []
    for item in invalid:
        message = f"{item['citation']}：{item.get('issue', '未通过校验')}"
        if item.get("suggestion"):
            message += f"（{item['suggestion']}）"
        messages.append(message)

    return {
        "status": "warn",
        "messages": messages,
        "details": invalid,
        "corpus": law_corpus.corpus_stats(),
    }


def collect_law_citations(utterances: list[str]) -> list[dict[str, Any]]:
    """Aggregate citation verification across all student utterances (for scoring)."""
    results: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for text in utterances:
        for item in extract_and_verify_citations(text):
            key = (item.get("title") or "", item.get("article_ref") or "")
            if key in seen:
                continue
            seen.add(key)
            results.append(item)
    return results


__all__ = [
    "check_submission_citations",
    "collect_law_citations",
    "corpus_available",
    "extract_and_verify_citations",
    "instant_citation_enabled",
]
