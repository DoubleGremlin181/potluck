"""Spatial linker for location-based entity relationships.

Creates SAME_LOCATION and NEAR links between entities based on their
geographic coordinates.
"""

import math
from typing import ClassVar
from uuid import UUID

from sqlmodel import Session, select

from potluck.core.logging import get_logger
from potluck.models import get_entity_type_model_map
from potluck.models.base import EntityType
from potluck.models.links import EntityLink, LinkType
from potluck.pipeline.processing.linkers.base import BaseLinker

logger = get_logger(__name__)


# Default distance thresholds in meters
DEFAULT_SAME_LOCATION_METERS = 50  # Within 50m = same location
DEFAULT_NEAR_METERS = 500  # Within 500m = near


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate the great-circle distance between two points on Earth.

    Uses the Haversine formula for accurate distance calculation.

    Args:
        lat1: Latitude of first point in degrees.
        lon1: Longitude of first point in degrees.
        lat2: Latitude of second point in degrees.
        lon2: Longitude of second point in degrees.

    Returns:
        Distance in meters.
    """
    # Earth's radius in meters
    earth_radius = 6371000

    # Convert to radians
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    # Haversine formula
    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return earth_radius * c


class SpatialLinker(BaseLinker):
    """Linker for location-based relationships between entities.

    Creates:
    - SAME_LOCATION links for entities within a small distance (default 50m)
    - NEAR links for entities within a larger distance (default 500m)

    Only processes entities that have latitude and longitude fields.
    """

    NAME: ClassVar[str] = "spatial"
    LINK_TYPES: ClassVar[set[LinkType]] = {LinkType.SAME_LOCATION, LinkType.NEAR}

    def __init__(
        self,
        *,
        same_location_meters: float = DEFAULT_SAME_LOCATION_METERS,
        near_meters: float = DEFAULT_NEAR_METERS,
    ) -> None:
        """Initialize the spatial linker.

        Args:
            same_location_meters: Maximum distance in meters for SAME_LOCATION links.
            near_meters: Maximum distance in meters for NEAR links.
        """
        self._same_location_meters = same_location_meters
        self._near_meters = near_meters

    def find_links(
        self,
        session: Session,
        entity_type: EntityType,
        entity_ids: list[UUID],
    ) -> list[EntityLink]:
        """Find spatial links between entities of the same type.

        Args:
            session: Database session.
            entity_type: Type of entities to analyze.
            entity_ids: List of entity IDs.

        Returns:
            List of SAME_LOCATION and NEAR EntityLink records.
        """
        if len(entity_ids) < 2:
            return []

        # Get the model class for this entity type
        model_map = get_entity_type_model_map()
        model_class = model_map.get(entity_type)
        if model_class is None:
            logger.warning(f"No model class found for entity type: {entity_type}")
            return []

        # Check if this model has latitude/longitude fields
        if not hasattr(model_class, "latitude") or not hasattr(model_class, "longitude"):
            logger.debug(f"Entity type {entity_type} has no lat/lon fields, skipping")
            return []

        # Fetch entities with coordinates
        stmt = (
            select(model_class)
            .where(model_class.id.in_(entity_ids))  # type: ignore[attr-defined]
            .where(model_class.latitude.isnot(None))  # type: ignore[attr-defined]
            .where(model_class.longitude.isnot(None))  # type: ignore[attr-defined]
        )
        result = session.exec(stmt)
        entities = list(result.all())

        if len(entities) < 2:
            return []

        # Find pairs within distance thresholds
        links: list[EntityLink] = []

        for i, entity_a in enumerate(entities):
            lat_a = entity_a.latitude  # type: ignore[attr-defined]
            lon_a = entity_a.longitude  # type: ignore[attr-defined]

            for entity_b in entities[i + 1 :]:
                lat_b = entity_b.latitude  # type: ignore[attr-defined]
                lon_b = entity_b.longitude  # type: ignore[attr-defined]

                distance = haversine_distance(lat_a, lon_a, lat_b, lon_b)

                if distance <= self._same_location_meters:
                    # SAME_LOCATION - very close
                    # Confidence inversely proportional to distance
                    confidence = 1.0 - (distance / self._same_location_meters)
                    links.append(
                        EntityLink(
                            source_type=entity_type,
                            source_id=entity_a.id,  # type: ignore[attr-defined]
                            target_type=entity_type,
                            target_id=entity_b.id,  # type: ignore[attr-defined]
                            link_type=LinkType.SAME_LOCATION,
                            confidence=max(0.5, confidence),  # Minimum 0.5 for same location
                        )
                    )
                elif distance <= self._near_meters:
                    # NEAR - within a reasonable distance
                    # Scale confidence between same_location and near thresholds
                    range_size = self._near_meters - self._same_location_meters
                    relative_distance = distance - self._same_location_meters
                    confidence = 1.0 - (relative_distance / range_size)
                    links.append(
                        EntityLink(
                            source_type=entity_type,
                            source_id=entity_a.id,  # type: ignore[attr-defined]
                            target_type=entity_type,
                            target_id=entity_b.id,  # type: ignore[attr-defined]
                            link_type=LinkType.NEAR,
                            confidence=max(0.3, confidence),  # Minimum 0.3 for near
                        )
                    )

        logger.debug(f"Found {len(links)} spatial links for {entity_type}")
        return links
