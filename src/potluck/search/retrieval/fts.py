"""PostgreSQL full-text search retriever."""

from datetime import datetime
from typing import Any

from sqlalchemy import func, literal, select, union_all
from sqlalchemy.sql import Select
from sqlmodel import Session, SQLModel

from potluck.models.base import EntityType
from potluck.search.dtos import RetrievalResult
from potluck.search.retrieval.base import Retriever
from potluck.search.utils import (
    get_model_priority_fields,
    get_model_text_fields,
    get_primary_date_field,
    get_searchable_models,
)


class FTSRetriever(Retriever):
    """PostgreSQL full-text search retriever.

    Uses tsvector columns and GIN indexes for fast keyword matching.
    Scores results using ts_rank_cd (cover density ranking).

    Supports Google-like search syntax via websearch_to_tsquery:
    - AND: space between words (default)
    - OR: "or" between words
    - Phrase: "quoted words"
    - Negation: -word
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

        # Build tsquery from user input using websearch_to_tsquery
        # Supports Google-like syntax: AND (space), OR, "phrase", -negation
        tsquery = func.websearch_to_tsquery("english", query)

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
        Date filtering is applied BEFORE ordering and limiting for correct results.
        """
        # Get search configuration using utility functions
        text_fields = get_model_text_fields(model)
        priority_fields = get_model_priority_fields(model)
        date_field = get_primary_date_field(model)

        if not text_fields:
            return None

        # Build headline (snippet) from priority field or first text field
        headline_field = next(iter(priority_fields), None) or (
            text_fields[0] if text_fields else None
        )
        if headline_field is None:
            return None

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

        # Build base query with FTS match filter
        query = select(
            literal(entity_type.value).label("entity_type"),
            id_col.label("entity_id"),
            score.label("score"),
            snippet.label("snippet"),
        ).where(search_vector_col.op("@@")(tsquery))

        # Apply date filtering BEFORE ordering and limiting (correct order of operations)
        date_col = getattr(model, date_field, None)
        if date_col is not None:
            if since is not None:
                query = query.where(date_col >= since)
            if until is not None:
                query = query.where(date_col <= until)

        # Apply ordering and limit after all filters
        query = query.order_by(score.desc()).limit(limit)

        return query
