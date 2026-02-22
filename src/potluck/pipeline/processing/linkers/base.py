"""Base class for entity linkers.

Linkers operate on batches of entities after import completes to create
EntityLink records between related entities. Unlike processors which
operate on individual entities, linkers analyze relationships across
entities in a batch.
"""

from abc import ABC, abstractmethod
from collections.abc import Iterator
from typing import Any, ClassVar
from uuid import UUID

from sqlmodel import Session

from potluck.core.logging import get_logger
from potluck.models.base import EntityType
from potluck.models.links import EntityLink, LinkType

logger = get_logger(__name__)

# Number of links to accumulate before committing to the database
PERSIST_BATCH_SIZE = 1000


class BaseLinker(ABC):
    """Abstract base class for entity linkers.

    Linkers analyze batches of entities and create EntityLink records for
    entities that are related (temporally, spatially, semantically, etc.).

    Linkers run after import completes, not during individual entity
    processing. They compare entities pairwise within the batch to find
    relationships.

    Attributes:
        NAME: Unique identifier for this linker.
        VERSION: Version string for tracking linker changes.
        LINK_TYPES: Set of LinkType values this linker creates.
        SUPPORTED_ENTITY_TYPES: Entity types this linker operates on.
    """

    NAME: ClassVar[str]  # Must be set by subclasses
    VERSION: ClassVar[str] = "1.0.0"
    LINK_TYPES: ClassVar[set[LinkType]]  # Must be set by subclasses
    SUPPORTED_ENTITY_TYPES: ClassVar[set[EntityType]]  # Must be set by subclasses

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Validate required class attributes on subclass definition."""
        super().__init_subclass__(**kwargs)

        # Skip validation for abstract classes
        if getattr(cls, "__abstractmethods__", None):
            return

        if not hasattr(cls, "NAME") or not cls.NAME:
            raise TypeError(f"{cls.__name__} must define NAME class attribute")

        if not hasattr(cls, "LINK_TYPES"):
            raise TypeError(f"{cls.__name__} must define LINK_TYPES class attribute")

        if not hasattr(cls, "SUPPORTED_ENTITY_TYPES"):
            raise TypeError(f"{cls.__name__} must define SUPPORTED_ENTITY_TYPES class attribute")

    @abstractmethod
    def find_links(
        self,
        session: Session,
        entity_type: EntityType,
        entity_ids: list[UUID],
    ) -> Iterator[EntityLink]:
        """Find links between entities of a given type.

        This method is called with a batch of entity IDs from an import.
        The linker should analyze these entities and yield EntityLink
        records for related pairs.

        Args:
            session: Database session for querying entities.
            entity_type: Type of entities in this batch.
            entity_ids: List of entity IDs to analyze.

        Yields:
            EntityLink records to persist.
        """
        ...

    def persist_links(self, session: Session, links: Iterator[EntityLink]) -> int:
        """Persist links from an iterator to the database in batches.

        Consumes the iterator and commits every PERSIST_BATCH_SIZE links
        to keep memory bounded.

        Args:
            session: Database session.
            links: Iterator of EntityLink records to persist.

        Returns:
            Number of links actually persisted.
        """
        persisted = 0
        batch_count = 0

        for link in links:
            link.linker_name = self.NAME
            link.linker_version = self.VERSION
            link.is_automatic = True
            session.add(link)
            persisted += 1
            batch_count += 1

            if batch_count >= PERSIST_BATCH_SIZE:
                session.commit()
                logger.debug(f"{self.NAME} committed {persisted} links so far")
                batch_count = 0

        # Commit any remaining links
        if batch_count > 0:
            session.commit()

        if persisted > 0:
            logger.info(f"{self.NAME} created {persisted} links")

        return persisted

    def run(
        self,
        session: Session,
        entity_type: EntityType,
        entity_ids: list[UUID],
    ) -> dict[str, Any]:
        """Run the linker on a batch of entities of a single type.

        Args:
            session: Database session.
            entity_type: Type of entities to link.
            entity_ids: List of entity IDs to analyze.

        Returns:
            Dict with linker statistics.
        """
        logger.info(f"Running {self.NAME} on {len(entity_ids)} {entity_type.value} entities")

        links = self.find_links(session, entity_type, entity_ids)
        persisted = self.persist_links(session, links)

        return {
            "linker_name": self.NAME,
            "linker_version": self.VERSION,
            "entity_type": entity_type.value,
            "entities_analyzed": len(entity_ids),
            "links_persisted": persisted,
        }
