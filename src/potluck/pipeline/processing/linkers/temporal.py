"""Temporal linker for time-based entity relationships.

Creates SAME_TIME links between entities that occurred within a specified
time window of each other.
"""

from typing import ClassVar
from uuid import UUID

from sqlmodel import Session, select

from potluck.core.logging import get_logger
from potluck.models import get_entity_type_model_map
from potluck.models.base import EntityType
from potluck.models.links import EntityLink, LinkType
from potluck.pipeline.processing.linkers.base import BaseLinker

logger = get_logger(__name__)


# Default time window for "same time" links (60 seconds)
DEFAULT_TIME_WINDOW_SECONDS = 60


class TemporalLinker(BaseLinker):
    """Linker for time-based relationships between entities.

    Creates SAME_TIME links between entities that have `occurred_at` timestamps
    within a configurable time window of each other.

    Confidence is calculated as:
        confidence = 1.0 - (time_diff_seconds / window_seconds)

    So entities that occurred at exactly the same time get confidence=1.0,
    and entities at the edge of the window get confidence near 0.
    """

    NAME: ClassVar[str] = "temporal"
    LINK_TYPES: ClassVar[set[LinkType]] = {LinkType.SAME_TIME}

    def __init__(
        self,
        *,
        time_window_seconds: float = DEFAULT_TIME_WINDOW_SECONDS,
        min_confidence: float = 0.5,
    ) -> None:
        """Initialize the temporal linker.

        Args:
            time_window_seconds: Maximum time difference in seconds to create links.
            min_confidence: Minimum confidence threshold (links below this are skipped).
        """
        self._time_window_seconds = time_window_seconds
        self._min_confidence = min_confidence

    def find_links(
        self,
        session: Session,
        entity_type: EntityType,
        entity_ids: list[UUID],
    ) -> list[EntityLink]:
        """Find temporal links between entities of the same type.

        Args:
            session: Database session.
            entity_type: Type of entities to analyze.
            entity_ids: List of entity IDs.

        Returns:
            List of SAME_TIME EntityLink records.
        """
        if len(entity_ids) < 2:
            return []

        # Get the model class for this entity type
        model_map = get_entity_type_model_map()
        model_class = model_map.get(entity_type)
        if model_class is None:
            logger.warning(f"No model class found for entity type: {entity_type}")
            return []

        # Check if this model has an occurred_at field
        if not hasattr(model_class, "occurred_at"):
            logger.debug(f"Entity type {entity_type} has no occurred_at field, skipping")
            return []

        # Fetch entities with timestamps
        stmt = (
            select(model_class)
            .where(model_class.id.in_(entity_ids))  # type: ignore[attr-defined]
            .where(model_class.occurred_at.isnot(None))  # type: ignore[attr-defined]
        )
        result = session.execute(stmt)
        entities = list(result.scalars().all())

        if len(entities) < 2:
            return []

        # Sort by timestamp
        entities.sort(key=lambda e: e.occurred_at)

        # Find pairs within the time window
        links: list[EntityLink] = []

        for i, entity_a in enumerate(entities):
            for entity_b in entities[i + 1 :]:
                time_a = entity_a.occurred_at
                time_b = entity_b.occurred_at
                time_diff = abs((time_b - time_a).total_seconds())

                if time_diff > self._time_window_seconds:
                    # Since entities are sorted, all subsequent will be further
                    break

                # Calculate confidence based on time proximity
                confidence = 1.0 - (time_diff / self._time_window_seconds)

                if confidence >= self._min_confidence:
                    links.append(
                        EntityLink(
                            source_type=entity_type,
                            source_id=entity_a.id,
                            target_type=entity_type,
                            target_id=entity_b.id,
                            link_type=LinkType.SAME_TIME,
                            confidence=confidence,
                        )
                    )

        logger.debug(f"Found {len(links)} temporal links for {entity_type}")
        return links
