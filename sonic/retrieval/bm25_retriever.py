# sonic/retrieval/bm25_retriever.py
"""
sonic/retrieval/bm25_retriever.py
----------------------------------------
BM25-based retriever. Requires: pip install rank_bm25

Uses the classic Okapi BM25 ranking function over each example's
title + description, tokenized on whitespace/lowercased. Much stronger
than difflib.SequenceMatcher for short technical text with proper nouns
(VLAN, BGP, SVI, switch names) since BM25 rewards exact term overlap
and down-weights common words across the corpus — no external model
or embedding server required, fully deterministic.

Same interface as every other retriever — swappable via factory.py.
"""

import re

from rank_bm25 import BM25Okapi

from sonic.retrieval.base import BaseRetriever

MIN_SCORE = 0.5   # BM25 scores are unbounded (not 0-1) — tune based on observed scores


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


class BM25Retriever(BaseRetriever):
    def _best_match(self, query: str, pool: list[dict]) -> dict | None:
        if not pool:
            return None

        corpus = [
            _tokenize(f"{ex.get('title', '')} {ex.get('description', '')} {ex.get('body', '')}")
            for ex in pool
        ]
        bm25 = BM25Okapi(corpus)
        query_tokens = _tokenize(query)
        scores = bm25.get_scores(query_tokens)

        best_idx = max(range(len(scores)), key=lambda i: scores[i])
        best_score = scores[best_idx]
        best_example = pool[best_idx]

        print(f"[BM25Retriever]   best: {best_example.get('resource', '?')} score={best_score:.3f}")

        if best_score < MIN_SCORE:
            return None
        return best_example

    def select(self, query: str, topology_str: str, mgmt_ips: dict, metadata_overview: list[dict]) -> dict:
        print(f"\n[BM25Retriever] Selecting for query: {query!r}")

        ansible_pool = [ex for ex in metadata_overview if ex.get("type", "").endswith("-ansible-example")]
        batfish_pool = [ex for ex in metadata_overview if ex.get("type", "").endswith("-batfish-example")]

        ansible_match = self._best_match(query, ansible_pool)
        batfish_match = self._best_match(query, batfish_pool)

        print(f"[BM25Retriever] Selected ansible: {ansible_match.get('resource') if ansible_match else None}")
        print(f"[BM25Retriever] Selected batfish: {batfish_match.get('resource') if batfish_match else None}")

        return {"ansible": ansible_match, "batfish": batfish_match}