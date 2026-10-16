"""
sonic/retrieval/base.py
--------------------------
Interface every retriever must implement. Swappable by design — the
pipeline only ever calls .select(), never cares HOW a retriever decides.
Today: embedding similarity (embedding_retriever.py). Could later be
TF-IDF, BM25, a fine-tuned classifier, or anything else — as long as it
implements this same shape, nothing else in the pipeline needs to change.
"""

from abc import ABC, abstractmethod


class BaseRetriever(ABC):
    @abstractmethod
    def select(
        self,
        query: str,
        topology_str: str,
        mgmt_ips: dict,
        metadata_overview: list[dict],
    ) -> dict:
        """
        Args:
          query             : natural language config request
          topology_str      : static topology text (node names, links)
          mgmt_ips          : {switch_name: ip} for the CURRENT topology
                               (not used for matching today, but passed
                               through in case a future retriever wants
                               topology-shape-aware matching, e.g. switch
                               count, portchannel presence)
          metadata_overview : full list of example dicts, as returned by
                               metadata_loader.load_metadata_overview()

        Returns:
          {
            "ansible": <one metadata dict from metadata_overview, or None>,
            "batfish": <one metadata dict from metadata_overview, or None>,
          }
          None on either key means "no good enough match" — same semantics
          as the old planner's null selected_*_example fields.
        """
        raise NotImplementedError