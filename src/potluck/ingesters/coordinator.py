"""Ingestion coordinator for orchestrating data import pipelines."""

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


# Default batch size for entity persistence
DEFAULT_BATCH_SIZE = 100


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
    ):
        """Initialize the coordinator.

        Args:
            session: SQLModel session for database operations.
            batch_size: Number of entities to batch before committing.
            progress_callback: Optional callback for progress notifications.
        """
        self.session = session
        self.batch_size = batch_size
        self.progress_callback = progress_callback or NoOpProgressCallback()

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
        discovery = discover(path)
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
            get_hook_registry().notify_import_complete(import_run)

        except Exception as e:
            logger.exception(f"Ingestion failed: {e}")
            import_run.status = ImportStatus.FAILED
            import_run.error_message = str(e)
            import_run.completed_at = utc_now()

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
        hook_registry = get_hook_registry()

        # Batch storage
        batch: dict[EntityType, list[BaseEntity]] = {}
        batch_count = 0

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

                # Add to batch
                if entity_type not in batch:
                    batch[entity_type] = []
                batch[entity_type].append(entity)
                batch_count += 1

                # Flush batch if full
                if batch_count >= self.batch_size:
                    self._flush_batch(batch, tracker, hook_registry)
                    batch = {}
                    batch_count = 0

                tracker.increment()

        # Flush remaining entities
        if batch:
            self._flush_batch(batch, tracker, hook_registry)

    def _is_duplicate(self, entity: BaseEntity) -> bool:
        """Check if an entity is a duplicate by content hash.

        Args:
            entity: Entity to check.

        Returns:
            True if a duplicate exists.
        """
        if entity.content_hash is None:
            return False

        from sqlmodel import select

        # Get the model class for this entity
        model_class = type(entity)

        stmt = select(model_class).where(model_class.content_hash == entity.content_hash).limit(1)

        existing = self.session.exec(stmt).first()
        return existing is not None

    def _flush_batch(
        self,
        batch: dict[EntityType, list[BaseEntity]],
        tracker: ProgressTracker,
        hook_registry: "HookRegistry",
    ) -> None:
        """Flush a batch of entities to the database.

        Args:
            batch: Entities to flush, grouped by type.
            tracker: Progress tracker.
            hook_registry: Hook registry for notifications.
        """
        created_count = 0

        for entity_type, entities in batch.items():
            for entity in entities:
                self.session.add(entity)
                created_count += 1

                # Notify hooks for each entity
                hook_registry.notify_entity_created(entity_type, entity)

        self.session.commit()
        tracker.update_stats(created=created_count)

        # Notify hooks of batch completion
        hook_registry.notify_batch_complete(batch)
