# -*- coding: utf-8 -*-
"""Build the statute embedding index for hybrid retrieval.

Embeds every article of legal_corpus/processed/*.jsonl via the configured
embedding endpoint (text-embedding-v4 by default) and persists the matrix.

Run:  cd backend && .venv\\Scripts\\python.exe -X utf8 scripts\\build_law_embeddings.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from dotenv import load_dotenv

load_dotenv(BACKEND_ROOT / ".env")


def main() -> int:
    from src.teaching import law_corpus, law_embedding

    if not law_embedding.embedding_available():
        print("[FAIL] EMBEDDING_API_KEY 未配置（.env）")
        return 1

    records = law_corpus._load_corpus_records()
    if not records:
        print("[FAIL] 法条库为空，先跑 build_law_corpus_from_pdfs.py")
        return 1

    texts = [
        f"{r['_source_title']} {r['_article_ref']} {r['_content'][:500]}"
        for r in records
    ]
    print(f"[PLAN] 嵌入 {len(texts)} 条法条（模型 {law_embedding.embedding_config()['model']}）…")
    manifest = law_embedding.build_index(texts)
    print(f"[DONE] {manifest['count']} 条 × {manifest['dim']} 维，corpus_hash={manifest['corpus_hash']}")

    # quick sanity: semantic query should rank the right article on top
    law_embedding.reset_vector_index()
    index = law_embedding.get_vector_index()
    if not index.available:
        print("[WARN] 索引构建后未能加载，请检查日志")
        return 1
    sims = index.similarity_scores("喝酒之后开车把人撞死了怎么判")
    if sims:
        best = max(range(len(sims)), key=lambda i: sims[i])
        print(f"[SANITY] 语义查询命中：{records[best]['_source_title']} {records[best]['_article_ref']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
