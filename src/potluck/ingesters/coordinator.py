"""Ingestion coordinator for orchestrating data import pipelines."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from potluck.core.logging import get_logger
from potluck.ingesters.base import IngestionFilter
from potluck.ingesters.discover import DiscoveryResult, discover
from potluck.ingesters.hooks import HookRegistry, get_hook_registry
from potluck.ingesters.utils.archive import extracted
from potluck.ingesters.utils.dedup import compute_file_hash
from potluck.ingesters.utils.progress import (
    IngestionStats,
    NoOpProgressCallback,
    ProgressCallback,
    ProgressTracker,
)
from potluck.models.base import BaseEntity, EntityType
from potluck.models.sources import ImportRun, ImportSource, ImportStatus
from potluck.models.utils import utc_now

if TYPE_CHECKING:
    from sqlmodel import Session

logger = get_logger(__name__)

# Type alias for discover function signature
DiscoverFn = Callable[[Path], DiscoveryResult]


# Default batch size for entity persistence
DEFAULT_BATCH_SIZE = 100


@dataclass
class EntityBatch:
    """Manages batched entity persistence.

    Collects entities and flushes them to the database when the batch
    reaches the configured size.
    """

    max_size: int
    """Maximum number of entities before auto-flush."""

    entities: dict[EntityType, list[BaseEntity]]
    """Entities grouped by type."""

    count: int = 0
    """Current number of entities in batch."""

    @classmethod
    def create(cls, max_size: int = DEFAULT_BATCH_SIZE) -> "EntityBatch":
        """Create a new empty batch.

        Args:
            max_size: Maximum batch size before flush.

        Returns:
            New EntityBatch instance.
        """
        return cls(max_size=max_size, entities={}, count=0)

    def add(self, entity_type: EntityType, entity: BaseEntity) -> bool:
        """Add an entity to the batch.

        Args:
            entity_type: Type of the entity.
            entity: Entity to add.

        Returns:
            True if batch is full and should be flushed.
        """
        if entity_type not in self.entities:
            self.entities[entity_type] = []
        self.entities[entity_type].append(entity)
        self.count += 1
        return self.count >= self.max_size

    def clear(self) -> None:
        """Clear the batch after flushing."""
        self.entities = {}
        self.count = 0

    def is_empty(self) -> bool:
        """Check if batch has no entities."""
        return self.count == 0


@dataclass
class IngestionResult:
    """Result of an ingestion operation."""

    import_run: ImportRun
    """The ImportRun record with statistics."""

    stats: IngestionStats
    """Detailed ingestion statistics."""

    @property
    def success(self) -> bool:
        """Check if the ingestion completed successfully."""
        return self.import_run.status == ImportStatus.COMPLETED


class IngestionCoordinator:
    """Coordinates the ingestion pipeline from detection through persistence.

    The coordinator is responsible for:
    - Discovering source type and available entities
    - Managing database session lifecycle
    - Deduplicating entities by content_hash before persisting
    - Batching entity persistence for performance
    - Updating ImportRun statistics in real-time
    - Calling registered hooks for post-processing

    Usage:
        coordinator = IngestionCoordinator(session)
        result = coordinator.run(path, entity_types={EntityType.MEDIA})
    """

    def __init__(
        self,
        session: "Session",
        batch_size: int = DEFAULT_BATCH_SIZE,
        progress_callback: ProgressCallback | None = None,
        hook_registry: HookRegistry | None = None,
        discover_fn: DiscoverFn | None = None,
    ):
        """Initialize the coordinator.

        Args:
            session: SQLModel session for database operations.
            batch_size: Number of entities to batch before committing.
            progress_callback: Optional callback for progress notifications.
            hook_registry: Optional hook registry for notifications (default: global).
            discover_fn: Optional discover function for testing (default: discover).
        """
        self.session = session
        self.batch_size = batch_size
        self.progress_callback = progress_callback or NoOpProgressCallback()
        self._hook_registry = hook_registry or get_hook_registry()
        self._discover_fn = discover_fn or discover
        # In-memory cache of seen content hashes to avoid N+1 queries
        self._seen_hashes: set[str] = set()

    def run(
        self,
        path: Path,
        entity_types: set[EntityType] | None = None,
        filters: IngestionFilter | None = None,
        import_source: ImportSource | None = None,
    ) -> IngestionResult:
        """Run the ingestion pipeline for a path.

        Args:
            path: Path to the source file or directory.
            entity_types: Entity types to ingest (None = all available).
            filters: Optional date range filters.
            import_source: Optional existing ImportSource (created if None).

        Returns:
            IngestionResult with statistics.
        """
        logger.info(f"Starting ingestion for: {path}")

        # Compute file hash for deduplication
        file_hash = None
        if path.is_file():
            file_hash = compute_file_hash(path)
            logger.debug(f"Source file hash: {file_hash}")

        # Discover source type and contents
        discovery = self._discover_fn(path)
        if not discovery.has_content:
            logger.warning(f"No ingestable content found in: {path}")
            return self._create_empty_result(path, file_hash)

        # Create or get import source
        if import_source is None:
            import_source = self._create_import_source(discovery)

        # Create import run
        import_run = self._create_import_run(import_source, file_hash)

        # Set up progress tracking
        tracker = ProgressTracker(
            import_run=import_run,
            session=self.session,
            callback=self.progress_callback,
        )

        # Determine entity types to ingest
        types_to_ingest = entity_types or set(discovery.available_entities.keys())
        types_to_ingest = types_to_ingest & set(discovery.available_entities.keys())

        if not types_to_ingest:
            logger.warning("No matching entity types to ingest")
            import_run.status = ImportStatus.COMPLETED
            import_run.completed_at = utc_now()
            self.session.commit()
            return IngestionResult(import_run=import_run, stats=tracker.get_stats())

        # Calculate total expected entities
        total_expected = sum(discovery.available_entities.get(et, 0) for et in types_to_ingest)
        tracker.set_total(total_expected)

        # Update status to running
        import_run.status = ImportStatus.RUNNING
        import_run.entities_found = total_expected
        self.session.commit()

        try:
            # Run ingestion with extracted content
            with extracted(path) as content_path:
                self._ingest_entities(
                    discovery=discovery,
                    content_path=content_path,
                    entity_types=types_to_ingest,
                    filters=filters,
                    tracker=tracker,
                )

            # Mark as completed
            import_run.status = ImportStatus.COMPLETED
            import_run.completed_at = utc_now()

            # Notify hooks
            self._hook_registry.notify_import_complete(import_run)

        except Exception as e:
            logger.exception(f"Ingestion failed: {e}")
            import_run.status = ImportStatus.FAILED
            import_run.error_message = str(e)
            import_run.completed_at = utc_now()
            self.session.commit()  # Persist failure status

        # Final flush
        tracker.flush()

        return IngestionResult(import_run=import_run, stats=tracker.get_stats())

    def _create_import_source(self, discovery: "DiscoveryResult") -> ImportSource:
        """Create an ImportSource record for the discovery.

        Args:
            discovery: Discovery result.

        Returns:
            Created ImportSource.
        """
        from potluck.models.base import SourceType

        source_type = SourceType.GENERIC
        if discovery.ingester is not None:
            source_type = discovery.ingester.SOURCE_TYPE

        source = ImportSource(
            source_type=source_type,
            name=discovery.source_path.name,
            description=f"Import from {discovery.source_path}",
        )
        self.session.add(source)
        self.session.commit()
        self.session.refresh(source)
        return source

    def _create_import_run(
        self,
        import_source: ImportSource,
        file_hash: str | None,
    ) -> ImportRun:
        """Create an ImportRun record.

        Args:
            import_source: The import source.
            file_hash: Hash of the source file.

        Returns:
            Created ImportRun.
        """
        run = ImportRun(
            source_id=import_source.id,
            status=ImportStatus.PENDING,
            file_hash=file_hash,
        )
        self.session.add(run)
        self.session.commit()
        self.session.refresh(run)
        return run

    def _create_empty_result(self, path: Path, file_hash: str | None) -> IngestionResult:
        """Create an empty result for paths with no content.

        Args:
            path: Source path.
            file_hash: Hash of the source file.

        Returns:
            Empty IngestionResult.
        """
        from potluck.models.base import SourceType

        source = ImportSource(
            source_type=SourceType.GENERIC,
            name=path.name,
            description=f"Empty import from {path}",
        )
        self.session.add(source)
        self.session.commit()
        self.session.refresh(source)

        run = ImportRun(
            source_id=source.id,
            status=ImportStatus.COMPLETED,
            file_hash=file_hash,
            completed_at=utc_now(),
        )
        self.session.add(run)
        self.session.commit()
        self.session.refresh(run)

        return IngestionResult(import_run=run, stats=IngestionStats())

    def _ingest_entities(
        self,
        discovery: "DiscoveryResult",
        content_path: Path,
        entity_types: set[EntityType],
        filters: IngestionFilter | None,
        tracker: ProgressTracker,
    ) -> None:
        """Ingest entities of the specified types.

        Args:
            discovery: Discovery result.
            content_path: Path to extracted content.
            entity_types: Entity types to ingest.
            filters: Optional date filters.
            tracker: Progress tracker.
        """
        if discovery.ingester is None:
            # Generic ingestion - would be implemented in Phase 8
            logger.info("Generic ingestion not yet implemented")
            return

        ingester_instance = discovery.ingester()
        batch = EntityBatch.create(self.batch_size)

        for entity_type in entity_types:
            try:
                ingest_method = ingester_instance.get_ingest_method(entity_type)
            except (ValueError, NotImplementedError):
                logger.debug(f"Ingester does not support {entity_type.value}")
                continue

            tracker.set_current_file(f"{entity_type.value} entities")

            for entity in ingest_method(content_path, filters):
                # Check for duplicate by content hash
                if self._is_duplicate(entity):
                    tracker.update_stats(skipped=1)
                    tracker.increment()
                    continue

                # Add to batch and flush if full
                if batch.add(entity_type, entity):
                    self._flush_batch(batch, tracker)
                    batch.clear()

                tracker.increment()

        # Flush remaining entities
        if not batch.is_empty():
            self._flush_batch(batch, tracker)

    def _is_duplicate(self, entity: BaseEntity) -> bool:
        """Check if an entity is a duplicate by content hash.

        Uses an in-memory cache to avoid N+1 queries - if we've already seen
        a hash in this ingestion run, we don't need to query the database again.

        Args:
            entity: Entity to check.

        Returns:
            True if a duplicate exists.
        """
        if entity.content_hash is None:
            return False

        # Check in-memory cache first (O(1) lookup)
        if entity.content_hash in self._seen_hashes:
            return True

        from sqlmodel import select

        # Get the model class for this entity
        model_class = type(entity)

        stmt = (
            select(model_class.content_hash)
            .where(model_class.content_hash == entity.content_hash)
            .limit(1)
        )

        existing = self.session.exec(stmt).first()
        if existing is not None:
            # Add to cache so we don't query again for this hash
            self._seen_hashes.add(entity.content_hash)
            return True

        # Not a duplicate - add to cache to prevent DB lookup if we see it again
        self._seen_hashes.add(entity.content_hash)
        return False

    def _flush_batch(
        self,
        batch: EntityBatch,
        tracker: ProgressTracker,
    ) -> None:
        """Flush a batch of entities to the database.

        Args:
            batch: EntityBatch to flush.
            tracker: Progress tracker.
        """
        created_count = 0

        for entity_type, entities in batch.entities.items():
            for entity in entities:
                self.session.add(entity)
                created_count += 1

                # Notify hooks for each entity
                self._hook_registry.notify_entity_created(entity_type, entity)

        self.session.commit()
        tracker.update_stats(created=created_count)

        # Notify hooks of batch completion
        self._hook_registry.notify_batch_complete(batch.entities)
