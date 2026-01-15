"""PostgreSQL full-text search retriever."""

from datetime import datetime
from typing import Any

from sqlalchemy import func, literal, select, union_all
from sqlalchemy.sql import Select
from sqlmodel import Session, SQLModel

from potluck.models import get_entity_type_model_map
from potluck.models.base import EntityType
from potluck.search.dtos import RetrievalResult
from potluck.search.retrieval.base import Retriever


def get_searchable_models() -> dict[EntityType, type[SQLModel]]:
    """Get all models that have search enabled.

    Returns models that have __searchable__ = True class attribute.
    """
    entity_map = get_entity_type_model_map()
    return {
        et: model for et, model in entity_map.items() if getattr(model, "__searchable__", False)
    }


class FTSRetriever(Retriever):
    """PostgreSQL full-text search retriever.

    Uses tsvector columns and GIN indexes for fast keyword matching.
    Scores results using ts_rank_cd (cover density ranking).

    FTS is best for:
    - Exact keyword matching
    - Boolean queries (AND/OR)
    - Phrase matching
    - Prefix matching
    """

    def retrieve(
        self,
        session: Session,
        query: str,
        entity_types: set[EntityType],
        limit: int,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[RetrievalResult]:
        """Retrieve results using PostgreSQL full-text search.

        Builds a UNION ALL query across all searchable entity tables,
        filtering by entity type and date range.
        """
        searchable = get_searchable_models()

        # Filter to requested entity types that are actually searchable
        target_types = entity_types & set(searchable.keys())
        if not target_types:
            return []

        # Build tsquery from user input
        # plainto_tsquery handles user input safely (splits on spaces, ANDs terms)
        tsquery = func.plainto_tsquery("english", query)

        # Build subqueries for each entity type
        subqueries = []
        for entity_type in target_types:
            model = searchable[entity_type]
            subquery = self._build_entity_query(
                model,
                entity_type,
                tsquery,
                limit,
                since=since,
                until=until,
            )
            if subquery is not None:
                subqueries.append(subquery)

        if not subqueries:
            return []

        # Combine with UNION ALL and order by score
        combined = union_all(*subqueries).subquery()
        final_query = (
            select(
                combined.c.entity_type,
                combined.c.entity_id,
                combined.c.score,
                combined.c.snippet,
            )
            .order_by(combined.c.score.desc())
            .limit(limit)
        )

        result = session.execute(final_query)
        rows = result.fetchall()

        # Convert to RetrievalResult with rank
        return [
            RetrievalResult(
                entity_type=EntityType(row.entity_type),
                entity_id=row.entity_id,
                score=float(row.score),
                rank=idx + 1,
                snippet=row.snippet,
            )
            for idx, row in enumerate(rows)
        ]

    def _build_entity_query(
        self,
        model: type[SQLModel],
        entity_type: EntityType,
        tsquery: Any,
        limit: int,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> Select[Any] | None:
        """Build a SELECT query for a single entity type.

        Returns a query that selects entity_type, entity_id, score, and snippet.
        """
        # Get search configuration from model class attributes
        text_fields: list[str] = getattr(model, "__search_text_fields__", [])
        title_field: str | None = getattr(model, "__search_title_field__", None)
        date_field: str = getattr(model, "__search_date_field__", "created_at")

        if not text_fields:
            return None

        # Build headline (snippet) from title or first text field
        headline_field = title_field or text_fields[0]
        headline_col = getattr(model, headline_field, None)

        if headline_col is None:
            return None

        # ts_headline generates highlighted snippets
        snippet = func.ts_headline(
            "english",
            func.coalesce(headline_col, ""),
            tsquery,
            "MaxWords=35, MinWords=15, StartSel=<<, StopSel=>>",
        )

        # ts_rank_cd uses cover density ranking (considers proximity)
        search_vector_col = getattr(model, "search_vector", None)
        if search_vector_col is None:
            return None

        score = func.ts_rank_cd(search_vector_col, tsquery)

        # Get the id column (all searchable models have id from base classes)
        id_col: Any = getattr(model, "id")  # noqa: B009

        # Build query
        query = (
            select(
                literal(entity_type.value).label("entity_type"),
                id_col.label("entity_id"),
                score.label("score"),
                snippet.label("snippet"),
            )
            .where(search_vector_col.op("@@")(tsquery))
            .order_by(score.desc())
            .limit(limit)
        )

        # Add date filtering if the model has the date field
        date_col = getattr(model, date_field, None)
        if date_col is not None:
            if since is not None:
                query = query.where(date_col >= since)
            if until is not None:
                query = query.where(date_col <= until)

        return query
