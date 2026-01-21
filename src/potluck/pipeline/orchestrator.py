"""Pipeline orchestration for data import and processing."""

from collections.abc import Callable
from pathlib import Path

from sqlmodel import Session, select

from potluck.core.exceptions import IngestionError
from potluck.core.logging import get_logger
from potluck.models import get_entity_type_model_map
from potluck.models.base import EntityType, IngestableEntity, SourceType
from potluck.models.sources import ImportRun, ImportSource, ImportStatus
from potluck.models.utils import utc_now
from potluck.pipeline.dtos import (
    DiscoveryResult,
    PipelineFilter,
    PipelineResult,
    PipelineStats,
)
from potluck.pipeline.ingestion.base import BaseIngestionStage
from potluck.pipeline.ingestion.registry import detect_stage
from potluck.pipeline.utils.archive import extracted
from potluck.pipeline.utils.hashing import compute_file_hash

logger = get_logger(__name__)


# Default batch size for entity persistence
DEFAULT_BATCH_SIZE = 100


# Callback types
ProgressCallback = Callable[[int, int, str | None], None]
"""Progress callback: (current, total, message) -> None"""


class PipelineOrchestrator:
    """Orchestrates the pipeline from detection through persistence and processing.

    The orchestrator handles:
    - Discovering source type and available entities
    - Extracting archives if needed
    - Checking for duplicate imports (same file processed before)
    - Deduplicating entities by content_hash before persisting
    - Batching entity persistence for performance
    - Tracking progress and statistics
    - Queuing entities for processing after ingestion (all entity types)
    - Queuing batch linkers after import completes

    Duplicate Handling:
    - Source-level: If the same file (by hash) was already imported successfully,
      the pipeline skips re-processing unless resume_failed=True.
    - Entity-level: Individual entities are deduplicated by content_hash.

    Usage:
        orchestrator = PipelineOrchestrator(session)
        result = orchestrator.run(path, entity_types={EntityType.MEDIA})
    """

    def __init__(
        self,
        session: Session,
        batch_size: int = DEFAULT_BATCH_SIZE,
        on_progress: ProgressCallback | None = None,
    ):
        """Initialize the orchestrator.

        Args:
            session: SQLModel session for database operations.
            batch_size: Number of entities to batch before committing.
            on_progress: Optional callback for progress updates (current, total, message).
        """
        self.session = session
        self.batch_size = batch_size
        self.on_progress = on_progress
        # In-memory cache of seen content hashes to avoid N+1 queries
        self._seen_hashes: set[str] = set()
        # Track entity IDs by type for batch linker processing
        self._entity_ids_by_type: dict[EntityType, list[str]] = {}

    def run(
        self,
        path: Path,
        entity_types: set[EntityType] | None = None,
        filters: PipelineFilter | None = None,
        import_source: ImportSource | None = None,
        resume_failed: bool = False,
    ) -> PipelineResult:
        """Run the pipeline for a path.

        Args:
            path: Path to the source file or directory.
            entity_types: Entity types to ingest (None = all available).
            filters: Optional date range filters.
            import_source: Optional existing ImportSource (created if None).
            resume_failed: If True, retry processing even if a previous run
                completed. Default is False (skip if already processed).

        Returns:
            PipelineResult with statistics.
        """
        logger.info(f"Starting pipeline for: {path}")

        # Clear caches for each new run
        self._seen_hashes.clear()
        self._entity_ids_by_type.clear()

        # Compute file hash for source-level deduplication
        file_hash = None
        if path.is_file():
            file_hash = compute_file_hash(path)
            logger.debug(f"Source file hash: {file_hash}")

        # Check for existing completed import of this exact file
        if file_hash and not resume_failed:
            existing_run = self._find_completed_run(file_hash)
            if existing_run:
                logger.info(f"File already imported (run {existing_run.id}), skipping")
                return PipelineResult(
                    import_run=existing_run,
                    stats=PipelineStats(
                        entities_skipped=existing_run.entities_created
                        + existing_run.entities_skipped
                    ),
                )

        # Extract archive if needed and run discovery + ingestion
        with extracted(path) as content_path:
            # Level 1: Detect source type from filename
            stage_cls = detect_stage(path)

            if stage_cls is None:
                logger.warning(f"No ingestion stage found for: {path}")
                return self._create_empty_result(path, file_hash, "No stage matched")

            # Level 2: Detect available entities
            stage = stage_cls()
            detection = stage.detect(content_path)
            discovery = DiscoveryResult(
                source_path=path,
                stage=stage_cls,
                available_entities=detection.entity_counts,
                metadata=detection.metadata,
            )

            if not discovery.has_content:
                logger.warning(f"No ingestable content found in: {path}")
                return self._create_empty_result(path, file_hash, "No content found")

            # Create or get import source
            if import_source is None:
                import_source = self._create_import_source(discovery)

            # Create import run
            import_run = self._create_import_run(import_source, file_hash)

            # Determine entity types to ingest
            types_to_ingest = entity_types or set(discovery.available_entities.keys())
            types_to_ingest = types_to_ingest & set(discovery.available_entities.keys())

            if not types_to_ingest:
                logger.warning("No matching entity types to ingest")
                import_run.status = ImportStatus.COMPLETED
                import_run.completed_at = utc_now()
                self.session.commit()
                return PipelineResult(import_run=import_run, stats=PipelineStats())

            # Calculate total expected entities
            total_expected = sum(discovery.available_entities.get(et, 0) for et in types_to_ingest)
            import_run.entities_found = total_expected
            import_run.progress_total = total_expected
            import_run.status = ImportStatus.RUNNING
            self.session.commit()

            try:
                # Run ingestion
                stats = self._ingest_entities(
                    stage=stage,
                    content_path=content_path,
                    entity_types=types_to_ingest,
                    filters=filters,
                    import_run=import_run,
                )

                # Mark as completed
                import_run.status = ImportStatus.COMPLETED
                import_run.completed_at = utc_now()
                import_run.entities_created = stats.entities_created
                import_run.entities_updated = stats.entities_updated
                import_run.entities_skipped = stats.entities_skipped
                import_run.entities_failed = stats.entities_failed
                self.session.commit()

                # Queue batch linkers if entities were created
                if stats.entities_created > 0:
                    self._queue_linkers(import_run)

                return PipelineResult(import_run=import_run, stats=stats)

            except Exception as e:
                logger.exception(f"Pipeline failed: {e}")
                import_run.status = ImportStatus.FAILED
                import_run.error_message = str(e)
                import_run.completed_at = utc_now()
                self.session.commit()
                raise

    def _find_completed_run(self, file_hash: str) -> ImportRun | None:
        """Find a completed import run for the given file hash."""
        stmt = (
            select(ImportRun)
            .where(ImportRun.file_hash == file_hash)
            .where(ImportRun.status == ImportStatus.COMPLETED)
            .order_by(ImportRun.completed_at.desc())  # type: ignore[union-attr]
            .limit(1)
        )
        result = self.session.exec(stmt)
        return result.first()

    def _create_import_source(self, discovery: DiscoveryResult) -> ImportSource:
        """Create an ImportSource record."""
        source = ImportSource(
            source_type=discovery.source_type,
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
        """Create an ImportRun record."""
        run = ImportRun(
            source_id=import_source.id,
            status=ImportStatus.PENDING,
            file_hash=file_hash,
        )
        self.session.add(run)
        self.session.commit()
        self.session.refresh(run)
        return run

    def _create_empty_result(
        self, path: Path, file_hash: str | None, reason: str
    ) -> PipelineResult:
        """Create an empty result for paths with no content."""
        source = ImportSource(
            source_type=SourceType.GENERIC,
            name=path.name,
            description=f"Empty import from {path}: {reason}",
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

        return PipelineResult(import_run=run, stats=PipelineStats())

    def _ingest_entities(
        self,
        stage: BaseIngestionStage,
        content_path: Path,
        entity_types: set[EntityType],
        filters: PipelineFilter | None,
        import_run: ImportRun,
    ) -> PipelineStats:
        """Ingest entities from the source."""
        stats = PipelineStats()
        batch: list[IngestableEntity] = []
        current = 0
        total = import_run.progress_total or 0

        for entity in stage.execute(content_path, entity_types, filters):
            current += 1

            # Check for duplicate by content hash
            if self._is_duplicate(entity):
                stats.entities_skipped += 1
                self._update_progress(current, total, import_run, "Skipping duplicate")
                continue

            # Add to batch
            batch.append(entity)

            # Flush batch if full
            if len(batch) >= self.batch_size:
                self._flush_batch(batch, stats)
                batch.clear()

            self._update_progress(current, total, import_run)

        # Flush remaining entities
        if batch:
            self._flush_batch(batch, stats)

        return stats

    def _is_duplicate(self, entity: IngestableEntity) -> bool:
        """Check if an entity is a duplicate by content hash."""
        # Only entities with content_hash can be deduplicated
        content_hash = getattr(entity, "content_hash", None)
        if content_hash is None:
            return False

        # Check in-memory cache first (O(1) lookup)
        if content_hash in self._seen_hashes:
            return True

        # Query database for existing entity with same hash
        model_class = type(entity)
        if not hasattr(model_class, "__tablename__"):
            self._seen_hashes.add(content_hash)
            return False

        stmt = (
            select(model_class.content_hash)  # type: ignore[attr-defined]
            .where(model_class.content_hash == content_hash)  # type: ignore[attr-defined]
            .limit(1)
        )

        result = self.session.exec(stmt)
        existing = result.first()
        if existing is not None:
            self._seen_hashes.add(content_hash)
            return True

        self._seen_hashes.add(content_hash)
        return False

    def _flush_batch(self, batch: list[IngestableEntity], stats: PipelineStats) -> None:
        """Flush a batch of entities to the database and queue processing."""
        for entity in batch:
            self.session.add(entity)
            stats.entities_created += 1

        self.session.commit()

        # Queue processing for all entity types
        for entity in batch:
            entity_type = self._get_entity_type(entity)
            self._queue_entity_processing(entity, entity_type)

            # Track entity IDs for batch linker processing
            if entity_type not in self._entity_ids_by_type:
                self._entity_ids_by_type[entity_type] = []
            entity_id = getattr(entity, "id", None)
            if entity_id:
                self._entity_ids_by_type[entity_type].append(str(entity_id))

    def _get_entity_type(self, entity: IngestableEntity) -> EntityType:
        """Determine EntityType from entity instance."""
        model_map = get_entity_type_model_map()
        for etype, model_class in model_map.items():
            if isinstance(entity, model_class):
                return etype
        # Default fallback
        return EntityType.MEDIA

    def _queue_entity_processing(self, entity: IngestableEntity, entity_type: EntityType) -> None:
        """Queue processing tasks for any entity type."""
        # Deferred import to avoid circular import with tasks module
        from potluck.pipeline.tasks.processing import run_entity_pipeline

        entity_id = getattr(entity, "id", None)
        if not entity_id:
            return

        try:
            run_entity_pipeline(entity_type.value, str(entity_id))
            logger.debug(f"Queued processing for {entity_type.value} {entity_id}")
        except Exception:
            logger.exception(
                f"Failed to queue processing for {entity_type.value} {entity_id}. "
                "This entity will need to be manually reprocessed."
            )

    def _queue_linkers(self, import_run: ImportRun) -> None:
        """Queue batch linkers for all imported entities."""
        # Deferred import to avoid circular import with tasks module
        from potluck.pipeline.tasks.processing import run_linkers_batch

        # Convert to serializable format
        entity_ids_by_type = {etype.value: ids for etype, ids in self._entity_ids_by_type.items()}

        if not any(entity_ids_by_type.values()):
            return

        try:
            run_linkers_batch(str(import_run.id), entity_ids_by_type)
            logger.debug(f"Queued batch linkers for import run {import_run.id}")
        except Exception:
            logger.exception(
                f"Failed to queue linkers for import run {import_run.id}. "
                "Linking will need to be run manually."
            )

    def _update_progress(
        self,
        current: int,
        total: int,
        import_run: ImportRun,
        message: str | None = None,
    ) -> None:
        """Update progress tracking."""
        import_run.progress_current = current
        if current % 100 == 0:
            self.session.commit()

        if self.on_progress:
            try:
                self.on_progress(current, total, message)
            except Exception:
                logger.exception(
                    f"Progress callback failed at {current}/{total}. "
                    "Progress updates may be delayed or missing."
                )


def discover(path: Path) -> DiscoveryResult:
    """Discover source type and available entities for a path.

    This is a convenience function for previewing what can be imported
    without actually running the pipeline.

    Args:
        path: Path to source file or directory.

    Returns:
        DiscoveryResult with source type and entity counts.

    Raises:
        IngestionError: If path does not exist.
    """
    if not path.exists():
        raise IngestionError(f"Path not found: {path}")

    with extracted(path) as content_path:
        stage_cls = detect_stage(path)

        if stage_cls is not None:
            stage = stage_cls()
            detection = stage.detect(content_path)
            return DiscoveryResult(
                source_path=path,
                stage=stage_cls,
                available_entities=detection.entity_counts,
                metadata=detection.metadata,
            )

        return DiscoveryResult(
            source_path=path,
            stage=None,
            available_entities={},
        )


def ingest(
    path: Path,
    session: Session,
    entity_types: set[EntityType] | None = None,
    filters: PipelineFilter | None = None,
    on_progress: ProgressCallback | None = None,
    resume_failed: bool = False,
) -> PipelineResult:
    """Run the pipeline for a path.

    This is a convenience function that creates a PipelineOrchestrator
    and runs it. All entity types are automatically queued for processing,
    and batch linkers are queued after import completes.

    Args:
        path: Path to source file or directory.
        session: Database session.
        entity_types: Entity types to ingest (None = all available).
        filters: Optional date range filters.
        on_progress: Optional progress callback (current, total, message).
        resume_failed: If True, retry failed entities from previous runs.

    Returns:
        PipelineResult with import run and statistics.
    """
    orchestrator = PipelineOrchestrator(
        session=session,
        on_progress=on_progress,
    )
    return orchestrator.run(
        path,
        entity_types=entity_types,
        filters=filters,
        resume_failed=resume_failed,
    )
