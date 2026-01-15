"""Search retrieval layer - candidate retrieval from various backends."""

from potluck.search.retrieval.base import Retriever
from potluck.search.retrieval.fts import FTSRetriever
from potluck.search.retrieval.vector import VectorRetriever

__all__ = [
    "Retriever",
    "FTSRetriever",
    "VectorRetriever",
]
