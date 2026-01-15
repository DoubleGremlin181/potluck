"""pgvector semantic similarity retriever."""

from datetime import datetime
from typing import Any

from sqlalchemy import literal, select, text, union_all
from sqlalchemy.sql import Select
from sqlmodel import Session, SQLModel

from potluck.models import get_entity_type_model_map
from potluck.models.base import EntityType
from potluck.pipeline.processing.core.ml import MLModels
from potluck.search.dtos import RetrievalResult
from potluck.search.retrieval.base import Retriever


def get_searchable_models() -> dict[EntityType, type[SQLModel]]:
    """Get all models that have search enabled."""
    entity_map = get_entity_type_model_map()
    return {
        et: model for et, model in entity_map.items() if getattr(model, "__searchable__", False)
    }


class VectorRetriever(Retriever):
    """pgvector semantic similarity retriever.

    Supports two embedding modes:
    - Text (384d e5-small-v2): Text-to-text semantic search
    - Multimodal (768d SigLIP): Cross-modal search (text queries find images)

    Uses cosine similarity via pgvector's <=> operator with HNSW indexes.

    Vector search is best for:
    - Semantic similarity (finding conceptually related content)
    - Cross-modal search (text to image)
    - Handling synonyms and paraphrases
    """

    def __init__(self, models: MLModels | None = None) -> None:
        """Initialize the vector retriever.

        Args:
            models: MLModels instance for query encoding. If None, creates one.
        """
        self._models = models

    @property
    def models(self) -> MLModels:
        """Lazy-load MLModels instance."""
        if self._models is None:
            self._models = MLModels()
        return self._models

    def retrieve(
        self,
        session: Session,
        query: str,
        entity_types: set[EntityType],
        limit: int,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        use_multimodal: bool = False,
    ) -> list[RetrievalResult]:
        """Retrieve results using vector similarity search.

        Args:
            session: Database session.
            query: Search query string.
            entity_types: Entity types to search across.
            limit: Maximum number of results.
            since: Only return results after this datetime.
            until: Only return results before this datetime.
            use_multimodal: If True, use 768d multimodal embeddings for
                cross-modal search. If False, use 384d text embeddings.
        """
        searchable = get_searchable_models()

        # Filter to requested entity types that are actually searchable
        target_types = entity_types & set(searchable.keys())
        if not target_types:
            return []

        # Encode query
        if use_multimodal:
            query_embedding = self.models.encode_text_multimodal(query)
            embedding_column = "multimodal_embedding"
        else:
            # e5 models require "query: " prefix for queries
            query_embedding = (
                self.models.get_text_encoder()
                .encode(
                    f"query: {query}",
                    normalize_embeddings=True,
                )
                .tolist()
            )
            embedding_column = "embedding"

        # Build subqueries for each entity type
        subqueries = []
        for entity_type in target_types:
            model = searchable[entity_type]
            subquery = self._build_entity_query(
                model,
                entity_type,
                query_embedding,
                embedding_column,
                limit,
                since=since,
                until=until,
            )
            if subquery is not None:
                subqueries.append(subquery)

        if not subqueries:
            return []

        # Combine with UNION ALL and order by similarity (ascending distance)
        combined = union_all(*subqueries).subquery()
        final_query = (
            select(
                combined.c.entity_type,
                combined.c.entity_id,
                combined.c.similarity,
            )
            .order_by(combined.c.similarity.desc())
            .limit(limit)
        )

        result = session.execute(final_query)
        rows = result.fetchall()

        # Convert to RetrievalResult with rank
        return [
            RetrievalResult(
                entity_type=EntityType(row.entity_type),
                entity_id=row.entity_id,
                score=float(row.similarity),
                rank=idx + 1,
                snippet=None,  # Vector search doesn't produce snippets
            )
            for idx, row in enumerate(rows)
        ]

    def _build_entity_query(
        self,
        model: type[SQLModel],
        entity_type: EntityType,
        query_embedding: list[float],
        embedding_column: str,
        limit: int,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> Select[Any] | None:
        """Build a SELECT query for a single entity type.

        Returns a query that selects entity_type, entity_id, and similarity score.
        """
        # Get the embedding column
        emb_col = getattr(model, embedding_column, None)
        if emb_col is None:
            return None

        # Get date field for filtering
        date_field: str = getattr(model, "__search_date_field__", "created_at")
        date_col = getattr(model, date_field, None)

        # Convert query embedding to PostgreSQL vector format
        # pgvector expects format: '[0.1, 0.2, ...]'
        vector_literal = f"[{','.join(str(x) for x in query_embedding)}]"

        # Cosine similarity = 1 - cosine_distance
        # pgvector's <=> operator returns cosine distance (0 = identical, 2 = opposite)
        # We convert to similarity for consistent "higher is better" semantics
        # Use text() to create a properly typed SQL expression for the vector literal
        cosine_distance = emb_col.op("<=>")(text(f"'{vector_literal}'::vector"))
        similarity = (1 - cosine_distance).label("similarity")

        # Get the id column (all searchable models have id from base classes)
        id_col: Any = getattr(model, "id")  # noqa: B009

        # Build query - only include rows with embeddings
        query = (
            select(
                literal(entity_type.value).label("entity_type"),
                id_col.label("entity_id"),
                similarity,
            )
            .where(emb_col.is_not(None))
            .order_by(cosine_distance)  # Order by distance (ascending)
            .limit(limit)
        )

        # Add date filtering
        if date_col is not None:
            if since is not None:
                query = query.where(date_col >= since)
            if until is not None:
                query = query.where(date_col <= until)

        return query
