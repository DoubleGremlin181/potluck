"""Spatial linker for location-based entity relationships.

Creates SAME_LOCATION and NEAR links between entities based on their
geographic coordinates. Uses a grid-based spatial index to avoid O(n²)
distance calculations.
"""

import math
from collections import defaultdict
from collections.abc import Iterator
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

# Approximate meters per degree of latitude (constant everywhere on Earth)
METERS_PER_DEGREE_LAT = 111_320.0


class _CoordEntity:
    """Lightweight coordinate holder to avoid loading full ORM objects."""

    __slots__ = ("id", "lat", "lon")

    def __init__(self, entity_id: UUID, lat: float, lon: float) -> None:
        self.id = entity_id
        self.lat = lat
        self.lon = lon


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

    Uses grid-based spatial indexing to reduce comparisons from O(n²)
    to O(n × k) where k is the average number of neighbors per cell.

    Only processes Location entities; LocationVisit is excluded because
    raw GPS pings are already linked to Locations via foreign keys.
    """

    NAME: ClassVar[str] = "spatial"
    LINK_TYPES: ClassVar[set[LinkType]] = {LinkType.SAME_LOCATION, LinkType.NEAR}
    SUPPORTED_ENTITY_TYPES: ClassVar[set[EntityType]] = {EntityType.LOCATION}

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
    ) -> Iterator[EntityLink]:
        """Find spatial links between entities using grid-based indexing.

        Algorithm:
        1. Compute grid cell size from ``near_meters`` threshold.
        2. Assign each entity to a grid cell based on lat/lon.
        3. For each cell, compare entities within the same cell and
           4 forward-neighbor cells to find pairs within distance thresholds.
        4. Yield links without accumulating them in memory.

        Args:
            session: Database session.
            entity_type: Type of entities to analyze.
            entity_ids: List of entity IDs.

        Yields:
            SAME_LOCATION and NEAR EntityLink records.
        """
        if len(entity_ids) < 2:
            return

        # Get the model class for this entity type
        model_map = get_entity_type_model_map()
        model_class = model_map.get(entity_type)
        if model_class is None:
            logger.warning(f"No model class found for entity type: {entity_type}")
            return

        # Check if this model has latitude/longitude fields
        if not hasattr(model_class, "latitude") or not hasattr(model_class, "longitude"):
            logger.debug(f"Entity type {entity_type} has no lat/lon fields, skipping")
            return

        # Fetch only id, lat, lon — avoid loading full ORM objects
        stmt = (
            select(
                model_class.id,  # type: ignore[attr-defined]
                model_class.latitude,  # type: ignore[attr-defined]
                model_class.longitude,  # type: ignore[attr-defined]
            )
            .where(model_class.id.in_(entity_ids))  # type: ignore[attr-defined]
            .where(model_class.latitude.isnot(None))  # type: ignore[attr-defined]
            .where(model_class.longitude.isnot(None))  # type: ignore[attr-defined]
        )
        rows = session.exec(stmt).all()

        coords = [_CoordEntity(row[0], row[1], row[2]) for row in rows]

        if len(coords) < 2:
            return

        yield from self._grid_find_links(coords, entity_type)

    def _grid_find_links(
        self,
        coords: list[_CoordEntity],
        entity_type: EntityType,
    ) -> Iterator[EntityLink]:
        """Find links using a grid-based spatial index.

        Args:
            coords: List of coordinate entities.
            entity_type: The entity type for link records.

        Yields:
            EntityLink records for nearby pairs.
        """
        if len(coords) < 2:
            return

        # Cell size in degrees — one cell covers near_meters distance
        cell_lat = self._near_meters / METERS_PER_DEGREE_LAT

        # Use median latitude for longitude correction
        lats = [c.lat for c in coords]
        median_lat = sorted(lats)[len(lats) // 2]
        cos_lat = math.cos(math.radians(median_lat))
        # Degrees of longitude per near_meters at the median latitude
        cell_lon = cell_lat / cos_lat if cos_lat > 0.01 else cell_lat

        # Build grid: map (row, col) → list of entities in that cell
        grid: dict[tuple[int, int], list[_CoordEntity]] = defaultdict(list)
        for c in coords:
            row = int(c.lat / cell_lat)
            col = int(c.lon / cell_lon)
            grid[(row, col)].append(c)

        # Forward-neighbor offsets: same cell + 4 forward neighbors.
        # This covers all unique pairs across adjacent cells without duplicates.
        neighbor_offsets = [(0, 0), (0, 1), (1, 0), (1, -1), (1, 1)]

        link_count = 0
        for (row, col), cell_entities in grid.items():
            for dr, dc in neighbor_offsets:
                neighbor_key = (row + dr, col + dc)

                if dr == 0 and dc == 0:
                    # Same cell: compare all pairs within cell
                    for i, a in enumerate(cell_entities):
                        for b in cell_entities[i + 1 :]:
                            link = self._check_pair(a, b, entity_type)
                            if link is not None:
                                link_count += 1
                                yield link
                else:
                    # Adjacent cell: compare every entity in this cell with every
                    # entity in the neighbor cell
                    neighbor_entities = grid.get(neighbor_key)
                    if neighbor_entities is None:
                        continue
                    for a in cell_entities:
                        for b in neighbor_entities:
                            link = self._check_pair(a, b, entity_type)
                            if link is not None:
                                link_count += 1
                                yield link

        logger.debug(f"Found {link_count} spatial links for {entity_type}")

    def _check_pair(
        self,
        a: _CoordEntity,
        b: _CoordEntity,
        entity_type: EntityType,
    ) -> EntityLink | None:
        """Check if two entities are close enough to create a link.

        Args:
            a: First entity.
            b: Second entity.
            entity_type: The entity type for the link.

        Returns:
            EntityLink if the pair is within thresholds, None otherwise.
        """
        distance = haversine_distance(a.lat, a.lon, b.lat, b.lon)

        if distance <= self._same_location_meters:
            confidence = max(0.5, 1.0 - (distance / self._same_location_meters))
            return EntityLink(
                source_type=entity_type,
                source_id=a.id,
                target_type=entity_type,
                target_id=b.id,
                link_type=LinkType.SAME_LOCATION,
                confidence=confidence,
            )

        if distance <= self._near_meters:
            range_size = self._near_meters - self._same_location_meters
            relative_distance = distance - self._same_location_meters
            confidence = max(0.3, 1.0 - (relative_distance / range_size))
            return EntityLink(
                source_type=entity_type,
                source_id=a.id,
                target_type=entity_type,
                target_id=b.id,
                link_type=LinkType.NEAR,
                confidence=confidence,
            )

        return None
