"""Pipeline orchestration for data import and processing."""

from collections.abc import Callable
from pathlib import Path

from sqlmodel import Session, col, select

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
from potluck.pipeline.ingestion.registry import detect_stage, get_stage
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
        # Track IDs of entities skipped due to deduplication (for FK orphan prevention)
        self._skipped_entity_ids: set[str] = set()

    def run(
        self,
        path: Path,
        entity_types: set[EntityType] | None = None,
        filters: PipelineFilter | None = None,
        import_source: ImportSource | None = None,
        import_run: ImportRun | None = None,
        resume_failed: bool = False,
        content_path: Path | None = None,
        source_type_override: SourceType | None = None,
    ) -> PipelineResult:
        """Run the pipeline for a path.

        Args:
            path: Path to the source file or directory (used for hashing and detection).
            entity_types: Entity types to ingest (None = all available).
            filters: Optional date range filters.
            import_source: Optional existing ImportSource (created if None).
            import_run: Optional existing ImportRun to reuse (created if None).
                When provided, the orchestrator updates this run instead of
                creating a new one. Used by the Celery task path to avoid
                duplicate ImportRun records.
            resume_failed: If True, retry processing even if a previous run
                completed. Default is False (skip if already processed).
            content_path: Optional pre-extracted content path. When provided, skips
                internal archive extraction. Use this when the caller manages the
                extraction lifecycle.
            source_type_override: Optional source type to force a specific ingester
                instead of auto-detecting from filename patterns.

        Returns:
            PipelineResult with statistics.
        """
        logger.info(f"Starting pipeline for: {path}")

        # Clear caches for each new run
        self._seen_hashes.clear()
        self._entity_ids_by_type.clear()
        self._skipped_entity_ids.clear()

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

        if content_path is not None:
            return self._run_inner(
                path,
                content_path,
                entity_types,
                filters,
                import_source,
                import_run,
                file_hash,
                source_type_override,
            )

        # Extract archive if needed and run discovery + ingestion
        with extracted(path) as cp:
            return self._run_inner(
                path,
                cp,
                entity_types,
                filters,
                import_source,
                import_run,
                file_hash,
                source_type_override,
            )

    def _run_inner(
        self,
        path: Path,
        content_path: Path,
        entity_types: set[EntityType] | None,
        filters: PipelineFilter | None,
        import_source: ImportSource | None,
        import_run: ImportRun | None,
        file_hash: str | None,
        source_type_override: SourceType | None = None,
    ) -> PipelineResult:
        """Inner pipeline logic operating on already-extracted content."""
        # Level 1: Resolve ingestion stage — use override or auto-detect from filename
        if source_type_override is not None:
            stage_cls = get_stage(source_type_override)
        else:
            stage_cls = detect_stage(path)

        if stage_cls is None:
            logger.warning(f"No ingestion stage found for: {path}")
            return self._create_empty_result(
                path,
                file_hash,
                "No stage matched",
                import_source=import_source,
                import_run=import_run,
            )

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
            return self._create_empty_result(
                path,
                file_hash,
                "No content found",
                import_source=import_source,
                import_run=import_run,
            )

        # Create or reuse import source
        if import_source is None:
            import_source = self._create_import_source(discovery)
        elif import_source.source_type == SourceType.GENERIC:
            # Update pre-created source with detected type info
            import_source.source_type = discovery.source_type
            import_source.description = f"Import from {discovery.source_path}"
            self.session.commit()

        # Create or reuse import run
        if import_run is None:
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
            .order_by(col(ImportRun.completed_at).desc())
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
        self,
        path: Path,
        file_hash: str | None,
        reason: str,
        import_source: ImportSource | None = None,
        import_run: ImportRun | None = None,
    ) -> PipelineResult:
        """Create an empty result for paths with no content.

        Reuses existing import_source/import_run when provided (e.g., from
        the Celery task path) to avoid creating duplicate records.
        """
        if import_source is None:
            import_source = ImportSource(
                source_type=SourceType.GENERIC,
                name=path.name,
                description=f"Empty import from {path}: {reason}",
            )
            self.session.add(import_source)
            self.session.commit()
            self.session.refresh(import_source)

        if import_run is None:
            import_run = ImportRun(
                source_id=import_source.id,
                status=ImportStatus.COMPLETED,
                file_hash=file_hash,
                completed_at=utc_now(),
            )
            self.session.add(import_run)
        else:
            import_run.status = ImportStatus.COMPLETED
            import_run.completed_at = utc_now()
            import_run.error_message = reason
        self.session.commit()
        self.session.refresh(import_run)

        return PipelineResult(import_run=import_run, stats=PipelineStats())

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
                # Track the skipped entity ID to prevent FK orphans
                entity_id = getattr(entity, "id", None)
                if entity_id:
                    self._skipped_entity_ids.add(str(entity_id))
                stats.entities_skipped += 1
                import_run.entities_skipped = stats.entities_skipped
                self._update_progress(current, total, import_run, "Skipping duplicate")
                continue

            # Check if this entity references a skipped parent entity
            if self._references_skipped_entity(entity):
                stats.entities_skipped += 1
                import_run.entities_skipped = stats.entities_skipped
                self._update_progress(
                    current, total, import_run, "Skipping orphan (parent skipped)"
                )
                continue

            # Add to batch
            batch.append(entity)

            # Flush batch if full
            if len(batch) >= self.batch_size:
                self._flush_batch(batch, stats)
                import_run.entities_created = stats.entities_created
                batch.clear()

            self._update_progress(current, total, import_run)

        # Flush remaining entities
        if batch:
            self._flush_batch(batch, stats)
            import_run.entities_created = stats.entities_created

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

    def _references_skipped_entity(self, entity: IngestableEntity) -> bool:
        """Check if an entity references a skipped parent entity via FK.

        This prevents orphaned child entities when their parent was skipped
        due to deduplication. For example, if a CalendarEvent was skipped,
        its EventParticipants should also be skipped.

        Common FK fields checked:
        - event_id (EventParticipant -> CalendarEvent)
        - email_id (EmailAttachment -> Email)
        - thread_id (ChatMessage -> ChatThread)
        - folder_id (Bookmark -> BookmarkFolder)
        """
        # Common foreign key field names that reference parent entities
        fk_fields = ["event_id", "email_id", "thread_id", "folder_id", "parent_id"]

        for field in fk_fields:
            fk_value = getattr(entity, field, None)
            if fk_value is not None and str(fk_value) in self._skipped_entity_ids:
                return True

        return False

    def _sort_by_dependencies(self, batch: list[IngestableEntity]) -> list[IngestableEntity]:
        """Sort entities by table dependency order for correct FK insertion.

        Tables are prioritized such that parent tables (those referenced by
        foreign keys) come before child tables. This ensures that when a batch
        contains both CalendarEvent and EventParticipant, events are inserted
        before their participants.

        Priority order (lower = insert first):
        - Primary entities: 0 (Media, CalendarEvent, Email, ChatThread, etc.)
        - Dependent entities: 1 (EventParticipant, EmailAttachment, ChatMessage, etc.)
        - Tertiary entities: 2 (BookmarkFolder before Bookmark)
        """
        # Define table names that must be inserted before their dependents
        # Lower priority number = insert first
        table_priority: dict[str, int] = {
            # Primary entities (no in-batch dependencies)
            "media": 0,
            "calendar_events": 0,
            "emails": 0,
            "email_threads": 0,
            "chat_threads": 0,
            "locations": 0,
            "people": 0,
            "bookmark_folders": 0,
            "tags": 0,
            # Dependent entities (have FK to primary)
            "event_participants": 1,
            "email_attachments": 1,
            "chat_messages": 1,
            "chat_thread_participants": 1,
            "location_visits": 1,
            "location_history": 1,
            "bookmarks": 1,
            "tag_assignments": 1,
            "person_aliases": 1,
            "media_embeddings": 1,
            "media_person_links": 1,
            "face_encodings": 1,
        }
        default_priority = 0  # Entities not listed default to primary

        def get_priority(entity: IngestableEntity) -> int:
            tablename = getattr(type(entity), "__tablename__", "")
            return table_priority.get(tablename, default_priority)

        return sorted(batch, key=get_priority)

    def _flush_batch(self, batch: list[IngestableEntity], stats: PipelineStats) -> None:
        """Flush a batch of entities to the database and queue processing.

        Entities are sorted by table dependency order before insertion to ensure
        parent entities (like CalendarEvent) are inserted before child entities
        (like EventParticipant) that have foreign key references to them.
        """
        # Sort batch by table dependency order
        sorted_batch = self._sort_by_dependencies(batch)

        for entity in sorted_batch:
            self.session.add(entity)
            stats.entities_created += 1

        self.session.commit()

        # Queue processing for primary entity types only (those in the model map).
        # Dependent entities (EventParticipant, LocationHistory, etc.) don't need
        # individual processing pipelines.
        for entity in batch:
            entity_type = self._get_entity_type(entity)
            if entity_type is None:
                continue

            self._queue_entity_processing(entity, entity_type)

            # Track entity IDs for batch linker processing
            if entity_type not in self._entity_ids_by_type:
                self._entity_ids_by_type[entity_type] = []
            entity_id = getattr(entity, "id", None)
            if entity_id:
                self._entity_ids_by_type[entity_type].append(str(entity_id))

    def _get_entity_type(self, entity: IngestableEntity) -> EntityType | None:
        """Determine EntityType from entity instance.

        Returns None for dependent entities (e.g. EventParticipant, LocationHistory)
        that don't have their own EntityType and don't need individual processing.
        """
        model_map = get_entity_type_model_map()
        for etype, model_class in model_map.items():
            if isinstance(entity, model_class):
                return etype
        return None

    def _queue_entity_processing(self, entity: IngestableEntity, entity_type: EntityType) -> None:
        """Queue processing tasks for any entity type."""
        # Deferred import: circular dependency via tasks/__init__.py → tasks/ingestion.py → orchestrator.py
        from potluck.pipeline.tasks.processing import run_entity_pipeline

        entity_id = getattr(entity, "id", None)
        if not entity_id:
            return

        try:
            run_entity_pipeline(entity_type.value, str(entity_id))
            logger.debug(f"Queued processing for {entity_type.value} {entity_id}")
        except (OSError, RuntimeError, ValueError, TypeError):
            logger.exception(
                f"Failed to queue processing for {entity_type.value} {entity_id}. "
                "This entity will need to be manually reprocessed."
            )

    def _queue_linkers(self, import_run: ImportRun) -> None:
        """Queue batch linkers for all imported entities."""
        # Deferred import: circular dependency via tasks/__init__.py → tasks/ingestion.py → orchestrator.py
        from potluck.pipeline.tasks.processing import run_linkers_batch

        # Convert to serializable format
        entity_ids_by_type = {etype.value: ids for etype, ids in self._entity_ids_by_type.items()}

        if not any(entity_ids_by_type.values()):
            return

        try:
            run_linkers_batch(str(import_run.id), entity_ids_by_type)
            logger.debug(f"Queued batch linkers for import run {import_run.id}")
        except (OSError, RuntimeError, ValueError, TypeError):
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


def discover(path: Path, content_path: Path | None = None) -> DiscoveryResult:
    """Discover source type and available entities for a path.

    This is a convenience function for previewing what can be imported
    without actually running the pipeline.

    Args:
        path: Path to source file or directory (used for stage detection and metadata).
        content_path: Optional pre-extracted content path. When provided, skips
            internal archive extraction. Use this when the caller manages the
            extraction lifecycle (e.g., CLI wrapping multiple operations in a
            single ``extracted()`` context).

    Returns:
        DiscoveryResult with source type and entity counts.

    Raises:
        IngestionError: If path does not exist.
    """
    if not path.exists():
        raise IngestionError(f"Path not found: {path}")

    def _discover_inner(cp: Path) -> DiscoveryResult:
        stage_cls = detect_stage(path)

        if stage_cls is not None:
            stage = stage_cls()
            detection = stage.detect(cp)
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

    if content_path is not None:
        return _discover_inner(content_path)

    with extracted(path) as cp:
        return _discover_inner(cp)


def ingest(
    path: Path,
    session: Session,
    entity_types: set[EntityType] | None = None,
    filters: PipelineFilter | None = None,
    on_progress: ProgressCallback | None = None,
    import_source: ImportSource | None = None,
    import_run: ImportRun | None = None,
    resume_failed: bool = False,
    content_path: Path | None = None,
    source_type_override: SourceType | None = None,
) -> PipelineResult:
    """Run the pipeline for a path.

    This is a convenience function that creates a PipelineOrchestrator
    and runs it. All entity types are automatically queued for processing,
    and batch linkers are queued after import completes.

    Args:
        path: Path to source file or directory (used for hashing and detection).
        session: Database session.
        entity_types: Entity types to ingest (None = all available).
        filters: Optional date range filters.
        on_progress: Optional progress callback (current, total, message).
        import_source: Optional existing ImportSource to reuse.
        import_run: Optional existing ImportRun to reuse.
        resume_failed: If True, retry failed entities from previous runs.
        content_path: Optional pre-extracted content path. When provided, skips
            internal archive extraction.
        source_type_override: Optional source type to force a specific ingester.

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
        import_source=import_source,
        import_run=import_run,
        resume_failed=resume_failed,
        content_path=content_path,
        source_type_override=source_type_override,
    )
