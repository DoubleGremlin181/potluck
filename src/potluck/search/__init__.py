"""Potluck Search Module.

Provides hybrid search combining PostgreSQL full-text search (FTS)
with pgvector semantic similarity, using Reciprocal Rank Fusion (RRF)
to blend results.

Usage:
    from potluck.search import search, SearchQuery, SearchMode

    with get_session() as session:
        # Search all entities (default hybrid mode)
        results = search(session, SearchQuery(query="vacation photos"))

        # Search specific entity types
        results = search(session, SearchQuery(
            query="birthday party",
            entity_types={EntityType.MEDIA, EntityType.CHAT_MESSAGE},
        ))

        # FTS only (keyword matching)
        results = search(session, SearchQuery(
            query="meeting notes",
            mode=SearchMode.FTS,
        ))

        # Vector only (semantic similarity)
        results = search(session, SearchQuery(
            query="happy moments with friends",
            mode=SearchMode.VECTOR_TEXT,
        ))

        # Cross-modal (find images from text description)
        results = search(session, SearchQuery(
            query="sunset at the beach",
            entity_types={EntityType.MEDIA},
            mode=SearchMode.VECTOR_MULTIMODAL,
        ))
"""

import time
from datetime import datetime
from typing import Any

from sqlmodel import Session, select

from potluck.core.exceptions import NoSearchableEntitiesError
from potluck.models import get_entity_type_model_map
from potluck.models.base import EntityType, SourceType
from potluck.pipeline.processing.core.ml import MLModels
from potluck.search.dtos import (
    RankingConfig,
    RetrievalResult,
    SearchMode,
    SearchQuery,
    SearchResultItem,
    SearchResults,
)
from potluck.search.ranking import RRFRanker
from potluck.search.retrieval import FTSRetriever, VectorRetriever

__all__ = [
    # Main API
    "search",
    # DTOs
    "SearchMode",
    "SearchQuery",
    "SearchResults",
    "SearchResultItem",
    "RankingConfig",
    "RetrievalResult",
    # Retrievers (for advanced usage)
    "FTSRetriever",
    "VectorRetriever",
    # Rankers (for advanced usage)
    "RRFRanker",
    # Utilities
    "get_searchable_entity_types",
]


def get_searchable_entity_types() -> set[EntityType]:
    """Get all entity types that support search.

    Returns:
        Set of EntityType values that have __searchable__ = True.
    """
    entity_map = get_entity_type_model_map()
    return {et for et, model in entity_map.items() if getattr(model, "__searchable__", False)}


def search(
    session: Session,
    query: SearchQuery,
    ranking_config: RankingConfig | None = None,
    models: MLModels | None = None,
) -> SearchResults:
    """Execute a search query across entities.

    This is the main entry point for search operations. It orchestrates
    retrieval from FTS and/or vector backends, fuses results using RRF,
    and enriches results with entity metadata.

    Args:
        session: Database session.
        query: Search query with filters and mode.
        ranking_config: Optional ranking configuration. Uses defaults if None.
        models: Optional MLModels instance for query encoding in vector search.

    Returns:
        SearchResults with ranked items.

    Raises:
        NoSearchableEntitiesError: If no searchable entities match the filter.
    """
    start_time = time.monotonic()

    # Determine which entity types to search
    searchable = get_searchable_entity_types()
    target_types = query.entity_types & searchable if query.entity_types is not None else searchable

    if not target_types:
        raise NoSearchableEntitiesError(
            f"No searchable entities found. Requested: {query.entity_types}, "
            f"Available: {searchable}"
        )

    # Use default ranking config if not provided
    config = ranking_config or RankingConfig()

    # Run retrievers based on mode
    retriever_results: dict[str, list[RetrievalResult]] = {}

    # Calculate effective limit per retriever (fetch more to allow for fusion)
    retriever_limit = query.limit * 3

    if query.mode in (SearchMode.FTS, SearchMode.HYBRID):
        fts = FTSRetriever()
        retriever_results["fts"] = fts.retrieve(
            session,
            query.query,
            target_types,
            retriever_limit,
            since=query.since,
            until=query.until,
        )

    if query.mode in (SearchMode.VECTOR_TEXT, SearchMode.HYBRID):
        vector = VectorRetriever(models=models)
        retriever_results["vector"] = vector.retrieve(
            session,
            query.query,
            target_types,
            retriever_limit,
            since=query.since,
            until=query.until,
            use_multimodal=False,
        )

    if query.mode == SearchMode.VECTOR_MULTIMODAL:
        vector = VectorRetriever(models=models)
        retriever_results["vector"] = vector.retrieve(
            session,
            query.query,
            target_types,
            retriever_limit,
            since=query.since,
            until=query.until,
            use_multimodal=True,
        )

    # Fuse and rank results
    ranker = RRFRanker()
    ranked_items = ranker.rank(retriever_results, config)

    # Apply pagination
    total_count = len(ranked_items)
    paginated_items = ranked_items[query.offset : query.offset + query.limit]

    # Enrich results with entity metadata
    enriched_items = _enrich_results(session, paginated_items)

    elapsed_ms = int((time.monotonic() - start_time) * 1000)

    return SearchResults(
        query=query.query,
        mode=query.mode,
        entity_types_searched=list(target_types),
        total_count=total_count,
        items=enriched_items,
        took_ms=elapsed_ms,
    )


def _enrich_results(
    session: Session,
    items: list[SearchResultItem],
) -> list[SearchResultItem]:
    """Enrich search results with entity metadata (title, date, source).

    Batches database queries by entity type for efficiency.
    """
    if not items:
        return items

    entity_map = get_entity_type_model_map()

    # Group items by entity type for batch loading
    items_by_type: dict[EntityType, list[SearchResultItem]] = {}
    for item in items:
        items_by_type.setdefault(item.entity_type, []).append(item)

    # Load metadata for each entity type
    for entity_type, type_items in items_by_type.items():
        model = entity_map.get(entity_type)
        if model is None:
            continue

        # Get model's search configuration
        title_field: str | None = getattr(model, "__search_title_field__", None)
        date_field: str = getattr(model, "__search_date_field__", "created_at")

        # Fetch entities
        entity_ids = [item.entity_id for item in type_items]
        id_col: Any = getattr(model, "id")  # noqa: B009
        stmt = select(model).where(id_col.in_(entity_ids))
        result = session.execute(stmt)
        entities = {e.id: e for e in result.scalars().all()}

        # Enrich items
        for item in type_items:
            entity = entities.get(item.entity_id)
            if entity is None:
                continue

            # Set title
            if title_field:
                item.title = getattr(entity, title_field, None)

            # Set occurred_at
            date_value = getattr(entity, date_field, None)
            if isinstance(date_value, datetime):
                item.occurred_at = date_value

            # Set source_type
            source_type = getattr(entity, "source_type", None)
            if isinstance(source_type, SourceType):
                item.source_type = source_type

    return items
