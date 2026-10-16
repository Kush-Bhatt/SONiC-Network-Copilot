# sonic/retrieval/factory.py
"""
sonic/retrieval/factory.py
----------------------------------------
Single entrypoint every pipeline consumer uses to get a retriever.
Swapping retrievers (keyword / bm25 / embedding / future ones) is a
ONE-LINE change here — nothing in run_pipeline.py, run.py, harnesses,
or anywhere else needs to change.

Controlled by RETRIEVER_TYPE in sonic/config/settings.py (or env var RETRIEVER_TYPE), 
defaulting to "bm25".
"""

import os

from sonic.retrieval.base import BaseRetriever
from sonic.config.settings import RETRIEVER_TYPE

_RETRIEVER_TYPE = RETRIEVER_TYPE.strip().lower()


def get_retriever() -> BaseRetriever:
    """
    Returns an instance of the currently configured retriever.
    Valid values for RETRIEVER_TYPE: "keyword", "bm25", "embedding".
    """
    if _RETRIEVER_TYPE == "keyword":
        from sonic.retrieval.keyword_retriever import KeywordRetriever
        return KeywordRetriever()

    if _RETRIEVER_TYPE == "bm25":
        from sonic.retrieval.bm25_retriever import BM25Retriever
        return BM25Retriever()

    if _RETRIEVER_TYPE == "embedding":
        from sonic.retrieval.embedding_retriever import EmbeddingRetriever
        return EmbeddingRetriever()

    raise ValueError(
        f"Unknown RETRIEVER_TYPE={_RETRIEVER_TYPE!r}. "
        f"Valid options: keyword, bm25, embedding"
    )