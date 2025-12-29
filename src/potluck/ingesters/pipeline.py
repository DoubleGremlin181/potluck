"""Ingestion pipeline for orchestrating data import."""

from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, Field
from sqlmodel import Session, select

from potluck.core.logging import get_logger
from potluck.ingesters.base import BaseIngester, IngestionFilter
from potluck.ingesters.utils.archive import extracted
from potluck.ingesters.utils.dedup import compute_file_hash
from potluck.models.base import BaseEntity, EntityType, SourceType, get_entity_type_model_map
from potluck.models.sources import ImportRun, ImportSource, ImportStatus
from potluck.models.utils import utc_now

logger = get_logger(__name__)


# Default batch size for entity persistence
DEFAULT_BATCH_SIZE = 100


# Callback types
ProgressCallback = Callable[[int, int, str | None], None]
"""Progress callback: (current, total, message) -> None"""

EntityCallback = Callable[[EntityType, BaseEntity], None]
"""Entity callback for post-processing (embeddings): (entity_type, entity) -> None"""


class IngestionStats(BaseModel):
    """Statistics from an ingestion run."""

    created: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0

    @property
    def total_processed(self) -> int:
        """Total entities processed."""
        return self.created + self.updated + self.skipped + self.failed


class DiscoveryResult(BaseModel):
    """Result of discovering source type and available entities."""

    source_path: Path
    """Original path that was discovered."""

    ingester: type[BaseIngester] | None = None
    """Matched ingester class, or None if no ingester matched."""

    available_entities: dict[EntityType, int] = Field(default_factory=dict)
    """Entity types available and their counts."""

    metadata: dict[str, str] = Field(default_factory=dict)
    """Additional metadata from detection."""

    model_config = {"arbitrary_types_allowed": True}

    @property
    def is_generic(self) -> bool:
        """True if no specific ingester matched."""
        return self.ingester is None

    @property
    def has_content(self) -> bool:
        """True if any entities were found."""
        return bool(self.available_entities)

    @property
    def source_type(self) -> SourceType:
        """Get source type from ingester or default to GENERIC."""
        if self.ingester is not None:
            return self.ingester.SOURCE_TYPE
        return SourceType.GENERIC


class IngestionResult(BaseModel):
    """Result of an ingestion operation."""

    import_run: ImportRun
    """The ImportRun record with statistics."""

    stats: IngestionStats
    """Detailed ingestion statistics."""

    model_config = {"arbitrary_types_allowed": True}

    @property
    def success(self) -> bool:
        """Check if the ingestion completed successfully."""
        return self.import_run.status == ImportStatus.COMPLETED


class IngestionPipeline:
    """Orchestrates the ingestion pipeline from detection through persistence.

    The pipeline handles:
    - Discovering source type and available entities
    - Extracting archives if needed
    - Checking for duplicate imports (same file processed before)
    - Deduplicating entities by content_hash before persisting
    - Batching entity persistence for performance
    - Tracking progress and statistics
    - Calling callbacks for post-processing (embeddings)

    Duplicate Handling:
    - Source-level: If the same file (by hash) was already imported successfully,
      the pipeline skips re-processing unless resume_failed=True.
    - Entity-level: Individual entities are deduplicated by content_hash.

    Usage:
        pipeline = IngestionPipeline(session)
        result = pipeline.run(path, entity_types={EntityType.MEDIA})
    """

    def __init__(
        self,
        session: Session,
        batch_size: int = DEFAULT_BATCH_SIZE,
        on_progress: ProgressCallback | None = None,
        on_entity: EntityCallback | None = None,
    ):
        """Initialize the pipeline.

        Args:
            session: SQLModel session for database operations.
            batch_size: Number of entities to batch before committing.
            on_progress: Optional callback for progress updates (current, total, message).
            on_entity: Optional callback for each entity created (for embeddings).
        """
        self.session = session
        self.batch_size = batch_size
        self.on_progress = on_progress
        self.on_entity = on_entity
        # In-memory cache of seen content hashes to avoid N+1 queries
        self._seen_hashes: set[str] = set()

    def run(
        self,
        path: Path,
        entity_types: set[EntityType] | None = None,
        filters: IngestionFilter | None = None,
        import_source: ImportSource | None = None,
        resume_failed: bool = False,
    ) -> IngestionResult:
        """Run the ingestion pipeline for a path.

        Args:
            path: Path to the source file or directory.
            entity_types: Entity types to ingest (None = all available).
            filters: Optional date range filters.
            import_source: Optional existing ImportSource (created if None).
            resume_failed: If True, retry processing even if a previous run
                completed. Default is False (skip if already processed).

        Returns:
            IngestionResult with statistics.
        """
        from potluck.ingesters import detect_ingester

        logger.info(f"Starting ingestion for: {path}")

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
                return IngestionResult(
                    import_run=existing_run,
                    stats=IngestionStats(
                        skipped=existing_run.entities_created + existing_run.entities_skipped
                    ),
                )

        # Extract archive if needed and run discovery + ingestion
        with extracted(path) as content_path:
            # Level 1: Detect source type from filename
            ingester_cls = detect_ingester(path)

            if ingester_cls is None:
                logger.warning(f"No ingester found for: {path}")
                return self._create_empty_result(path, file_hash, "No ingester matched")

            # Level 2: Detect available entities
            ingester = ingester_cls()
            detection = ingester.detect_contents(content_path)
            discovery = DiscoveryResult(
                source_path=path,
                ingester=ingester_cls,
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
                return IngestionResult(import_run=import_run, stats=IngestionStats())

            # Calculate total expected entities
            total_expected = sum(discovery.available_entities.get(et, 0) for et in types_to_ingest)
            import_run.entities_found = total_expected
            import_run.progress_total = total_expected
            import_run.status = ImportStatus.RUNNING
            self.session.commit()

            try:
                # Run ingestion
                stats = self._ingest_entities(
                    ingester=ingester,
                    content_path=content_path,
                    entity_types=types_to_ingest,
                    filters=filters,
                    import_run=import_run,
                )

                # Mark as completed
                import_run.status = ImportStatus.COMPLETED
                import_run.completed_at = utc_now()
                import_run.entities_created = stats.created
                import_run.entities_updated = stats.updated
                import_run.entities_skipped = stats.skipped
                import_run.entities_failed = stats.failed
                self.session.commit()

                return IngestionResult(import_run=import_run, stats=stats)

            except Exception as e:
                logger.exception(f"Ingestion failed: {e}")
                import_run.status = ImportStatus.FAILED
                import_run.error_message = str(e)
                import_run.completed_at = utc_now()
                self.session.commit()
                raise

    def _find_completed_run(self, file_hash: str) -> ImportRun | None:
        """Find a completed import run for the given file hash.

        Args:
            file_hash: SHA256 hash of the source file.

        Returns:
            The existing ImportRun if found and completed, None otherwise.
        """
        stmt = (
            select(ImportRun)
            .where(ImportRun.file_hash == file_hash)
            .where(ImportRun.status == ImportStatus.COMPLETED)
            .order_by(ImportRun.completed_at.desc())  # type: ignore[union-attr]
            .limit(1)
        )
        return self.session.exec(stmt).first()

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
    ) -> IngestionResult:
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

        return IngestionResult(import_run=run, stats=IngestionStats())

    def _ingest_entities(
        self,
        ingester: BaseIngester,
        content_path: Path,
        entity_types: set[EntityType],
        filters: IngestionFilter | None,
        import_run: ImportRun,
    ) -> IngestionStats:
        """Ingest entities from the source.

        Args:
            ingester: Instantiated ingester to use.
            content_path: Path to extracted content.
            entity_types: Entity types to ingest.
            filters: Optional date filters.
            import_run: Import run for progress tracking.

        Returns:
            IngestionStats with counts.
        """
        stats = IngestionStats()
        batch: list[BaseEntity] = []
        current = 0
        total = import_run.progress_total or 0

        # Build reverse map from model class to EntityType for callbacks
        entity_type_map = get_entity_type_model_map()
        model_to_type = {v: k for k, v in entity_type_map.items()}

        for entity in ingester.ingest(content_path, entity_types, filters):
            current += 1

            # Check for duplicate by content hash
            if self._is_duplicate(entity):
                stats.skipped += 1
                self._update_progress(current, total, import_run, "Skipping duplicate")
                continue

            # Add to batch
            batch.append(entity)

            # Notify entity callback (for embeddings)
            if self.on_entity:
                try:
                    entity_type = model_to_type.get(type(entity))
                    if entity_type:
                        self.on_entity(entity_type, entity)
                except Exception as e:
                    logger.warning(f"Entity callback failed: {e}")

            # Flush batch if full
            if len(batch) >= self.batch_size:
                self._flush_batch(batch, stats)
                batch.clear()

            self._update_progress(current, total, import_run)

        # Flush remaining entities
        if batch:
            self._flush_batch(batch, stats)

        return stats

    def _is_duplicate(self, entity: BaseEntity) -> bool:
        """Check if an entity is a duplicate by content hash."""
        if entity.content_hash is None:
            return False

        # Check in-memory cache first (O(1) lookup)
        if entity.content_hash in self._seen_hashes:
            return True

        # Query database for existing entity with same hash
        model_class = type(entity)
        stmt = (
            select(model_class.content_hash)
            .where(model_class.content_hash == entity.content_hash)
            .limit(1)
        )

        existing = self.session.exec(stmt).first()
        if existing is not None:
            self._seen_hashes.add(entity.content_hash)
            return True

        # Not a duplicate - add to cache
        self._seen_hashes.add(entity.content_hash)
        return False

    def _flush_batch(self, batch: list[BaseEntity], stats: IngestionStats) -> None:
        """Flush a batch of entities to the database."""
        for entity in batch:
            self.session.add(entity)
            stats.created += 1

        self.session.commit()

    def _update_progress(
        self,
        current: int,
        total: int,
        import_run: ImportRun,
        message: str | None = None,
    ) -> None:
        """Update progress tracking."""
        import_run.progress_current = current
        if current % 100 == 0:  # Flush progress every 100 items
            self.session.commit()

        if self.on_progress:
            try:
                self.on_progress(current, total, message)
            except Exception as e:
                logger.warning(f"Progress callback failed: {e}")


def discover(path: Path) -> DiscoveryResult:
    """Discover source type and available entities for a path.

    This is a convenience function for previewing what can be imported
    without actually running the ingestion.

    Args:
        path: Path to source file or directory.

    Returns:
        DiscoveryResult with source type and entity counts.

    Raises:
        FileNotFoundError: If path does not exist.
    """
    from potluck.ingesters import detect_ingester

    if not path.exists():
        raise FileNotFoundError(f"Path not found: {path}")

    with extracted(path) as content_path:
        # Detect source type and contents
        ingester_cls = detect_ingester(path)

        if ingester_cls is not None:
            ingester = ingester_cls()
            detection = ingester.detect_contents(content_path)
            return DiscoveryResult(
                source_path=path,
                ingester=ingester_cls,
                available_entities=detection.entity_counts,
                metadata=detection.metadata,
            )

        # No ingester matched
        return DiscoveryResult(
            source_path=path,
            ingester=None,
            available_entities={},
        )
