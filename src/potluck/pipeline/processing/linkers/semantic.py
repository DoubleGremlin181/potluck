"""Semantic linker for content-based entity relationships.

Creates SIMILAR links between entities based on embedding vector similarity.
Uses cosine similarity to compare entity embeddings.
"""

from typing import ClassVar
from uuid import UUID

import numpy as np
from sqlmodel import Session, select

from potluck.core.logging import get_logger
from potluck.models.base import EntityType
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
    """

    NAME: ClassVar[str] = "semantic"
    LINK_TYPES: ClassVar[set[LinkType]] = {LinkType.SIMILAR}

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
    ) -> list[EntityLink]:
        """Find semantic links between entities of the same type.

        Args:
            session: Database session.
            entity_type: Type of entities to analyze.
            entity_ids: List of entity IDs.

        Returns:
            List of SIMILAR EntityLink records.
        """
        if len(entity_ids) < 2:
            return []

        # Route to appropriate handler based on entity type
        if entity_type == EntityType.MEDIA:
            return self._find_media_links(session, entity_ids)
        elif entity_type == EntityType.KNOWLEDGE_NOTE:
            return self._find_note_links(session, entity_ids)
        else:
            # Other entity types don't have embeddings yet
            logger.debug(f"Entity type {entity_type} has no embedding support")
            return []

    def _find_media_links(
        self,
        session: Session,
        entity_ids: list[UUID],
    ) -> list[EntityLink]:
        """Find semantic links between media entities via MediaEmbedding.

        Args:
            session: Database session.
            entity_ids: List of media IDs.

        Returns:
            List of SIMILAR EntityLink records.
        """
        # Fetch embeddings for these media items
        stmt = (
            select(MediaEmbedding)
            .where(MediaEmbedding.media_id.in_(entity_ids))  # type: ignore[attr-defined]
            .where(MediaEmbedding.embedding_type == self._embedding_type)
        )
        result = session.execute(stmt)
        embeddings = list(result.scalars().all())

        if len(embeddings) < 2:
            return []

        # Build embedding lookup
        embedding_map: dict[UUID, list[float]] = {e.media_id: e.embedding for e in embeddings}

        # Compare all pairs
        links: list[EntityLink] = []
        media_ids = list(embedding_map.keys())

        for i, id_a in enumerate(media_ids):
            vec_a = embedding_map[id_a]
            for id_b in media_ids[i + 1 :]:
                vec_b = embedding_map[id_b]

                similarity = cosine_similarity(vec_a, vec_b)

                if similarity >= self._similarity_threshold:
                    links.append(
                        EntityLink(
                            source_type=EntityType.MEDIA,
                            source_id=id_a,
                            target_type=EntityType.MEDIA,
                            target_id=id_b,
                            link_type=LinkType.SIMILAR,
                            confidence=similarity,
                        )
                    )

        logger.debug(f"Found {len(links)} semantic links for media")
        return links

    def _find_note_links(
        self,
        session: Session,
        entity_ids: list[UUID],
    ) -> list[EntityLink]:
        """Find semantic links between knowledge notes via inline embeddings.

        Args:
            session: Database session.
            entity_ids: List of note IDs.

        Returns:
            List of SIMILAR EntityLink records.
        """
        # Fetch notes with embeddings
        stmt = (
            select(KnowledgeNote)
            .where(KnowledgeNote.id.in_(entity_ids))  # type: ignore[attr-defined]
            .where(KnowledgeNote.embedding.isnot(None))  # type: ignore[union-attr]
        )
        result = session.execute(stmt)
        notes = list(result.scalars().all())

        if len(notes) < 2:
            return []

        # Build embedding lookup
        embedding_map: dict[UUID, list[float]] = {
            n.id: n.embedding for n in notes if n.embedding is not None
        }

        # Compare all pairs
        links: list[EntityLink] = []
        note_ids = list(embedding_map.keys())

        for i, id_a in enumerate(note_ids):
            vec_a = embedding_map[id_a]
            for id_b in note_ids[i + 1 :]:
                vec_b = embedding_map[id_b]

                similarity = cosine_similarity(vec_a, vec_b)

                if similarity >= self._similarity_threshold:
                    links.append(
                        EntityLink(
                            source_type=EntityType.KNOWLEDGE_NOTE,
                            source_id=id_a,
                            target_type=EntityType.KNOWLEDGE_NOTE,
                            target_id=id_b,
                            link_type=LinkType.SIMILAR,
                            confidence=similarity,
                        )
                    )

        logger.debug(f"Found {len(links)} semantic links for notes")
        return links
