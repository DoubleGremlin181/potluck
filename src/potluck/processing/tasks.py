"""Celery tasks for background media processing.

This module provides Celery tasks for running media processing in the background,
enabling non-blocking ingestion and progress tracking.
"""

from typing import Any
from uuid import UUID

from celery import Task, chain
from celery.exceptions import Reject, Retry
from sqlmodel import Session, select

from potluck.core.celery import celery_app
from potluck.core.celery_utils import (
    MAX_RETRIES,
    RETRY_BACKOFF,
    RETRY_BACKOFF_MAX,
    is_fatal_error,
    is_transient_error,
)
from potluck.core.logging import get_logger
from potluck.db.session import get_engine
from potluck.models.media import Media
from potluck.processing.base import ProcessingStatus

logger = get_logger(__name__)


def _get_media(session: Session, media_id: str) -> Media | None:
    """Fetch a Media record by ID."""
    stmt = select(Media).where(Media.id == UUID(media_id))
    result = session.execute(stmt)
    return result.scalar_one_or_none()


def _update_media_fields(session: Session, media_id: str, **fields: Any) -> None:
    """Update specific fields on a Media record."""
    media = _get_media(session, media_id)
    if media:
        for key, value in fields.items():
            if value is not None:
                setattr(media, key, value)
        session.add(media)
        session.commit()


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True,
    queue="process",
    autoretry_for=(Retry,),
    retry_backoff=RETRY_BACKOFF,
    retry_backoff_max=RETRY_BACKOFF_MAX,
    max_retries=MAX_RETRIES,
    acks_late=True,
)
def process_media_hashing(self: Task, media_id: str) -> dict[str, Any]:
    """Compute SHA256 and perceptual hash for a media item.

    Args:
        self: Celery task instance (bound).
        media_id: UUID string of the Media to process.

    Returns:
        Dict with processing result.
    """
    from potluck.processing.hashing import HashingProcessor

    logger.info(f"Starting hashing for media {media_id}")

    try:
        engine = get_engine()
        with Session(engine) as session:
            media = _get_media(session, media_id)
            if media is None:
                raise Reject(f"Media not found: {media_id}", requeue=False)

            processor = HashingProcessor()
            result = processor.process(media)

            if result.status == ProcessingStatus.COMPLETED:
                _update_media_fields(
                    session,
                    media_id,
                    file_hash=result.data.get("file_hash"),
                    perceptual_hash=result.data.get("perceptual_hash"),
                )

            return {
                "media_id": media_id,
                "status": result.status.value,
                "file_hash": result.data.get("file_hash"),
                "perceptual_hash": result.data.get("perceptual_hash"),
                "processing_time_ms": result.processing_time_ms,
            }

    except Reject:
        raise
    except Exception as err:
        logger.exception(f"Hashing task failed for {media_id}: {err}")
        if is_fatal_error(err):
            raise Reject(str(err), requeue=False) from err
        elif is_transient_error(err):
            raise self.retry(exc=err) from err
        else:
            raise Reject(str(err), requeue=False) from err


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True,
    queue="process",
    autoretry_for=(Retry,),
    retry_backoff=RETRY_BACKOFF,
    retry_backoff_max=RETRY_BACKOFF_MAX,
    max_retries=MAX_RETRIES,
    acks_late=True,
)
def process_media_metadata(self: Task, media_id: str) -> dict[str, Any]:
    """Extract EXIF metadata from a media item.

    Args:
        self: Celery task instance (bound).
        media_id: UUID string of the Media to process.

    Returns:
        Dict with processing result.
    """
    from potluck.processing.metadata import MetadataProcessor

    logger.info(f"Starting metadata extraction for media {media_id}")

    try:
        engine = get_engine()
        with Session(engine) as session:
            media = _get_media(session, media_id)
            if media is None:
                raise Reject(f"Media not found: {media_id}", requeue=False)

            processor = MetadataProcessor()
            result = processor.process(media)

            if result.status == ProcessingStatus.COMPLETED and result.data.get("has_exif"):
                _update_media_fields(
                    session,
                    media_id,
                    latitude=result.data.get("latitude"),
                    longitude=result.data.get("longitude"),
                    camera_make=result.data.get("camera_make"),
                    camera_model=result.data.get("camera_model"),
                    exif_data=result.data.get("exif_data"),
                )

            return {
                "media_id": media_id,
                "status": result.status.value,
                "has_exif": result.data.get("has_exif", False),
                "latitude": result.data.get("latitude"),
                "longitude": result.data.get("longitude"),
                "processing_time_ms": result.processing_time_ms,
            }

    except Reject:
        raise
    except Exception as err:
        logger.exception(f"Metadata task failed for {media_id}: {err}")
        if is_fatal_error(err):
            raise Reject(str(err), requeue=False) from err
        elif is_transient_error(err):
            raise self.retry(exc=err) from err
        else:
            raise Reject(str(err), requeue=False) from err


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True,
    queue="process",
    autoretry_for=(Retry,),
    retry_backoff=RETRY_BACKOFF,
    retry_backoff_max=RETRY_BACKOFF_MAX,
    max_retries=MAX_RETRIES,
    acks_late=True,
)
def process_media_ocr(self: Task, media_id: str) -> dict[str, Any]:
    """Run OCR on a media item.

    Args:
        self: Celery task instance (bound).
        media_id: UUID string of the Media to process.

    Returns:
        Dict with processing result.
    """
    from potluck.processing.ocr import OCRProcessor

    logger.info(f"Starting OCR for media {media_id}")

    try:
        engine = get_engine()
        with Session(engine) as session:
            media = _get_media(session, media_id)
            if media is None:
                raise Reject(f"Media not found: {media_id}", requeue=False)

            processor = OCRProcessor()
            result = processor.process(media)

            if result.status == ProcessingStatus.COMPLETED:
                ocr_text = result.data.get("ocr_text")
                if ocr_text:
                    _update_media_fields(session, media_id, ocr_text=ocr_text)

            return {
                "media_id": media_id,
                "status": result.status.value,
                "ocr_text_length": len(result.data.get("ocr_text", "")),
                "processing_time_ms": result.processing_time_ms,
            }

    except Reject:
        raise
    except Exception as err:
        logger.exception(f"OCR task failed for {media_id}: {err}")
        if is_fatal_error(err):
            raise Reject(str(err), requeue=False) from err
        elif is_transient_error(err):
            raise self.retry(exc=err) from err
        else:
            raise Reject(str(err), requeue=False) from err


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True,
    queue="process",
    autoretry_for=(Retry,),
    retry_backoff=RETRY_BACKOFF,
    retry_backoff_max=RETRY_BACKOFF_MAX,
    max_retries=MAX_RETRIES,
    acks_late=True,
)
def process_media_faces(self: Task, media_id: str) -> dict[str, Any]:
    """Detect faces in a media item.

    Args:
        self: Celery task instance (bound).
        media_id: UUID string of the Media to process.

    Returns:
        Dict with processing result.
    """
    from potluck.models.base import SourceType
    from potluck.models.media import MediaPersonLink
    from potluck.processing.faces import FaceProcessor

    logger.info(f"Starting face detection for media {media_id}")

    try:
        engine = get_engine()
        with Session(engine) as session:
            media = _get_media(session, media_id)
            if media is None:
                raise Reject(f"Media not found: {media_id}", requeue=False)

            processor = FaceProcessor()
            result = processor.process(media)

            # Persist detected faces to MediaPersonLink table
            faces = result.data.get("faces", [])
            for face_data in faces:
                face_link = MediaPersonLink(
                    media_id=media.id,
                    person_id=None,  # Unidentified until clustered/assigned
                    cluster_id=None,  # Will be assigned by clustering task
                    source_type=SourceType.FACE_DETECTION,
                    confidence=face_data.get("confidence", 1.0),
                    is_confirmed=False,
                    embedding=face_data.get("embedding"),
                    bbox_x=face_data.get("bbox_x"),
                    bbox_y=face_data.get("bbox_y"),
                    bbox_width=face_data.get("bbox_width"),
                    bbox_height=face_data.get("bbox_height"),
                )
                session.add(face_link)

            if faces:
                session.commit()
                logger.info(f"Persisted {len(faces)} faces for media {media_id}")

            return {
                "media_id": media_id,
                "status": result.status.value,
                "faces_detected": len(faces),
                "processing_time_ms": result.processing_time_ms,
            }

    except Reject:
        raise
    except Exception as err:
        logger.exception(f"Face detection task failed for {media_id}: {err}")
        if is_fatal_error(err):
            raise Reject(str(err), requeue=False) from err
        elif is_transient_error(err):
            raise self.retry(exc=err) from err
        else:
            raise Reject(str(err), requeue=False) from err


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True,
    queue="process",
    autoretry_for=(Retry,),
    retry_backoff=RETRY_BACKOFF,
    retry_backoff_max=RETRY_BACKOFF_MAX,
    max_retries=MAX_RETRIES,
    acks_late=True,
)
def process_media_caption(self: Task, media_id: str) -> dict[str, Any]:
    """Generate AI caption for a media item.

    Args:
        self: Celery task instance (bound).
        media_id: UUID string of the Media to process.

    Returns:
        Dict with processing result.
    """
    from potluck.processing.captioning import CaptioningProcessor

    logger.info(f"Starting captioning for media {media_id}")

    try:
        engine = get_engine()
        with Session(engine) as session:
            media = _get_media(session, media_id)
            if media is None:
                raise Reject(f"Media not found: {media_id}", requeue=False)

            processor = CaptioningProcessor()
            result = processor.process(media)

            if result.status == ProcessingStatus.COMPLETED:
                caption = result.data.get("caption")
                if caption:
                    _update_media_fields(session, media_id, caption=caption)

            return {
                "media_id": media_id,
                "status": result.status.value,
                "caption": result.data.get("caption"),
                "processing_time_ms": result.processing_time_ms,
            }

    except Reject:
        raise
    except Exception as err:
        logger.exception(f"Captioning task failed for {media_id}: {err}")
        if is_fatal_error(err):
            raise Reject(str(err), requeue=False) from err
        elif is_transient_error(err):
            raise self.retry(exc=err) from err
        else:
            raise Reject(str(err), requeue=False) from err


def process_media_pipeline(media_id: str) -> None:
    """Trigger full processing pipeline for a media item.

    Chains processors in order: hashing -> metadata -> ocr -> faces -> caption

    Args:
        media_id: UUID string of the Media to process.
    """
    # Use .si() (immutable signature) to prevent previous task result
    # from being passed as first argument to the next task
    chain(
        process_media_hashing.si(media_id),
        process_media_metadata.si(media_id),
        process_media_ocr.si(media_id),
        process_media_faces.si(media_id),
        process_media_caption.si(media_id),
    ).apply_async()


def process_media_basic(media_id: str) -> None:
    """Trigger basic processing (hashing + metadata only).

    Useful when ML dependencies are not available.

    Args:
        media_id: UUID string of the Media to process.
    """
    chain(
        process_media_hashing.si(media_id),
        process_media_metadata.si(media_id),
    ).apply_async()


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True,
    queue="process",
    autoretry_for=(Retry,),
    retry_backoff=RETRY_BACKOFF,
    retry_backoff_max=RETRY_BACKOFF_MAX,
    max_retries=MAX_RETRIES,
    acks_late=True,
)
def cluster_unassigned_faces(self: Task) -> dict[str, Any]:
    """Run DBSCAN clustering on unclustered detected faces.

    This is a periodic task that groups similar unclustered faces together.
    New faces are assigned to existing clusters if similar enough,
    or new clusters are created.

    Returns:
        Dict with clustering statistics.
    """
    from potluck.models.media import MediaPersonLink
    from potluck.models.people import ClusterStatus, FaceCluster
    from potluck.processing.faces import FaceProcessor

    logger.info("Starting face clustering task")

    try:
        engine = get_engine()
        with Session(engine) as session:
            # Get all unclustered faces with embeddings (cluster_id IS NULL and embedding IS NOT NULL)
            stmt = select(MediaPersonLink).where(
                MediaPersonLink.cluster_id == None,  # noqa: E711
                MediaPersonLink.embedding != None,  # noqa: E711
            )
            result = session.execute(stmt)
            unclustered_faces = list(result.scalars().all())

            if not unclustered_faces:
                logger.info("No unclustered faces to process")
                return {
                    "status": "completed",
                    "faces_processed": 0,
                    "clusters_created": 0,
                    "faces_assigned": 0,
                }

            # Extract embeddings and IDs
            embeddings = [list(face.embedding) for face in unclustered_faces]
            face_ids = [face.id for face in unclustered_faces]

            # Run clustering
            processor = FaceProcessor()
            clusters = processor.cluster_embeddings(embeddings, face_ids)

            clusters_created = 0
            faces_assigned = 0

            for label, cluster_face_ids in clusters.items():
                if label == -1:
                    # Noise - faces that don't belong to any cluster
                    continue

                # Get embeddings for this cluster
                cluster_embeddings = [embeddings[face_ids.index(fid)] for fid in cluster_face_ids]

                # Compute centroid
                centroid = processor.compute_cluster_centroid(cluster_embeddings)

                # Create new cluster
                new_cluster = FaceCluster(
                    representative_encoding=centroid,
                    status=ClusterStatus.PENDING,
                    face_count=len(cluster_face_ids),
                    needs_review=len(cluster_face_ids) < 3,  # Flag small clusters
                )
                session.add(new_cluster)
                session.flush()  # Get the cluster ID

                # Assign faces to cluster
                for face_id in cluster_face_ids:
                    face = session.get(MediaPersonLink, face_id)
                    if face:
                        face.cluster_id = new_cluster.id
                        faces_assigned += 1

                clusters_created += 1

            session.commit()

            logger.info(
                f"Clustering complete: {clusters_created} clusters created, "
                f"{faces_assigned} faces assigned"
            )

            return {
                "status": "completed",
                "faces_processed": len(unclustered_faces),
                "clusters_created": clusters_created,
                "faces_assigned": faces_assigned,
            }

    except Exception as err:
        logger.exception(f"Face clustering task failed: {err}")
        if is_transient_error(err):
            raise self.retry(exc=err) from err
        else:
            raise Reject(str(err), requeue=False) from err
