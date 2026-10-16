"""
sonic/retrieval/keyword_retriever.py
----------------------------------------
Zero-dependency retriever for sanity-checking the retrieval interface
and pipeline wiring BEFORE spending time on embedding model setup.

Uses stdlib difflib.SequenceMatcher for a crude text-similarity score —
not semantically meaningful (won't understand synonyms or paraphrasing
the way embeddings would), but proves the plumbing: pool-splitting by
type, per-query selection, MIN_SIMILARITY threshold -> None behavior,
and the exact same return shape EmbeddingRetriever will produce.

Swap this for EmbeddingRetriever later by changing ONE import line in
run_pipeline.py — nothing else changes. That's the whole point of the
BaseRetriever interface.
"""

from difflib import SequenceMatcher

from sonic.retrieval.base import BaseRetriever

MIN_SIMILARITY = 0.15   # much lower than embedding threshold — difflib scores run lower


class KeywordRetriever(BaseRetriever):
    def _score(self, query: str, example: dict) -> float:
        text = f"{example.get('title', '')} {example.get('description', '')} {example.get('body', '')}".lower()
        return SequenceMatcher(None, query.lower(), text).ratio()

    def _best_match(self, query: str, pool: list[dict]) -> dict | None:
        if not pool:
            return None

        scored = [(self._score(query, ex), ex) for ex in pool]
        scored.sort(key=lambda pair: pair[0], reverse=True)

        best_score, best_example = scored[0]
        print(f"[KeywordRetriever]   best: {best_example.get('resource', '?')} score={best_score:.3f}")

        if best_score < MIN_SIMILARITY:
            return None
        return best_example

    def select(self, query: str, topology_str: str, mgmt_ips: dict, metadata_overview: list[dict]) -> dict:
        print(f"\n[KeywordRetriever] Selecting for query: {query!r}")

        ansible_pool = [ex for ex in metadata_overview if ex.get("type", "").endswith("-ansible-example")]
        batfish_pool = [ex for ex in metadata_overview if ex.get("type", "").endswith("-batfish-example")]

        ansible_match = self._best_match(query, ansible_pool)
        batfish_match = self._best_match(query, batfish_pool)

        print(f"[KeywordRetriever] Selected ansible: {ansible_match.get('resource') if ansible_match else None}")
        print(f"[KeywordRetriever] Selected batfish: {batfish_match.get('resource') if batfish_match else None}")

        return {"ansible": ansible_match, "batfish": batfish_match}