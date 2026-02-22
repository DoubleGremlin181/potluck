"""Face clustering task using DBSCAN.

This module provides the batch clustering task for grouping unclustered face
embeddings into clusters. Unlike per-media processors, clustering operates
on all unclustered faces at once.
"""

from typing import Any

from celery import Task
from celery.exceptions import Reject, Retry
from sqlmodel import Session, select

from potluck.core.celery import (
    MAX_RETRIES,
    RETRY_BACKOFF,
    RETRY_BACKOFF_MAX,
    celery_app,
    is_transient_error,
)
from potluck.core.logging import get_logger
from potluck.db.session import get_engine
from potluck.models.faces import ClusterStatus, FaceCluster, MediaPersonLink
from potluck.pipeline.processing.processors.faces import FaceProcessor

logger = get_logger(__name__)


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True,
    queue="pipeline",
    autoretry_for=(Retry,),
    retry_backoff=RETRY_BACKOFF,
    retry_backoff_max=RETRY_BACKOFF_MAX,
    max_retries=MAX_RETRIES,
    acks_late=True,
)
def cluster_unassigned_faces(self: "Task[..., dict[str, Any]]") -> dict[str, Any]:
    """Run DBSCAN clustering on unclustered detected faces.

    This is a batch operation that:
    1. Finds all MediaPersonLink records without a cluster_id
    2. Clusters their embeddings using DBSCAN
    3. Creates FaceCluster records for each cluster
    4. Updates MediaPersonLink records with their cluster assignments

    Returns:
        Dict with clustering statistics.
    """
    logger.info("Starting face clustering task")

    try:
        engine = get_engine()
        with Session(engine) as session:
            stmt = select(MediaPersonLink).where(
                MediaPersonLink.cluster_id == None,  # noqa: E711
                MediaPersonLink.embedding != None,  # noqa: E711
            )
            result = session.exec(stmt)
            unclustered_faces = list(result.all())

            if not unclustered_faces:
                logger.info("No unclustered faces to process")
                return {
                    "status": "completed",
                    "faces_processed": 0,
                    "clusters_created": 0,
                    "faces_assigned": 0,
                }

            embeddings = [
                list(face.embedding) for face in unclustered_faces if face.embedding is not None
            ]
            face_ids = [face.id for face in unclustered_faces if face.embedding is not None]

            processor = FaceProcessor()
            clusters = processor.cluster_embeddings(embeddings, face_ids)

            clusters_created = 0
            faces_assigned = 0

            for label, cluster_face_ids in clusters.items():
                if label == -1:
                    continue

                cluster_embeddings = [embeddings[face_ids.index(fid)] for fid in cluster_face_ids]
                centroid = processor.compute_cluster_centroid(cluster_embeddings)

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
