"""Embedding layer for hybrid statute retrieval (BM25 + dense vectors).

Backed by the Aliyun MaaS OpenAI-compatible endpoint:
    POST {EMBEDDING_BASE_URL}/embeddings   {"model": "text-embedding-v4", "input": [...]}

Index artifacts (per corpus content hash, so stale vectors never mismatch):
    legal_corpus/processed/law_embeddings.float16.npy   (N × D matrix)
    legal_corpus/processed/law_embeddings.manifest.json (model/dim/hash)

Query vectors are computed online (one small request per search); document
vectors are built once by `build_index()` and cached on disk.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

LEGAL_CORPUS_DIR = Path(__file__).resolve().parents[2] / "legal_corpus" / "processed"

INDEX_VECTOR_FILENAME = "law_embeddings.float16.npy"
INDEX_MANIFEST_FILENAME = "law_embeddings.manifest.json"

_BATCH_SIZE = 10  # texts per embeddings request (this endpoint rejects >10)
_REQUEST_TIMEOUT = 60
_MAX_ATTEMPTS = 3


def embedding_config() -> dict[str, str]:
    """Resolve embedding endpoint config via the centralised settings layer."""
    from ..config import get_embedding_settings

    cfg = get_embedding_settings()
    return {
        "api_key": cfg.api_key.strip(),
        "base_url": cfg.base_url.strip().rstrip("/"),
        "model": cfg.model_name.strip() or "text-embedding-v4",
    }


def embedding_available() -> bool:
    return bool(embedding_config()["api_key"])


def _embed_texts(texts: list[str], *, text_type: str = "document") -> list[list[float]]:
    """Call the embeddings endpoint; returns one vector per text. Raises on failure."""
    import requests

    cfg = embedding_config()
    if not cfg["api_key"]:
        raise RuntimeError("EMBEDDING_API_KEY not configured")

    vectors: list[list[float]] = []
    for start in range(0, len(texts), _BATCH_SIZE):
        batch = texts[start : start + _BATCH_SIZE]
        last_error: Exception | None = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                response = requests.post(
                    f"{cfg['base_url']}/embeddings",
                    headers={"Authorization": f"Bearer {cfg['api_key']}"},
                    json={"model": cfg["model"], "input": batch, "text_type": text_type},
                    timeout=_REQUEST_TIMEOUT,
                )
                response.raise_for_status()
                payload = response.json()
                data = payload.get("data") or []
                if len(data) != len(batch):
                    raise RuntimeError(f"embedding count mismatch: {len(data)} != {len(batch)}")
                vectors.extend(item["embedding"] for item in data)
                last_error = None
                break
            except Exception as exc:
                last_error = exc
                time.sleep(min(2**attempt, 8))
        if last_error is not None:
            raise RuntimeError(f"embeddings request failed: {last_error}")
    return vectors


def embed_query(query: str) -> list[list[float]]:
    return _embed_texts([str(query or "")[:1000]], text_type="query")


def _corpus_content_hash(corpus_dir: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(corpus_dir.glob("*.jsonl")):
        digest.update(path.name.encode("utf-8"))
        digest.update(str(path.stat().st_size).encode("utf-8"))
        digest.update(str(int(path.stat().st_mtime)).encode("utf-8"))
    return digest.hexdigest()[:16]


def build_index(embed_texts_for: list[str]) -> dict[str, Any]:
    """Build & persist document vectors for the given texts (corpus order)."""
    import numpy as np

    LEGAL_CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    vectors = _embed_texts(embed_texts_for, text_type="document")
    matrix = np.asarray(vectors, dtype=np.float16)
    vector_path = LEGAL_CORPUS_DIR / INDEX_VECTOR_FILENAME
    manifest_path = LEGAL_CORPUS_DIR / INDEX_MANIFEST_FILENAME
    cfg = embedding_config()
    manifest = {
        "model": cfg["model"],
        "dim": int(matrix.shape[1]),
        "count": int(matrix.shape[0]),
        "corpus_hash": _corpus_content_hash(LEGAL_CORPUS_DIR),
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    np.save(vector_path, matrix)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    logger.info(
        "[LawEmbedding] index built: %d docs × %ddim -> %s",
        matrix.shape[0], matrix.shape[1], vector_path,
    )
    return manifest


class LawVectorIndex:
    """Lazy-loaded on-disk vector index aligned with the corpus records order."""

    def __init__(self) -> None:
        self.available = False
        self.model = ""
        self.dim = 0
        self.matrix = None
        self._load()

    def _load(self) -> None:
        if not embedding_available():
            return
        vector_path = LEGAL_CORPUS_DIR / INDEX_VECTOR_FILENAME
        manifest_path = LEGAL_CORPUS_DIR / INDEX_MANIFEST_FILENAME
        if not vector_path.exists() or not manifest_path.exists():
            return
        try:
            import numpy as np

            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            current_hash = _corpus_content_hash(LEGAL_CORPUS_DIR)
            if manifest.get("corpus_hash") != current_hash:
                logger.info("[LawEmbedding] index stale (corpus changed), ignoring")
                return
            if manifest.get("model") != embedding_config()["model"]:
                logger.info("[LawEmbedding] index built for different model, ignoring")
                return
            self.matrix = np.load(vector_path).astype(np.float32)
            self.dim = int(self.matrix.shape[1])
            self.model = str(manifest.get("model") or "")
            self.available = True
            logger.info("[LawEmbedding] index loaded: %s", manifest)
        except Exception as exc:
            logger.warning("[LawEmbedding] failed to load index: %s", exc)

    def similarity_scores(self, query: str) -> list[float] | None:
        """Cosine similarity of query vs every doc; None when unavailable/failing."""
        if not self.available or self.matrix is None:
            return None
        try:
            import numpy as np

            qvec = embed_query(query)[0]
            q = np.asarray(qvec, dtype=np.float32)
            q_norm = np.linalg.norm(q)
            if q_norm <= 0:
                return None
            sims = (self.matrix @ q) / (
                np.linalg.norm(self.matrix, axis=1) * q_norm + 1e-12
            )
            return sims.tolist()
        except Exception as exc:
            logger.warning("[LawEmbedding] query embed failed: %s", exc)
            return None


_INDEX_SINGLETON: LawVectorIndex | None = None


def get_vector_index() -> LawVectorIndex:
    global _INDEX_SINGLETON
    if _INDEX_SINGLETON is None:
        _INDEX_SINGLETON = LawVectorIndex()
    return _INDEX_SINGLETON


def reset_vector_index() -> None:
    global _INDEX_SINGLETON
    _INDEX_SINGLETON = None


__all__ = [
    "build_index",
    "embedding_available",
    "embedding_config",
    "get_vector_index",
    "reset_vector_index",
]
