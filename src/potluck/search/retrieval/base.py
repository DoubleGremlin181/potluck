"""Base retriever protocol for search backends."""

from abc import ABC, abstractmethod
from datetime import datetime

from sqlmodel import Session

from potluck.models.base import EntityType
from potluck.search.dtos import RetrievalResult


class Retriever(ABC):
    """Abstract base class for search retrievers.

    Retrievers are responsible for fetching candidate results from a specific
    backend (FTS, vector similarity, etc.). They return ranked results with
    scores that can be combined by a ranker.
    """

    @abstractmethod
    def retrieve(
        self,
        session: Session,
        query: str,
        entity_types: set[EntityType],
        limit: int,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[RetrievalResult]:
        """Retrieve candidate results for a query.

        Args:
            session: Database session.
            query: Search query string.
            entity_types: Entity types to search across.
            limit: Maximum number of results per entity type.
            since: Only return results after this datetime.
            until: Only return results before this datetime.

        Returns:
            List of retrieval results ordered by score (highest first).
        """
