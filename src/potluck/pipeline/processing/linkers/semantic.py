"""Semantic linker for content-based entity relationships.

Creates SIMILAR links between entities based on embedding vector similarity.
Uses cosine similarity to compare entity embeddings.
"""

from collections.abc import Iterator
from typing import ClassVar
from uuid import UUID

import numpy as np
from sqlmodel import Session, col, select

from potluck.core.logging import get_logger
from potluck.models.base import EntityType
from potluck.models.documents import Document
from potluck.models.links import EntityLink, LinkType
from potluck.models.media import EmbeddingType, MediaEmbedding
from potluck.models.notes import KnowledgeNote
from potluck.pipeline.processing.linkers.base import BaseLinker

logger = get_logger(__name__)


# Default similarity threshold for creating links
DEFAULT_SIMILARITY_THRESHOLD = 0.8


def cosine_similarity(vec1: list[float], vec2: list[float]) -> float:
    """Calculate cosine similarity between two vectors.

    Args:
        vec1: First embedding vector.
        vec2: Second embedding vector.

    Returns:
        Cosine similarity score (0.0 to 1.0).
    """
    a = np.array(vec1)
    b = np.array(vec2)

    # Handle edge cases
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0

    return float(np.dot(a, b) / (norm_a * norm_b))


class SemanticLinker(BaseLinker):
    """Linker for semantic similarity relationships between entities.

    Creates SIMILAR links between entities with embedding vectors that have
    cosine similarity above a configurable threshold.

    Supports:
    - Media entities via MediaEmbedding table (CLIP, OCR, caption embeddings)
    - KnowledgeNote entities via inline embedding field
    - Document entities via inline embedding field
    """

    NAME: ClassVar[str] = "semantic"
    LINK_TYPES: ClassVar[set[LinkType]] = {LinkType.SIMILAR}
    SUPPORTED_ENTITY_TYPES: ClassVar[set[EntityType]] = {
        EntityType.MEDIA,
        EntityType.KNOWLEDGE_NOTE,
        EntityType.DOCUMENT,
    }

    def __init__(
        self,
        *,
        similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
        embedding_type: EmbeddingType = EmbeddingType.CLIP,
    ) -> None:
        """Initialize the semantic linker.

        Args:
            similarity_threshold: Minimum cosine similarity to create links.
            embedding_type: Type of embedding to use for media comparisons.
        """
        self._similarity_threshold = similarity_threshold
        self._embedding_type = embedding_type

    def find_links(
        self,
        session: Session,
        entity_type: EntityType,
        entity_ids: list[UUID],
    ) -> Iterator[EntityLink]:
        """Find semantic links between entities of the same type.

        Args:
            session: Database session.
            entity_type: Type of entities to analyze.
            entity_ids: List of entity IDs.

        Yields:
            SIMILAR EntityLink records.
        """
        if len(entity_ids) < 2:
            return

        # Route to appropriate handler based on entity type
        if entity_type == EntityType.MEDIA:
            yield from self._find_media_links(session, entity_ids)
        elif entity_type == EntityType.KNOWLEDGE_NOTE:
            yield from self._find_note_links(session, entity_ids)
        elif entity_type == EntityType.DOCUMENT:
            yield from self._find_document_links(session, entity_ids)
        else:
            logger.debug(f"Entity type {entity_type} has no embedding support")

    def _find_pairwise_links(
        self,
        embedding_map: dict[UUID, list[float]],
        entity_type: EntityType,
        label: str,
    ) -> Iterator[EntityLink]:
        """Yield SIMILAR links for all embedding pairs above the threshold.

        Args:
            embedding_map: Mapping of entity ID to embedding vector.
            entity_type: The entity type for created links.
            label: Label for debug logging.

        Yields:
            SIMILAR EntityLink records.
        """
        ids = list(embedding_map.keys())
        link_count = 0

        for i, id_a in enumerate(ids):
            vec_a = embedding_map[id_a]
            for id_b in ids[i + 1 :]:
                similarity = cosine_similarity(vec_a, embedding_map[id_b])
                if similarity >= self._similarity_threshold:
                    link_count += 1
                    yield EntityLink(
                        source_type=entity_type,
                        source_id=id_a,
                        target_type=entity_type,
                        target_id=id_b,
                        link_type=LinkType.SIMILAR,
                        confidence=similarity,
                    )

        logger.debug("Found %d semantic links for %s", link_count, label)

    def _find_media_links(self, session: Session, entity_ids: list[UUID]) -> Iterator[EntityLink]:
        """Find semantic links between media entities via MediaEmbedding."""
        stmt = (
            select(MediaEmbedding)
            .where(col(MediaEmbedding.media_id).in_(entity_ids))
            .where(MediaEmbedding.embedding_type == self._embedding_type)
        )
        embeddings = list(session.exec(stmt).all())
        if len(embeddings) < 2:
            return
        embedding_map = {e.media_id: e.embedding for e in embeddings}
        yield from self._find_pairwise_links(embedding_map, EntityType.MEDIA, "media")

    def _find_note_links(self, session: Session, entity_ids: list[UUID]) -> Iterator[EntityLink]:
        """Find semantic links between knowledge notes via inline embeddings."""
        stmt = (
            select(KnowledgeNote)
            .where(col(KnowledgeNote.id).in_(entity_ids))
            .where(col(KnowledgeNote.embedding).isnot(None))
        )
        notes = list(session.exec(stmt).all())
        if len(notes) < 2:
            return
        embedding_map = {n.id: n.embedding for n in notes if n.embedding is not None}
        yield from self._find_pairwise_links(embedding_map, EntityType.KNOWLEDGE_NOTE, "notes")

    def _find_document_links(
        self, session: Session, entity_ids: list[UUID]
    ) -> Iterator[EntityLink]:
        """Find semantic links between documents via inline embeddings."""
        stmt = (
            select(Document)
            .where(col(Document.id).in_(entity_ids))
            .where(col(Document.embedding).isnot(None))
        )
        documents = list(session.exec(stmt).all())
        if len(documents) < 2:
            return
        embedding_map = {d.id: d.embedding for d in documents if d.embedding is not None}
        yield from self._find_pairwise_links(embedding_map, EntityType.DOCUMENT, "documents")
