# sonic/retrieval/embedding_retriever.py
"""
sonic/retrieval/embedding_retriever.py
----------------------------------------
Real embedding-based retriever using LM Studio's OpenAI-compatible
/v1/embeddings endpoint with text-embedding-nomic-embed-text-v1.5.

Caches example embeddings on disk (keyed by resource path + text hash)
so we don't re-embed the whole example pool on every single query —
only the query itself gets embedded fresh each call. Cache invalidates
automatically if an example's title/description text changes.

Same interface as every other retriever — swappable via factory.py.
"""

import hashlib
import json
import math
from pathlib import Path

import requests

from sonic.retrieval.base import BaseRetriever
from sonic.config.settings import LMSTUDIO_BASE_URL

EMBEDDING_MODEL = "text-embedding-nomic-embed-text-v1.5"
MIN_SIMILARITY = 0.5   # cosine similarity, 0-1 range — tune based on observed scores

CACHE_PATH = Path(__file__).parent / ".embedding_cache.json"


def _text_for(example: dict) -> str:
    return f"{example.get('title', '')} {example.get('description', '')} {example.get('body', '')}".strip()


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _embed(texts: list[str]) -> list[list[float]]:
    """Calls LM Studio's /v1/embeddings endpoint for a batch of texts."""
    resp = requests.post(
        f"{LMSTUDIO_BASE_URL}/embeddings",
        json={"model": EMBEDDING_MODEL, "input": texts},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()["data"]
    # LM Studio returns results in the same order as input, but sort by index to be safe
    data.sort(key=lambda d: d["index"])
    return [d["embedding"] for d in data]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class EmbeddingRetriever(BaseRetriever):
    def __init__(self):
        self._cache = self._load_cache()

    def _load_cache(self) -> dict:
        if CACHE_PATH.exists():
            try:
                return json.loads(CACHE_PATH.read_text())
            except Exception:
                return {}
        return {}

    def _save_cache(self):
        CACHE_PATH.write_text(json.dumps(self._cache))

    def _get_embeddings_for_pool(self, pool: list[dict]) -> list[list[float]]:
        """
        Returns embeddings for each example in pool, using cache where
        possible and batch-embedding only the ones that changed/are new.
        """
        to_embed_texts = []
        to_embed_idx = []
        results = [None] * len(pool)

        for i, ex in enumerate(pool):
            text = _text_for(ex)
            key = f"{ex.get('resource', '?')}::{_hash(text)}"
            if key in self._cache:
                results[i] = self._cache[key]
            else:
                to_embed_texts.append(text)
                to_embed_idx.append((i, key))

        if to_embed_texts:
            print(f"[EmbeddingRetriever] Embedding {len(to_embed_texts)} new/changed examples...")
            new_vectors = _embed(to_embed_texts)
            for (i, key), vec in zip(to_embed_idx, new_vectors):
                results[i] = vec
                self._cache[key] = vec
            self._save_cache()

        return results

    def _best_match(self, query_vec: list[float], pool: list[dict]) -> dict | None:
        if not pool:
            return None

        pool_vecs = self._get_embeddings_for_pool(pool)
        scored = [(_cosine(query_vec, vec), ex) for vec, ex in zip(pool_vecs, pool)]
        scored.sort(key=lambda pair: pair[0], reverse=True)

        best_score, best_example = scored[0]
        print(f"[EmbeddingRetriever]   best: {best_example.get('resource', '?')} score={best_score:.3f}")

        if best_score < MIN_SIMILARITY:
            return None
        return best_example

    def select(self, query: str, topology_str: str, mgmt_ips: dict, metadata_overview: list[dict]) -> dict:
        print(f"\n[EmbeddingRetriever] Selecting for query: {query!r}")

        query_vec = _embed([query])[0]

        ansible_pool = [ex for ex in metadata_overview if ex.get("type", "").endswith("-ansible-example")]
        batfish_pool = [ex for ex in metadata_overview if ex.get("type", "").endswith("-batfish-example")]

        ansible_match = self._best_match(query_vec, ansible_pool)
        batfish_match = self._best_match(query_vec, batfish_pool)

        print(f"[EmbeddingRetriever] Selected ansible: {ansible_match.get('resource') if ansible_match else None}")
        print(f"[EmbeddingRetriever] Selected batfish: {batfish_match.get('resource') if batfish_match else None}")

        return {"ansible": ansible_match, "batfish": batfish_match}