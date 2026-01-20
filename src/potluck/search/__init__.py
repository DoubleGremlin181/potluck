"""Potluck Search Module.

Provides hybrid search combining PostgreSQL full-text search (FTS)
with pgvector semantic similarity, using Reciprocal Rank Fusion (RRF)
to blend results.

Usage:
    from potluck.search import search, SearchQuery, SearchMode

    # Standalone search (manages its own session)
    results = await search(SearchQuery(query="vacation photos"))

    # Search specific entity types
    results = await search(SearchQuery(
        query="birthday party",
        entity_types={EntityType.MEDIA, EntityType.CHAT_MESSAGE},
    ))

    # FTS only (keyword matching, supports Google-like syntax)
    results = await search(SearchQuery(
        query='"exact phrase" -excluded word',
        mode=SearchMode.FTS,
    ))

    # Vector only (semantic similarity)
    results = await search(SearchQuery(
        query="happy moments with friends",
        mode=SearchMode.VECTOR_TEXT,
    ))

    # Cross-modal (find images from text description)
    results = await search(SearchQuery(
        query="sunset at the beach",
        entity_types={EntityType.MEDIA},
        mode=SearchMode.VECTOR_MULTIMODAL,
    ))
"""

import asyncio
import time
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import Session

from potluck.core.exceptions import NoSearchableEntitiesError
from potluck.db.session import get_async_session, get_session
from potluck.models import get_entity_type_model_map
from potluck.models.base import EntityType, SourceType
from potluck.search.cache import get_search_cache, invalidate_search_cache
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
from potluck.search.utils import (
    get_model_priority_fields,
    get_primary_date_field,
    get_searchable_entity_types,
)

__all__ = [
    # Main API
    "search",
    "search_sync",
    "invalidate_search_cache",
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


async def search(
    query: SearchQuery,
    ranking_config: RankingConfig | None = None,
) -> SearchResults:
    """Execute a search query across entities (async).

    This is the main entry point for search operations. It manages its own
    database session, runs FTS and vector retrieval in parallel, fuses results
    using RRF, and enriches results with entity metadata.

    Results are cached with all-or-nothing invalidation on writes.

    Args:
        query: Search query with filters and mode.
        ranking_config: Optional ranking configuration. Uses defaults if None.

    Returns:
        SearchResults with ranked items.

    Raises:
        NoSearchableEntitiesError: If no searchable entities match the filter.
    """
    # Check cache first
    cache = get_search_cache()
    entity_types_key = frozenset(
        et.value for et in (query.entity_types or get_searchable_entity_types())
    )
    source_types_key = (
        frozenset(st.value for st in query.source_types) if query.source_types else None
    )

    cached = cache.get(
        query=query.query,
        entity_types=entity_types_key,
        mode=query.mode.value,
        limit=query.limit,
        offset=query.offset,
        since=query.since,
        until=query.until,
        source_types=source_types_key,
    )
    if cached is not None:
        return cached

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

    # Calculate effective limit per retriever (fetch more to allow for fusion)
    retriever_limit = query.limit * 3

    # Run retrievers in parallel using asyncio
    async with get_async_session() as session:
        retriever_results = await _run_retrievers_parallel(
            session, query, target_types, retriever_limit, config
        )

        # Fuse and rank results
        ranker = RRFRanker()
        ranked_items = ranker.rank(retriever_results, config)

        # Apply pagination
        total_count = len(ranked_items)
        paginated_items = ranked_items[query.offset : query.offset + query.limit]

        # Enrich results with entity metadata
        enriched_items = await _enrich_results_async(session, paginated_items)

    elapsed_ms = int((time.monotonic() - start_time) * 1000)

    results = SearchResults(
        query=query.query,
        mode=query.mode,
        entity_types_searched=list(target_types),
        total_count=total_count,
        items=enriched_items,
        took_ms=elapsed_ms,
    )

    # Cache results
    cache.set(
        query=query.query,
        entity_types=entity_types_key,
        mode=query.mode.value,
        limit=query.limit,
        offset=query.offset,
        since=query.since,
        until=query.until,
        source_types=source_types_key,
        results=results,
    )

    return results


async def _run_retrievers_parallel(
    session: AsyncSession,
    query: SearchQuery,
    target_types: set[EntityType],
    retriever_limit: int,
    config: RankingConfig,
) -> dict[str, list[RetrievalResult]]:
    """Run FTS and vector retrievers in parallel.

    Since retrievers currently use sync database operations, we run them
    in a thread pool executor to avoid blocking the event loop.
    """
    retriever_results: dict[str, list[RetrievalResult]] = {}

    # Create sync session for retrievers (they use SQLModel's sync Session)
    # We need to run these in the thread pool since they're sync operations
    loop = asyncio.get_event_loop()

    tasks = []

    if query.mode in (SearchMode.FTS, SearchMode.HYBRID):
        tasks.append(
            loop.run_in_executor(
                None,
                _run_fts_retriever,
                query.query,
                target_types,
                retriever_limit,
                query.since,
                query.until,
            )
        )
    if query.mode in (SearchMode.VECTOR_TEXT, SearchMode.HYBRID):
        tasks.append(
            loop.run_in_executor(
                None,
                _run_vector_retriever,
                query.query,
                target_types,
                retriever_limit,
                query.since,
                query.until,
                False,  # use_multimodal
            )
        )
    elif query.mode == SearchMode.VECTOR_MULTIMODAL:
        tasks.append(
            loop.run_in_executor(
                None,
                _run_vector_retriever,
                query.query,
                target_types,
                retriever_limit,
                query.since,
                query.until,
                True,  # use_multimodal
            )
        )
    # Run in parallel
    if tasks:
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Process results based on what retrievers were run
        result_idx = 0
        if query.mode in (SearchMode.FTS, SearchMode.HYBRID):
            fts_result = results[result_idx]
            result_idx += 1
            if isinstance(fts_result, list):
                retriever_results["fts"] = fts_result
            elif isinstance(fts_result, Exception):
                raise fts_result

        if query.mode in (SearchMode.VECTOR_TEXT, SearchMode.HYBRID, SearchMode.VECTOR_MULTIMODAL):
            vector_result = results[result_idx]
            if isinstance(vector_result, list):
                retriever_results["vector"] = vector_result
            elif isinstance(vector_result, Exception):
                raise vector_result

    return retriever_results


def _run_fts_retriever(
    query: str,
    target_types: set[EntityType],
    limit: int,
    since: datetime | None,
    until: datetime | None,
) -> list[RetrievalResult]:
    """Run FTS retriever in sync context."""
    for session in get_session():
        fts = FTSRetriever()
        return fts.retrieve(
            session,
            query,
            target_types,
            limit,
            since=since,
            until=until,
        )
    return []


def _run_vector_retriever(
    query: str,
    target_types: set[EntityType],
    limit: int,
    since: datetime | None,
    until: datetime | None,
    use_multimodal: bool,
) -> list[RetrievalResult]:
    """Run vector retriever in sync context."""
    for session in get_session():
        vector = VectorRetriever()
        return vector.retrieve(
            session,
            query,
            target_types,
            limit,
            since=since,
            until=until,
            use_multimodal=use_multimodal,
        )
    return []


def search_sync(
    session: Session,
    query: SearchQuery,
    ranking_config: RankingConfig | None = None,
) -> SearchResults:
    """Execute a search query across entities (sync).

    Synchronous version for use when an async context is not available.

    Args:
        session: Database session.
        query: Search query with filters and mode.
        ranking_config: Optional ranking configuration. Uses defaults if None.

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
        vector = VectorRetriever()
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
        vector = VectorRetriever()
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
    enriched_items = _enrich_results_sync(session, paginated_items)

    elapsed_ms = int((time.monotonic() - start_time) * 1000)

    return SearchResults(
        query=query.query,
        mode=query.mode,
        entity_types_searched=list(target_types),
        total_count=total_count,
        items=enriched_items,
        took_ms=elapsed_ms,
    )


async def _enrich_results_async(
    session: AsyncSession,
    items: list[SearchResultItem],
) -> list[SearchResultItem]:
    """Enrich search results with entity metadata (title, date, source) - async version.

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

        # Get model's search configuration using utility functions
        priority_fields = get_model_priority_fields(model)
        title_field = next(iter(priority_fields), None)
        date_field = get_primary_date_field(model)

        # Fetch entities
        entity_ids = [item.entity_id for item in type_items]
        id_col: Any = getattr(model, "id")  # noqa: B009
        stmt = select(model).where(id_col.in_(entity_ids))
        result = await session.execute(stmt)
        entities: dict[Any, Any] = {getattr(e, "id"): e for e in result.scalars().all()}  # noqa: B009

        # Enrich items
        for item in type_items:
            entity = entities.get(item.entity_id)
            if entity is None:
                continue

            # Set title from priority field
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


def _enrich_results_sync(
    session: Session,
    items: list[SearchResultItem],
) -> list[SearchResultItem]:
    """Enrich search results with entity metadata (title, date, source) - sync version.

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

        # Get model's search configuration using utility functions
        priority_fields = get_model_priority_fields(model)
        title_field = next(iter(priority_fields), None)
        date_field = get_primary_date_field(model)

        # Fetch entities
        entity_ids = [item.entity_id for item in type_items]
        id_col: Any = getattr(model, "id")  # noqa: B009
        stmt = select(model).where(id_col.in_(entity_ids))
        result = session.execute(stmt)
        entities: dict[Any, Any] = {getattr(e, "id"): e for e in result.scalars().all()}  # noqa: B009

        # Enrich items
        for item in type_items:
            entity = entities.get(item.entity_id)
            if entity is None:
                continue

            # Set title from priority field
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
