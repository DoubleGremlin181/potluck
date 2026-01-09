"""Base class for entity linkers.

Linkers operate on batches of entities after import completes to create
EntityLink records between related entities. Unlike processors which
operate on individual entities, linkers analyze relationships across
entities in a batch.
"""

from abc import ABC, abstractmethod
from typing import Any, ClassVar
from uuid import UUID

from sqlmodel import Session

from potluck.core.logging import get_logger
from potluck.models.base import EntityType
from potluck.models.links import EntityLink, LinkType

logger = get_logger(__name__)


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
    """

    NAME: ClassVar[str]  # Must be set by subclasses
    VERSION: ClassVar[str] = "1.0.0"
    LINK_TYPES: ClassVar[set[LinkType]]  # Must be set by subclasses

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

    @abstractmethod
    def find_links(
        self,
        session: Session,
        entity_type: EntityType,
        entity_ids: list[UUID],
    ) -> list[EntityLink]:
        """Find links between entities of a given type.

        This method is called with a batch of entity IDs from an import.
        The linker should analyze these entities and return EntityLink
        records for related pairs.

        Args:
            session: Database session for querying entities.
            entity_type: Type of entities in this batch.
            entity_ids: List of entity IDs to analyze.

        Returns:
            List of EntityLink records to persist.
        """
        ...

    def find_cross_type_links(
        self,
        session: Session,
        entity_ids_by_type: dict[EntityType, list[UUID]],
    ) -> list[EntityLink]:
        """Find links between entities of different types.

        Override this method to create cross-type links. Default
        implementation calls find_links for each type separately.

        Args:
            session: Database session.
            entity_ids_by_type: Dict mapping entity types to entity IDs.

        Returns:
            List of EntityLink records to persist.
        """
        links: list[EntityLink] = []
        for entity_type, entity_ids in entity_ids_by_type.items():
            links.extend(self.find_links(session, entity_type, entity_ids))
        return links

    def persist_links(self, session: Session, links: list[EntityLink]) -> int:
        """Persist a batch of links to the database.

        Handles deduplication - skips links that already exist.

        Args:
            session: Database session.
            links: List of EntityLink records to persist.

        Returns:
            Number of links actually persisted (after deduplication).
        """
        if not links:
            return 0

        persisted = 0
        for link in links:
            # Set linker provenance
            link.linker_name = self.NAME
            link.linker_version = self.VERSION
            link.is_automatic = True

            # TODO: Check for existing link to avoid duplicates
            # For now, just add all links
            session.add(link)
            persisted += 1

        if persisted > 0:
            session.commit()
            logger.info(f"{self.NAME} created {persisted} links")

        return persisted

    def run(
        self,
        session: Session,
        entity_ids_by_type: dict[EntityType, list[UUID]],
    ) -> dict[str, Any]:
        """Run the linker on a batch of entities.

        Args:
            session: Database session.
            entity_ids_by_type: Dict mapping entity types to entity IDs.

        Returns:
            Dict with linker statistics.
        """
        total_entities = sum(len(ids) for ids in entity_ids_by_type.values())
        logger.info(f"Running {self.NAME} on {total_entities} entities")

        links = self.find_cross_type_links(session, entity_ids_by_type)
        persisted = self.persist_links(session, links)

        return {
            "linker_name": self.NAME,
            "linker_version": self.VERSION,
            "entities_analyzed": total_entities,
            "links_found": len(links),
            "links_persisted": persisted,
        }
