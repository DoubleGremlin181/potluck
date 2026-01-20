"""Base ranker protocol for result fusion and scoring."""

from abc import ABC, abstractmethod

from potluck.search.dtos import RankingConfig, RetrievalResult, SearchResultItem


class Ranker(ABC):
    """Abstract base class for result rankers.

    Rankers take results from multiple retrievers and produce a single
    ranked list using some fusion algorithm (RRF, weighted combination, etc.).
    """

    @abstractmethod
    def rank(
        self,
        results: dict[str, list[RetrievalResult]],
        config: RankingConfig,
    ) -> list[SearchResultItem]:
        """Rank and merge results from multiple retrievers.

        Args:
            results: Dict mapping retriever names to their results.
                     Keys are typically "fts" and "vector".
            config: Ranking configuration (weights, RRF k, etc.).

        Returns:
            List of ranked search results, ordered by combined score.
        """
