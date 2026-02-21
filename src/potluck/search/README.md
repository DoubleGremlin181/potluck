# Search Module

Hybrid search combining PostgreSQL full-text search (FTS) with pgvector semantic similarity, fused using Reciprocal Rank Fusion (RRF).

## Directory Structure

```
search/
├── __init__.py         # search() entry point, parallel retriever execution
├── dtos.py             # SearchQuery, SearchMode, SearchResultItem, RankingConfig
├── cache.py            # In-memory LRU cache with TTL
├── utils.py            # Model introspection (searchable fields, dates, priorities)
├── retrieval/
│   ├── base.py         # Retriever abstract base
│   ├── fts.py          # FTSRetriever (PostgreSQL tsvector)
│   └── vector.py       # VectorRetriever (pgvector cosine similarity)
└── ranking/
    ├── base.py         # Ranker abstract base
    └── rrf.py          # RRFRanker (reciprocal rank fusion)
```

## Architecture

```
SearchQuery
    |
    v
Cache check (SHA256 key)
    |  miss
    v
+------ asyncio.gather ------+
|                             |
v                             v
FTSRetriever            VectorRetriever
(websearch_to_tsquery)  (e5/SigLIP encode)
(ts_rank_cd scoring)    (cosine distance)
(ts_headline snippets)  (HNSW index)
|                             |
+-------- RRFRanker ---------+
          |
          v
    Enrich with metadata
    (title, date, source)
          |
          v
    Cache + return SearchResults
```

## Search Modes

| Mode | Value | Description |
|------|-------|-------------|
| FTS | `fts` | Keyword matching via PostgreSQL full-text search |
| Vector (text) | `vector_text` | 384-dim text-to-text semantic search (e5-small-v2) |
| Vector (multimodal) | `vector_multimodal` | 768-dim cross-modal search (SigLIP, text queries find images) |
| Hybrid | `hybrid` | FTS + vector text combined via RRF (default) |

## FTS Retriever

Uses PostgreSQL's `websearch_to_tsquery` for Google-like query syntax:

- **AND**: space between words (default)
- **OR**: `or` between words
- **Phrase**: `"quoted words"`
- **Negation**: `-word`

Scoring uses `ts_rank_cd` (cover density ranking, considers word proximity). Snippets are generated with `ts_headline` using `<<` / `>>` as highlight delimiters.

Queries are built as `UNION ALL` across all searchable entity tables, with date filtering applied before ordering and limiting.

## Vector Retriever

Supports two embedding modes:

- **Text** (384d, e5-small-v2): Best for finding conceptually similar text. Query prefix: `"query: "` (required by e5 models).
- **Multimodal** (768d, SigLIP): Cross-modal search where text queries find images. Encoded via `MLModels.encode_text_multimodal()`.

Uses pgvector's `<=>` operator (cosine distance) with HNSW indexes. Distance is converted to similarity (`1 - distance`) for consistent "higher is better" semantics.

## RRF Ranker

Reciprocal Rank Fusion combines ranked lists without requiring score normalization:

```
score = sum(weight / (k + rank))
```

Default configuration:
- FTS weight: **0.3**
- Vector weight: **0.7**
- k constant: **60** (dampens high rankings)

Configured via `RankingConfig` (Pydantic model with validation).

## Caching

In-memory LRU cache with all-or-nothing invalidation:

- **Key generation**: SHA256 hash of query + entity types + mode + limit + offset + date range + source types
- **TTL**: 5 minutes (300 seconds)
- **Max size**: 1000 entries (oldest evicted on overflow)
- **Invalidation**: `invalidate_search_cache()` clears everything; call on any write to a searchable entity

Global singleton accessed via `get_search_cache()`.

## Entity Search Configuration

Models opt into search via class-level attributes:

```python
class ChatMessage(SearchableEntity):
    __searchable__ = True
    __search_exclude_fields__ = {"sender_id", "thread_id"}
    __search_priority_fields__ = {"content"}
    __search_date_fields__ = {"sent_at"}
```

| Attribute | Purpose |
|-----------|---------|
| `__searchable__` | Enables search for this entity type |
| `__search_exclude_fields__` | Fields to exclude from FTS indexing |
| `__search_priority_fields__` | Fields with weight 'A' in FTS (used for titles/snippets) |
| `__search_date_fields__` | Date fields for range filtering |

The `utils.py` module auto-discovers text fields via SQLAlchemy column introspection, filtering out IDs, hashes, URLs, paths, and other non-searchable fields by default.

## Graceful Degradation

In hybrid mode, if one retriever fails, search continues with the other:

- FTS fails: logs warning, returns vector-only results
- Vector fails: logs warning, returns FTS-only results
- Both fail: raises `SearchError`

This ensures search remains available even when embeddings are not yet generated or the FTS index is temporarily unavailable.

## Usage

```python
from potluck.search import search, SearchQuery, SearchMode

# Default hybrid search
results = await search(SearchQuery(query="vacation photos"))

# FTS only (Google-like syntax)
results = await search(SearchQuery(
    query='"birthday party" -work',
    mode=SearchMode.FTS,
))

# Cross-modal: find images from text
results = await search(SearchQuery(
    query="sunset at the beach",
    entity_types={EntityType.MEDIA},
    mode=SearchMode.VECTOR_MULTIMODAL,
))
```
