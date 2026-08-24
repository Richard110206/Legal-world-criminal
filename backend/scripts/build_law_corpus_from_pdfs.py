"""Build legal corpus JSONL (刑法/刑诉法) from the provided PDFs.

Reads `中华人民共和国刑法.pdf` / `中华人民共和国刑诉法.pdf`, extracts text with
PyMuPDF, then reuses `prepare_law_corpus.build_documents_from_plaintext` to split
into per-article records in the schema consumed by `citation_check_tool`:

    {"document_id", "source_title", "category", "article_ref",
     "title", "content", "effective_date", "source_url"}

Output:
    backend/legal_corpus/processed/xingfa.jsonl
    backend/legal_corpus/processed/xingsufa.jsonl
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

# ensure backend root on sys.path so `scripts.prepare_law_corpus` imports resolve
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from scripts.prepare_law_corpus import build_documents_from_plaintext  # noqa: E402


FIRST_ARTICLE_PATTERN = re.compile(r"第[一二三四五六七八九十百千万零〇两\d]+条")
ARTICLE_REF_AT_LINE_START = re.compile(r"^第[一二三四五六七八九十百千万零〇两\d]+条")
SENTENCE_TERMINATOR = re.compile(r"[。！？；：]+[\u201d\u201c\'\"）)]*$")
STRUCTURE_HEADING = re.compile(
    r"^(?:第[一二三四五六七八九十百千万零〇两\d]+(?:编|章|节)|附则|附件[一二三四五六七八九十百千零〇\d]+)"
)
DEFAULT_PDFS = [
    {
        "path": "中华人民共和国刑法.pdf",
        "document_id": "xingfa",
        "source_title": "中华人民共和国刑法",
        "category": "刑法",
        "effective_date": "根据《刑法修正案（十一）》修正",
    },
    {
        "path": "中华人民共和国刑诉法.pdf",
        "document_id": "xingsufa",
        "source_title": "中华人民共和国刑事诉讼法",
        "category": "刑事诉讼法",
        "effective_date": "根据2018年10月26日第三次修正",
    },
]


def extract_pdf_text(pdf_path: Path) -> str:
    import pymupdf

    document = pymupdf.open(str(pdf_path))
    pages = [document[page_index].get_text("text") for page_index in range(len(document))]
    return "\n".join(pages)


def cut_before_first_article(text: str) -> str:
    """Drop preamble / TOC: keep only text from the first 第X条 onward."""
    match = FIRST_ARTICLE_PATTERN.search(text)
    if not match:
        return text
    return text[match.start() :]


def rejoin_inline_article_refs(text: str) -> str:
    """Merge line-start 第X条 back into the previous line when it is only an
    inline cross-reference (PDF line wraps mid-sentence), e.g.

        ...如果没有本法
        第七十七条规定的情形...

    A line-start 第X条 is treated as a *real* new-article header only when the
    previous non-blank line ends with a sentence terminator (。！？；：) or is a
    structure heading (第X编/章/节) — genuine law text always terminates the
    preceding article before the next header.
    """
    lines = [line.rstrip() for line in text.splitlines()]
    output: list[str] = []
    for index, line in enumerate(lines):
        if not ARTICLE_REF_AT_LINE_START.match(line):
            output.append(line)
            continue

        previous_index = len(output) - 1
        while previous_index >= 0 and not output[previous_index].strip():
            previous_index -= 1
        if previous_index < 0:
            output.append(line)
            continue

        previous = output[previous_index].strip()
        if SENTENCE_TERMINATOR.search(previous) or STRUCTURE_HEADING.match(previous):
            output.append(line)
            continue

        # mid-sentence wrap of a cross-reference → glue onto the previous line
        output[previous_index] = previous + line.strip()

    return "\n".join(output)


def build_corpus_record(source: dict[str, Any], article: dict[str, Any]) -> dict[str, Any]:
    return {
        "document_id": article["document_id"],
        "source_title": source["source_title"],
        "category": source["category"],
        "article_ref": article["article_ref"],
        "title": article["title"],
        "content": article["content"],
        "effective_date": source["effective_date"],
        "source_url": article.get("source_url", ""),
    }


def build_for_pdf(
    pdf_path: Path,
    *,
    document_id: str,
    source_title: str,
    category: str,
    effective_date: str,
    output_dir: Path,
) -> int:
    raw_text = extract_pdf_text(pdf_path)
    body_text = rejoin_inline_article_refs(cut_before_first_article(raw_text))

    source = {
        "document_id": document_id,
        "source_title": source_title,
        "title": source_title,
        "category": category,
        "effective_date": effective_date,
        "source_url": "",
    }
    articles = build_documents_from_plaintext(
        source_document_id=document_id,
        source_title=source_title,
        category=category,
        effective_date=effective_date,
        source_url="",
        text=body_text,
    )

    output_path = output_dir / f"{document_id}.jsonl"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as handle:
        for article in articles:
            handle.write(
                json.dumps(build_corpus_record(source, article), ensure_ascii=False) + "\n"
            )

    # sanity: article_refs must be unique within a document
    refs = [article["article_ref"] for article in articles]
    duplicates = sorted({ref for ref in refs if refs.count(ref) > 1})
    if duplicates:
        print(f"  WARNING duplicate article_refs in {document_id}: {duplicates[:10]}")

    print(
        f"{document_id}: {len(articles)} articles -> {output_path} "
        f"(chars={sum(len(a['content']) for a in articles)})"
    )
    return len(articles)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pdf-dir",
        default=r"E:\大二下活动\【揭榜挂帅】法律一流学科建设\数据集",
        help="Directory containing the law PDFs",
    )
    parser.add_argument(
        "--output-dir",
        default=str(BACKEND_ROOT / "legal_corpus" / "processed"),
        help="Directory to write processed JSONL",
    )
    args = parser.parse_args()

    pdf_dir = Path(args.pdf_dir)
    output_dir = Path(args.output_dir)

    total = 0
    for source in DEFAULT_PDFS:
        pdf_path = pdf_dir / source["path"]
        if not pdf_path.exists():
            print(f"SKIP (not found): {pdf_path}")
            continue
        total += build_for_pdf(
            pdf_path,
            document_id=source["document_id"],
            source_title=source["source_title"],
            category=source["category"],
            effective_date=source["effective_date"],
            output_dir=output_dir,
        )

    print(f"TOTAL articles: {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
