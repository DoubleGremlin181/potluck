"""Celery tasks for background media processing.

This module provides Celery tasks for running processing stages on media items.
Tasks use a factory pattern to reduce code duplication while maintaining
stage-specific result handling.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID

from celery import Task, chain
from celery.exceptions import Reject, Retry
from sqlmodel import Session, select

from potluck.core.celery import (
    MAX_RETRIES,
    RETRY_BACKOFF,
    RETRY_BACKOFF_MAX,
    celery_app,
    is_fatal_error,
    is_transient_error,
)
from potluck.core.logging import get_logger
from potluck.db.session import get_engine
from potluck.models.media import Media
from potluck.pipeline.dtos import StageResult, StageStatus
from potluck.pipeline.processing.base import BaseProcessingStage

logger = get_logger(__name__)


# Type alias for result handler functions
ResultHandler = Callable[[Session, str, StageResult], dict[str, Any]]


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


def _run_stage_task(
    task: Task[..., dict[str, Any]],
    media_id: str,
    stage_factory: Callable[[], BaseProcessingStage],
    stage_name: str,
    result_handler: ResultHandler,
) -> dict[str, Any]:
    """Execute a processing stage with standard error handling.

    This is the core implementation shared by all stage tasks. It handles:
    - Media lookup and validation
    - Stage execution
    - Error classification (transient vs fatal)
    - Retry/reject logic

    Args:
        task: The Celery task instance (for retry support).
        media_id: ID of the media item to process.
        stage_factory: Callable that creates the stage instance.
        stage_name: Human-readable name for logging.
        result_handler: Function to handle stage result and return task output.

    Returns:
        Dict with task results (structure depends on result_handler).

    Raises:
        Reject: For fatal errors or unknown errors.
        Retry: For transient errors (via task.retry).
    """
    logger.info(f"Starting {stage_name} for media {media_id}")

    try:
        engine = get_engine()
        with Session(engine) as session:
            media = _get_media(session, media_id)
            if media is None:
                raise Reject(f"Media not found: {media_id}", requeue=False)

            stage = stage_factory()
            result = stage.execute(media)
            return result_handler(session, media_id, result)

    except Reject:
        raise
    except Exception as err:
        logger.exception(f"{stage_name} task failed for {media_id}: {err}")
        if is_fatal_error(err):
            raise Reject(str(err), requeue=False) from err
        elif is_transient_error(err):
            raise task.retry(exc=err) from err
        else:
            raise Reject(str(err), requeue=False) from err


# -----------------------------------------------------------------------------
# Result Handlers - Stage-specific logic for processing results
# -----------------------------------------------------------------------------


def _handle_hashing_result(session: Session, media_id: str, result: StageResult) -> dict[str, Any]:
    """Handle hashing stage result: update file_hash and perceptual_hash."""
    if result.status == StageStatus.COMPLETED:
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


def _handle_metadata_result(session: Session, media_id: str, result: StageResult) -> dict[str, Any]:
    """Handle metadata stage result: update EXIF fields."""
    if result.status == StageStatus.COMPLETED and result.data.get("has_exif"):
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


def _handle_ocr_result(session: Session, media_id: str, result: StageResult) -> dict[str, Any]:
    """Handle OCR stage result: update ocr_text field."""
    if result.status == StageStatus.COMPLETED:
        ocr_text = result.data.get("ocr_text")
        if ocr_text:
            _update_media_fields(session, media_id, ocr_text=ocr_text)
    return {
        "media_id": media_id,
        "status": result.status.value,
        "ocr_text_length": len(result.data.get("ocr_text", "")),
        "processing_time_ms": result.processing_time_ms,
    }


def _handle_captioning_result(
    session: Session, media_id: str, result: StageResult
) -> dict[str, Any]:
    """Handle captioning stage result: update caption field."""
    if result.status == StageStatus.COMPLETED:
        caption = result.data.get("caption")
        if caption:
            _update_media_fields(session, media_id, caption=caption)
    return {
        "media_id": media_id,
        "status": result.status.value,
        "caption": result.data.get("caption"),
        "processing_time_ms": result.processing_time_ms,
    }


def _handle_faces_result(session: Session, media_id: str, result: StageResult) -> dict[str, Any]:
    """Handle faces stage result: persist detected faces to MediaPersonLink."""
    from potluck.models.faces import MediaPersonLink

    faces = result.data.get("faces", [])

    for face_data in faces:
        face_link = MediaPersonLink(
            media_id=UUID(media_id),
            person_id=None,
            cluster_id=None,
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


# -----------------------------------------------------------------------------
# Celery Tasks
# -----------------------------------------------------------------------------


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True,
    queue="process",
    autoretry_for=(Retry,),
    retry_backoff=RETRY_BACKOFF,
    retry_backoff_max=RETRY_BACKOFF_MAX,
    max_retries=MAX_RETRIES,
    acks_late=True,
)
def run_hashing_stage(self: Task[..., dict[str, Any]], media_id: str) -> dict[str, Any]:
    """Compute SHA256 and perceptual hash for a media item."""
    from potluck.pipeline.processing.hashing import HashingStage

    return _run_stage_task(self, media_id, HashingStage, "hashing", _handle_hashing_result)


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True,
    queue="process",
    autoretry_for=(Retry,),
    retry_backoff=RETRY_BACKOFF,
    retry_backoff_max=RETRY_BACKOFF_MAX,
    max_retries=MAX_RETRIES,
    acks_late=True,
)
def run_metadata_stage(self: Task[..., dict[str, Any]], media_id: str) -> dict[str, Any]:
    """Extract EXIF metadata from a media item."""
    from potluck.pipeline.processing.metadata import MetadataStage

    return _run_stage_task(self, media_id, MetadataStage, "metadata", _handle_metadata_result)


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True,
    queue="process",
    autoretry_for=(Retry,),
    retry_backoff=RETRY_BACKOFF,
    retry_backoff_max=RETRY_BACKOFF_MAX,
    max_retries=MAX_RETRIES,
    acks_late=True,
)
def run_ocr_stage(self: Task[..., dict[str, Any]], media_id: str) -> dict[str, Any]:
    """Run OCR on a media item."""
    from potluck.pipeline.processing.ocr import OCRStage

    return _run_stage_task(self, media_id, OCRStage, "OCR", _handle_ocr_result)


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True,
    queue="process",
    autoretry_for=(Retry,),
    retry_backoff=RETRY_BACKOFF,
    retry_backoff_max=RETRY_BACKOFF_MAX,
    max_retries=MAX_RETRIES,
    acks_late=True,
)
def run_faces_stage(self: Task[..., dict[str, Any]], media_id: str) -> dict[str, Any]:
    """Detect faces in a media item."""
    from potluck.pipeline.processing.faces import FaceStage

    return _run_stage_task(self, media_id, FaceStage, "face detection", _handle_faces_result)


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True,
    queue="process",
    autoretry_for=(Retry,),
    retry_backoff=RETRY_BACKOFF,
    retry_backoff_max=RETRY_BACKOFF_MAX,
    max_retries=MAX_RETRIES,
    acks_late=True,
)
def run_captioning_stage(self: Task[..., dict[str, Any]], media_id: str) -> dict[str, Any]:
    """Generate AI caption for a media item."""
    from potluck.pipeline.processing.captioning import CaptioningStage

    return _run_stage_task(self, media_id, CaptioningStage, "captioning", _handle_captioning_result)


def run_processing_pipeline(media_id: str) -> None:
    """Trigger full processing pipeline for a media item.

    Chains stages in order: hashing -> metadata -> ocr -> faces -> caption
    """
    chain(
        run_hashing_stage.si(media_id),
        run_metadata_stage.si(media_id),
        run_ocr_stage.si(media_id),
        run_faces_stage.si(media_id),
        run_captioning_stage.si(media_id),
    ).apply_async()


def run_basic_processing(media_id: str) -> None:
    """Trigger basic processing (hashing + metadata only).

    Useful when ML dependencies are not available.
    """
    chain(
        run_hashing_stage.si(media_id),
        run_metadata_stage.si(media_id),
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
def cluster_unassigned_faces(self: Task[..., dict[str, Any]]) -> dict[str, Any]:
    """Run DBSCAN clustering on unclustered detected faces."""
    from potluck.models.faces import ClusterStatus, FaceCluster, MediaPersonLink
    from potluck.pipeline.processing.faces import FaceStage

    logger.info("Starting face clustering task")

    try:
        engine = get_engine()
        with Session(engine) as session:
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

            embeddings = [list(face.embedding) for face in unclustered_faces]
            face_ids = [face.id for face in unclustered_faces]

            stage = FaceStage()
            clusters = stage.cluster_embeddings(embeddings, face_ids)

            clusters_created = 0
            faces_assigned = 0

            for label, cluster_face_ids in clusters.items():
                if label == -1:
                    continue

                cluster_embeddings = [embeddings[face_ids.index(fid)] for fid in cluster_face_ids]
                centroid = stage.compute_cluster_centroid(cluster_embeddings)

                new_cluster = FaceCluster(
                    representative_encoding=centroid,
                    status=ClusterStatus.PENDING,
                    face_count=len(cluster_face_ids),
                    needs_review=len(cluster_face_ids) < 3,
                )
                session.add(new_cluster)
                session.flush()

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
