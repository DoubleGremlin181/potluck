# Potluck Architecture

Potluck is a privacy-first personal knowledge database that ingests data from
various sources (Google Takeout, Reddit, WhatsApp, etc.), processes it with ML
models, and exposes it to LLMs via the Model Context Protocol (MCP). All
processing runs locally -- no data ever leaves your machine.

This document covers the system design, data flow, key abstractions, and the
reasoning behind architectural decisions. It is written for engineers who are
onboarding to the project.

---

## Table of Contents

1. [System Overview](#system-overview)
2. [Technology Stack](#technology-stack)
3. [Project Structure](#project-structure)
4. [Entity Model Hierarchy](#entity-model-hierarchy)
5. [Data Flow](#data-flow)
6. [Pipeline Architecture](#pipeline-architecture)
7. [Processing Pipeline](#processing-pipeline)
8. [Entity Linking](#entity-linking)
9. [Search System](#search-system)
10. [Web Interface](#web-interface)
11. [MCP Server](#mcp-server)
12. [Database Design](#database-design)
13. [ML Model Management](#ml-model-management)
14. [Deployment Architecture](#deployment-architecture)
15. [Key Design Decisions](#key-design-decisions)

---

## System Overview

```
+------------------+     +------------------+     +------------------+
|                  |     |                  |     |                  |
|   Data Sources   |     |     Potluck      |     |   LLM Clients   |
|                  |     |                  |     |                  |
|  Google Takeout  +---->+  Ingest + Process +---->+  Claude Desktop  |
|  Reddit Export   |     |  Store + Search   |     |  Other MCP hosts |
|  WhatsApp Chat   |     |  Link + Expose    |     |                  |
|  YNAB Budget     |     |                  |     |                  |
|  Image Folders   |     +--------+---------+     +------------------+
|  MBOX Email      |              |
|  Text Files      |              v
|  Android Timeline|     +--------+---------+
+------------------+     |                  |
                         |  PostgreSQL 17   |
                         |  + pgvector      |
                         |  + pg_tde        |
                         |                  |
                         +------------------+
```

The high-level flow is:

1. **Ingest** -- Parse exports from various platforms into normalized entities.
2. **Process** -- Run ML pipelines (hashing, OCR, face detection, captioning, embeddings).
3. **Link** -- Discover relationships between entities (temporal, spatial, semantic).
4. **Search** -- Hybrid full-text + vector search with RRF fusion.
5. **Expose** -- Serve data to LLMs via MCP and to humans via a web UI.

---

## Technology Stack

| Layer             | Technology                                                   |
|-------------------|--------------------------------------------------------------|
| Language          | Python 3.12+                                                 |
| Web Framework     | FastAPI + Uvicorn                                            |
| Frontend          | HTMX + Jinja2 templates (server-rendered, no SPA)            |
| ORM               | SQLModel (SQLAlchemy + Pydantic)                             |
| Database          | Percona PostgreSQL 17 with pgvector and pg_tde               |
| Task Queue        | Celery + Redis                                               |
| ML - Text Embed   | e5-small-v2 (384 dimensions)                                 |
| ML - Multimodal   | SigLIP (768 dimensions, shared text-image space)             |
| ML - Faces        | MTCNN (detection) + ArcFace/iResNet50 (512d embeddings)      |
| ML - Captioning   | BLIP-2 (opt-2.7b)                                            |
| ML - OCR          | EasyOCR                                                      |
| CLI               | Typer + Rich                                                 |
| Package Manager   | uv                                                           |
| Linting           | Ruff (format + lint) + mypy (strict)                         |
| Migrations        | Alembic (handwritten initial migration for explicit control)  |
| CI/CD             | GitHub Actions, GHCR container registry                      |

---

## Project Structure

```
src/potluck/
|-- core/                   # Infrastructure and configuration
|   |-- config.py           #   Pydantic Settings (env vars, .env file)
|   |-- celery.py           #   Celery app factory, retry/fatal error helpers
|   |-- cli.py              #   Typer CLI (ingest, web, mcp, download-models)
|   |-- constants.py         #   Embedding dimensions, model identifiers
|   |-- exceptions.py       #   Exception hierarchy (PotluckError base)
|   +-- logging.py          #   Structured logging setup
|
|-- models/                 # SQLModel entity definitions (30+ classes)
|   |-- base.py             #   Entity hierarchy, EnumStr, SourceType, EntityType
|   |-- utils.py            #   UTCDatetime, IANATimezone validators
|   |-- media.py            #   Media, MediaEmbedding, MediaType
|   |-- messages.py         #   ChatThread, ChatMessage, ChatThreadParticipant
|   |-- email.py            #   Email, EmailThread, EmailAttachment, EmailFolder
|   |-- people.py           #   Person, PersonAlias, FaceEncoding
|   |-- faces.py            #   FaceCluster, MediaPersonLink
|   |-- social.py           #   SocialPost, SocialComment, SocialFollow
|   |-- notes.py            #   KnowledgeNote
|   |-- documents.py        #   Document
|   |-- calendar.py         #   CalendarEvent, EventParticipant
|   |-- locations.py        #   Location, LocationVisit, LocationHistory
|   |-- browsing.py         #   BrowsingHistory, Bookmark, BookmarkFolder
|   |-- financial.py        #   Transaction, Budget, Account
|   |-- tags.py             #   Tag, TagAssignment
|   |-- links.py            #   EntityLink, LinkType
|   +-- sources.py          #   ImportSource, ImportRun, ImportStatus
|
|-- pipeline/               # Ingestion + processing pipeline
|   |-- base.py             #   Abstract Stage[InputT, OutputT]
|   |-- dtos.py             #   StageResult, PipelineFilter, DetectionResult, etc.
|   |-- orchestrator.py     #   PipelineOrchestrator (discovery -> ingest -> queue)
|   |-- ingestion/          #   Source-specific importers
|   |   |-- base.py         #     BaseIngestionStage
|   |   |-- registry.py     #     Auto-detection registry
|   |   |-- google_takeout/ #     Photos, chat, mail, calendar, chrome, location, keep
|   |   |-- whatsapp/       #     Messages, media
|   |   |-- reddit/         #     Posts, comments, subscriptions
|   |   |-- ynab/           #     Transactions, budgets
|   |   |-- mbox/           #     MBOX email files
|   |   |-- image_folder/   #     Generic image directories
|   |   |-- text_files/     #     Plain text / markdown documents
|   |   +-- android_timeline/ #   Android Timeline location data
|   |-- processing/         #   ML processing stages
|   |   |-- core/           #     BaseProcessor, ProcessorRegistry, MLModels
|   |   |-- processors/     #     hashing, metadata, ocr, faces, captioning, embeddings, clustering
|   |   |-- linkers/        #     temporal, spatial, semantic linkers
|   |   +-- _arcface/       #     Vendored ArcFace iResNet50 implementation
|   |-- tasks/              #   Celery task definitions
|   |   |-- ingestion.py    #     run_ingestion, cancel_ingestion
|   |   +-- processing.py   #     run_*_processor tasks, pipeline functions
|   +-- utils/              #   Archive extraction, parsers, hashing, media helpers
|
|-- search/                 # Hybrid search system
|   |-- dtos.py             #   SearchQuery, SearchResults, RankingConfig
|   |-- cache.py            #   In-memory result cache with TTL
|   |-- retrieval/          #   FTSRetriever, VectorRetriever
|   |-- ranking/            #   RRFRanker (Reciprocal Rank Fusion)
|   +-- utils.py            #   Searchable model discovery, field introspection
|
|-- web/                    # FastAPI web application
|   |-- app.py              #   FastAPI app factory, auth middleware
|   |-- dependencies.py     #   DB session, auth dependencies
|   |-- utils.py            #   Shared router utilities
|   |-- routers/            #   13 routers (see Web Interface section)
|   |-- templates/          #   Jinja2 HTML templates
|   +-- static/             #   CSS, JS, images
|
|-- mcp/                    # MCP server (not yet implemented)
|   +-- server.py           #   Placeholder
|
|-- db/                     # Database session management
|   |-- session.py          #   Dual sync/async engines, session factories
|   +-- migration.py        #   Alembic config helpers, migration checks
|
+-- linkers/                # Top-level linkers package (placeholder, empty)
```

---

## Entity Model Hierarchy

All database entities inherit from a five-level class hierarchy. Each level adds
capabilities so subclasses only carry the weight they need.

```
                     SQLModel
                        |
                 IngestableEntity      (marker mixin -- no fields, type safety only)
                        |
                  SimpleEntity         (id, created_at, updated_at, search config)
                        |
                   BaseEntity          (+ source_type, source_id, content_hash)
                        |
                TimestampedEntity      (+ occurred_at, occurred_at_precision, source_timezone)
                        |
                GeolocatedEntity       (+ latitude, longitude, altitude, location_name)
```

**Where each entity sits:**

| Level              | Entities                                                        |
|--------------------|-----------------------------------------------------------------|
| SimpleEntity       | Tag, TagAssignment, MediaEmbedding, EventParticipant, etc.      |
| BaseEntity         | Person, BookmarkFolder, EmailFolder, Budget, SocialFollow       |
| TimestampedEntity  | ChatMessage, Email, SocialPost, SocialComment, KnowledgeNote,   |
|                    | Document, CalendarEvent, Transaction, BrowsingHistory, Bookmark |
| GeolocatedEntity   | Media, Location, LocationVisit                                  |

### Search Configuration

Each entity class declares its searchability through class variables:

```python
class Media(GeolocatedEntity, table=True):
    __searchable__ = True
    __search_priority_fields__ = {"caption"}       # Weight 'A' in FTS
    __search_date_fields__ = {"occurred_at"}        # For date-range filtering
    __search_exclude_fields__ = set()               # Fields to skip
```

The search system auto-discovers all models with `__searchable__ = True` and
introspects their string fields to build FTS queries at runtime.

### EnumStr Type Decorator

SQLAlchemy returns plain strings from VARCHAR columns, which breaks enum
comparisons. The custom `EnumStr` type decorator transparently converts between
Python enums and database strings in both directions:

```python
# Write: SourceType.GOOGLE_TAKEOUT -> "google_takeout" (VARCHAR)
# Read:  "google_takeout" (VARCHAR) -> SourceType.GOOGLE_TAKEOUT
source_type: SourceType = enum_field(SourceType)
```

All enum fields use this pattern via the `enum_field()` helper rather than
PostgreSQL native enums. This avoids migration pain when adding enum values.

---

## Data Flow

The complete lifecycle from raw export to searchable, LLM-accessible data:

```
                                CLI / Web Upload
                                       |
                                       v
                        +-----------------------------+
                        |   1. Archive Extraction     |
                        |   (ZIP/TAR -> temp dir)     |
                        +-----------------------------+
                                       |
                                       v
                        +-----------------------------+
                        |   2. Source Detection        |
                        |   (filename pattern match)  |
                        +-----------------------------+
                                       |
                                       v
                        +-----------------------------+
                        |   3. Entity Detection       |
                        |   (scan for available types)|
                        +-----------------------------+
                                       |
                                       v
                        +-----------------------------+
                        |   4. Ingestion              |
                        |   (parse -> yield entities  |
                        |    -> deduplicate -> batch   |
                        |    -> flush to DB)           |
                        +-----------------------------+
                                       |
                                       v
                        +-----------------------------+
                        |   5. Processing (Celery)    |
                        |   hashing -> metadata ->    |
                        |   OCR -> faces -> captions  |
                        |   -> embeddings             |
                        +-----------------------------+
                                       |
                                       v
                        +-----------------------------+
                        |   6. Linking (Celery)       |
                        |   temporal -> spatial ->    |
                        |   semantic                  |
                        +-----------------------------+
                                       |
                                       v
                        +-----------------------------+
                        |   7. Search / MCP / Web     |
                        |   FTS + vector -> RRF ->    |
                        |   enriched results          |
                        +-----------------------------+
```

### Step-by-step:

1. **Archive Extraction** -- The `extracted()` context manager detects archive
   format (ZIP, TAR, GZIP) and extracts to a temporary directory. Non-archives
   pass through unchanged.

2. **Source Detection** -- The ingestion registry matches the filename against
   registered `FILENAME_PATTERNS` using regex. Example: `takeout-.*\.zip`
   matches Google Takeout exports.

3. **Entity Detection** -- The matched stage's `detect()` method scans the
   extracted directory and returns entity counts (e.g., 5,000 photos, 12,000
   chat messages, 500 emails).

4. **Ingestion** -- The `PipelineOrchestrator` drives the process:
   - Creates `ImportSource` and `ImportRun` tracking records
   - Calls `stage.execute()` which yields entities one at a time
   - Deduplicates by `content_hash` (in-memory set + DB query)
   - Skips orphaned children when parents are deduplicated
   - Batches entities (default 100) for efficient DB writes
   - Sorts batches by FK dependency order before flushing
   - Reports progress via callback (drives SSE in web UI)

5. **Processing** -- Each entity is queued to Celery for ML processing. The
   processing pipeline runs stages in priority order (see next section).

6. **Linking** -- After all entities in an import are created, batch linkers
   analyze relationships and create `EntityLink` records.

7. **Serving** -- Processed data is available through the web UI, search API,
   and MCP server.

---

## Pipeline Architecture

### The Stage Abstraction

All pipeline operations share a common base:

```python
class Stage[InputT, OutputT](ABC):
    NAME: ClassVar[str]

    @abstractmethod
    def execute(self, input_data: InputT) -> OutputT: ...

    def should_execute(self, input_data: InputT) -> bool:
        return True
```

This generic type is specialized twice:

| Specialization      | InputT          | OutputT                     | Purpose              |
|---------------------|-----------------|-----------------------------|----------------------|
| BaseIngestionStage  | Path            | Iterator[IngestableEntity]  | Parse data exports   |
| BaseProcessor       | SQLModel        | StageResult                 | ML processing        |

### Ingestion Stages

Each data source is a package under `pipeline/ingestion/` with:

- An `__init__.py` that registers the stage via `@register`
- One or more parser modules
- An `instructions.md` with user-facing export instructions

```python
@register
class GoogleTakeoutStage(BaseIngestionStage):
    SOURCE_TYPE = SourceType.GOOGLE_TAKEOUT
    FILENAME_PATTERNS = [r"takeout-.*\.zip"]
    SUPPORTED_ENTITY_TYPES = {
        EntityType.MEDIA, EntityType.CHAT_MESSAGE, EntityType.EMAIL,
        EntityType.CALENDAR_EVENT, EntityType.BROWSING_HISTORY,
        EntityType.LOCATION, EntityType.KNOWLEDGE_NOTE,
    }

    def detect(self, path: Path) -> DetectionResult: ...
    def execute(self, path, entity_types, filters) -> Iterator[IngestableEntity]: ...
```

**Auto-discovery**: At import time, `ingestion/__init__.py` uses
`pkgutil.iter_modules()` to find all sub-packages and imports them. The
`@register` decorator in each package fires on import, populating the global
registry. Adding a new ingester requires zero configuration changes -- just
create a new package.

**Current ingesters**:

| Package            | Source Type       | Entity Types                                    |
|--------------------|-------------------|-------------------------------------------------|
| google_takeout     | GOOGLE_TAKEOUT    | Media, ChatMessage, Email, Calendar, Browsing,  |
|                    |                   | Location, KnowledgeNote                         |
| whatsapp           | WHATSAPP          | ChatMessage, Media                              |
| reddit             | REDDIT            | SocialPost, SocialComment, SocialFollow         |
| ynab               | YNAB              | Transaction, Budget                             |
| mbox               | GENERIC           | Email                                           |
| image_folder       | GENERIC           | Media                                           |
| text_files         | GENERIC           | Document                                        |
| android_timeline   | ANDROID_TIMELINE  | Location, LocationVisit                         |

### Deduplication

Two levels prevent duplicate data:

1. **File-level** (`ImportRun.file_hash`): SHA256 of the source archive. If an
   identical file was already imported successfully, the entire pipeline is
   skipped.

2. **Entity-level** (`BaseEntity.content_hash`): SHA256 of the entity's
   significant content fields. Checked in-memory first (O(1) set lookup), then
   against the database. Prevents duplicates even across different imports.

---

## Processing Pipeline

After ingestion, each entity is queued for ML processing via Celery. Processors
run in priority order:

```
hashing (10) -> metadata (20) -> OCR (30) -> faces (40) -> captioning (50) -> embeddings (60)
```

### Processor Architecture

Each processor is a self-contained file in `processing/processors/` with both
the business logic class and its Celery task:

```python
@ProcessorRegistry.register(priority=10)
class HashingProcessor(BaseProcessor):
    NAME = "hashing"
    SUPPORTED_ENTITY_TYPES = {EntityType.MEDIA}
    PERSIST_FIELDS = ["file_hash", "perceptual_hash"]

    def execute(self, media: Media) -> StageResult: ...
    def should_execute(self, media: Media) -> bool: ...

# Co-located Celery task
@celery_app.task(bind=True, queue="process")
def run_hashing_processor(self, entity_type: str, entity_id: str) -> dict:
    return run_processor_task(self, EntityType(entity_type), entity_id, HashingProcessor)
```

**Auto-discovery** works the same way as ingestion: `processing/__init__.py`
uses `pkgutil` to import all modules in `processors/`, which triggers both
class registration and Celery task creation.

### Two-Phase Registration

Processors face a chicken-and-egg problem: the registry decorator runs when the
class is defined, but the Celery task is defined below the class. The solution
is two-phase registration:

1. **Phase 1**: `@ProcessorRegistry.register()` stores the processor class with
   a placeholder task function.
2. **Phase 2**: `ProcessorRegistry.set_task()` links the real Celery task after
   it is defined.

This decouples processor logic from the Celery framework.

### Persistence Patterns

Processors declare how their results are saved:

- **Simple fields**: Set `PERSIST_FIELDS = ["file_hash", "perceptual_hash"]`.
  The base class automatically maps `result.data["file_hash"]` to the entity's
  `file_hash` column.

- **Complex persistence**: Override `persist_result()` for processors that
  create related records (e.g., `FaceProcessor` creates `MediaPersonLink`
  records, `EmbeddingProcessor` creates `MediaEmbedding` records).

### Processor Summary

| Processor   | Priority | What It Does                                                   |
|-------------|----------|----------------------------------------------------------------|
| Hashing     | 10       | SHA256 file hash + perceptual hash (pHash) for images          |
| Metadata    | 20       | EXIF extraction, dimensions, file size, GPS coordinates        |
| OCR         | 30       | Text extraction from images via EasyOCR                        |
| Faces       | 40       | Face detection (MTCNN) + recognition (ArcFace, 512d)           |
| Captioning  | 50       | Image captioning via BLIP-2                                    |
| Embeddings  | 60       | Text (e5, 384d) + multimodal (SigLIP, 768d) embeddings        |
| Clustering  | --       | DBSCAN face clustering (batch task, runs after faces)          |

### Celery Queue Configuration

Tasks are routed to separate queues so ingestion and processing can scale
independently:

```python
task_routes = {
    "potluck.pipeline.tasks.ingestion.*": {"queue": "ingest"},
    "potluck.pipeline.tasks.processing.*": {"queue": "process"},
    "potluck.embeddings.*":               {"queue": "embed"},
}
```

Error handling classifies failures as transient (DB connection errors, disk I/O
-- retried with exponential backoff) or fatal (file not found, permission denied
-- rejected permanently).

---

## Entity Linking

After an import completes, the orchestrator queues batch linkers to discover
relationships between entities. Linkers create `EntityLink` records with typed
relationships and confidence scores.

### Linker Types

| Linker     | Link Types Created        | How It Works                                    |
|------------|---------------------------|-------------------------------------------------|
| Temporal   | SAME_TIME                 | Entities within 60s window. Confidence = 1 - (dt/window). |
| Spatial    | SAME_LOCATION, NEAR       | Haversine distance. <50m = same location, <500m = near.   |
| Semantic   | SIMILAR                   | Cosine similarity of embedding vectors (threshold 0.8).   |

### EntityLink Model

```python
class EntityLink(SQLModel, table=True):
    source_type: EntityType     # e.g., MEDIA
    source_id: UUID
    target_type: EntityType     # e.g., CHAT_MESSAGE
    target_id: UUID
    link_type: LinkType         # e.g., SAME_TIME, NEAR, SIMILAR
    confidence: float           # 0.0 to 1.0
    is_automatic: bool          # True for linker-created
    linker_name: str            # "temporal", "spatial", "semantic"
    linker_version: str         # For tracking changes
```

Links can be cross-type (a photo linked to a chat message that happened at the
same time) or within-type (two photos taken at the same location).

---

## Search System

Potluck uses hybrid search combining PostgreSQL full-text search with pgvector
semantic similarity, fused via Reciprocal Rank Fusion (RRF).

### Search Modes

| Mode              | Retriever(s)         | Best For                                   |
|-------------------|----------------------|--------------------------------------------|
| FTS               | FTSRetriever         | Exact keyword matching, Google-like syntax  |
| VECTOR_TEXT       | VectorRetriever      | Semantic similarity, synonyms              |
| VECTOR_MULTIMODAL | VectorRetriever      | Cross-modal (text query -> find images)    |
| HYBRID (default)  | FTS + Vector         | Best of both worlds                        |

### Architecture

```
                   SearchQuery
                       |
          +------------+------------+
          |                         |
          v                         v
   +-------------+          +---------------+
   | FTSRetriever|          |VectorRetriever|
   | (tsvector + |          | (pgvector +   |
   |  GIN index) |          |  HNSW index)  |
   +------+------+          +-------+-------+
          |                         |
          v                         v
   [ranked results]          [ranked results]
          |                         |
          +------------+------------+
                       |
                       v
              +-----------------+
              |   RRF Ranker    |
              | score = SUM of  |
              | w/(k + rank)    |
              +-----------------+
                       |
                       v
              +-----------------+
              |   Enrichment    |
              | (title, date,   |
              |  source_type)   |
              +-----------------+
                       |
                       v
                 SearchResults
```

### FTS Retriever

- Uses PostgreSQL `tsvector` columns (auto-populated by database triggers)
- GIN indexes for fast lookup
- `websearch_to_tsquery` for Google-like syntax: `"exact phrase" -excluded OR alternative`
- `ts_rank_cd` (cover density) for scoring
- Builds UNION ALL queries across all searchable entity tables
- Priority fields get weight 'A' in the tsvector for boosting

### Vector Retriever

- Two embedding spaces:
  - **Text** (384d, e5-small-v2): text-to-text semantic search
  - **Multimodal** (768d, SigLIP): cross-modal search (text queries find images)
- Uses pgvector's `<=>` cosine distance operator
- HNSW indexes for approximate nearest neighbor search
- Query text is embedded at search time using the same models

### RRF Fusion

Reciprocal Rank Fusion combines ranked lists without score normalization:

```
score(entity) = SUM over retrievers of: weight / (k + rank)
```

Default configuration: `fts_weight=0.3`, `vector_weight=0.7`, `k=60`.

The k constant dampens the impact of top rankings, preventing any single
retriever from dominating. Results that appear in both retrievers get a
significant boost.

### Caching

Search results are cached in-memory with TTL-based expiration (default 5
minutes, max 1000 entries). Invalidation is all-or-nothing: any write to a
searchable entity clears the entire cache.

### Parallel Execution

FTS and vector retrievers run in parallel via `asyncio.gather()` with thread
pool executors (retrievers use sync database sessions). In hybrid mode, if one
retriever fails, the other's results are still returned.

---

## Web Interface

The web UI is built with FastAPI + HTMX + Jinja2, providing a server-rendered
experience with partial page updates for interactivity.

### Routers

| Router     | Path Prefix  | Features                                          |
|------------|-------------|---------------------------------------------------|
| auth       | /login      | Password-based login, signed cookie sessions       |
| dashboard  | /           | Overview statistics and recent activity            |
| search     | /search     | Hybrid search with filters and mode selection      |
| media      | /media      | Photo/video gallery, detail views                  |
| notes      | /notes      | Knowledge notes (create, edit, view)               |
| people     | /people     | People management, face clusters                   |
| timeline   | /timeline   | Chronological entity view                          |
| imports    | /imports    | Upload interface, SSE progress tracking            |
| events     | /events     | Calendar events view                               |
| map        | /map        | Geolocated entity map view                         |
| tags       | /tags       | Tag management and browsing                        |
| settings   | /settings   | Application configuration                          |
| entity     | /entity     | Generic entity detail view (any type)              |

### Authentication

- Optional password protection via `WEB_PASSWORD` environment variable
- Signed cookies using `itsdangerous.URLSafeTimedSerializer`
- `AuthMiddleware` redirects unauthenticated requests to `/login`
- When `WEB_PASSWORD` is unset, all requests pass through (no auth required)

### Import Progress

File uploads trigger ingestion jobs. Progress is streamed to the browser via
Server-Sent Events (SSE). The `PipelineOrchestrator` accepts a progress
callback that the web layer connects to SSE.

### Media Serving

Media files are served through database-ID-based routes (`/media/file/{id}`),
never exposing filesystem paths to the client. The handler resolves the database
record, verifies the file exists on disk, and returns it with the correct
content type.

---

## MCP Server

The MCP (Model Context Protocol) server is the primary interface for LLMs to
access Potluck data. It is not yet implemented -- `mcp/server.py` contains a
placeholder that raises `NotImplementedError`.

The planned implementation will use stdio transport for Claude Desktop
integration, exposing tools for search, entity retrieval, and relationship
navigation.

---

## Database Design

### PostgreSQL with Extensions

Potluck uses Percona PostgreSQL 17 with two key extensions:

- **pgvector**: Vector similarity search with HNSW indexes for approximate
  nearest neighbors. Supports cosine distance (`<=>` operator).

- **pg_tde** (Transparent Data Encryption): Encrypts all tables at rest. The
  database image sets `default_table_access_method=tde_heap` so all tables are
  encrypted by default. File-based keys for development; HashiCorp Vault for
  production.

### Schema Overview

The database has 41 tables across these domains:

```
People:     people, person_aliases, face_encodings, face_clusters, media_person_links
Media:      media, media_embeddings
Chat:       chat_threads, chat_messages, chat_thread_participants
Email:      emails, email_threads, email_attachments, email_folders
Social:     social_posts, social_comments, social_follows
Browsing:   browsing_history, bookmarks, bookmark_folders
Notes:      knowledge_notes
Documents:  documents
Locations:  locations, location_visits, location_history
Calendar:   calendar_events, event_participants
Financial:  transactions, budgets, accounts
Tags:       tags, tag_assignments
Links:      entity_links
Imports:    import_sources, import_runs
```

### Index Strategy

- **17 HNSW indexes** for vector similarity search (one per embedding column
  across entity tables, using cosine distance)
- **11 GIN indexes** for full-text search (one per tsvector column)
- **22 TSVECTOR triggers** that automatically populate search vectors when
  entity text fields change
- Standard B-tree indexes on foreign keys, content hashes, and date fields

### Embedding Storage

Most entities store embeddings inline:
- `embedding` column (384d): text-to-text semantic search
- `multimodal_embedding` column (768d): cross-modal search

Media entities additionally use the `media_embeddings` table for multiple
embedding types per item (CLIP visual, OCR text, caption text, audio
transcript), each stored as a 768d vector.

### Migrations

The initial migration (`001_initial_schema.py`, ~1,800 lines) is handwritten
rather than auto-generated. This provides explicit control over:
- pgvector HNSW index parameters (lists, ef_construction, m)
- tsvector trigger functions and their field configurations
- Composite indexes and partial indexes
- Extension setup (pgvector, pg_tde)

### Dual Sync/Async Engines

The application needs both synchronous and asynchronous database access:

| Context         | Engine    | Driver  | Use Case                            |
|-----------------|-----------|---------|-------------------------------------|
| Web server      | Async     | asyncpg | Non-blocking request handling        |
| Search          | Async     | asyncpg | Parallel retriever execution         |
| Celery workers  | Sync      | psycopg2| Task processing                     |
| Alembic         | Sync      | psycopg2| Schema migrations                   |
| CLI             | Sync      | psycopg2| Direct database operations           |

Both engines are `@lru_cache` singletons, created lazily on first use. The
async URL uses `postgresql+asyncpg://`, and a `sync_db_url` property
auto-converts it to `postgresql://` for the sync engine.

### Timezone Handling

All timestamps are stored as `TIMESTAMP WITHOUT TIME ZONE` in UTC. The
`ensure_utc()` validator converts any timezone-aware datetime to naive UTC
before storage. The original source timezone is preserved in a separate
`source_timezone` field (validated as an IANA timezone string) for display
purposes.

---

## ML Model Management

All ML models are managed through the centralized `MLModels` class in
`pipeline/processing/core/ml.py`.

### Design Principles

- **Lazy loading**: Models are loaded on first use, not at import time
- **Class-level cache**: A `ClassVar[dict]` shared across all instances avoids
  redundant model loading in Celery workers
- **GPU auto-detection**: Checks the `GPU` environment variable (set via Docker)
  and `torch.cuda.is_available()` to select device
- **Pre-download support**: `potluck download-models` eagerly loads all models
  for offline use (important for container startup)

### Model Inventory

| Model                    | Dimensions | Size    | Purpose                        |
|--------------------------|-----------|---------|--------------------------------|
| intfloat/e5-small-v2     | 384       | ~90MB   | Text embeddings                |
| google/siglip-base-patch16-224 | 768 | ~380MB  | Cross-modal (text+image)       |
| ArcFace iResNet50        | 512       | ~250MB  | Face recognition embeddings    |
| MTCNN                    | --        | small   | Face detection                 |
| EasyOCR                  | --        | ~100MB  | Optical character recognition  |
| Salesforce/blip2-opt-2.7b| --        | ~2.7GB  | Image captioning               |

Note: e5 models require input text to be prefixed with `"query: "` or
`"passage: "` depending on use case. The caller (embedding processor / search
retriever) is responsible for adding this prefix.

---

## Deployment Architecture

### Docker Compose

The system runs as three containers:

```
+------------------+     +------------------+     +------------------+
|   potluck-app    |     |   potluck-db     |     |  potluck-redis   |
|                  |     |                  |     |                  |
|  FastAPI server  +---->+  Percona PG 17   |     |  Redis 7         |
|  Celery worker   |     |  pgvector        |<----+  (broker +       |
|  (same container)|     |  pg_tde          |     |   result backend)|
|                  +---->+                  |     |                  |
+------------------+     +------------------+     +------------------+
```

The app container runs both the web server and a Celery worker in a single
process. The CMD starts migrations, downloads models, launches Celery in the
background, and then starts the web server.

### Build Strategy

The Dockerfile uses multi-stage builds with GPU selection:

```
base (python:3.12-slim + system deps)
  |
  +-- cpu-deps (~1.5GB, CPU-only PyTorch from special index)
  |
  +-- gpu-deps (~4.5GB, CUDA 12.4 PyTorch)
  |
  +-- deps (selected via GPU build arg)
       |
       +-- app (add application code)
```

CPU builds exclude all NVIDIA CUDA packages and install PyTorch from the
CPU-only wheel index, saving ~3GB of image size.

### CI Base Image Caching

CI performance is optimized with dependency caching:

1. Hash `pyproject.toml` + `uv.lock` to create a base image tag
2. Check GHCR for existing base image via manifest inspection (no download)
3. Cache hit (~2-3 min): Build only the `app` stage on top of cached deps
4. Cache miss (~8-10 min): Build `deps` target, push to GHCR, then build `app`

### Profiles

Docker Compose supports two profiles:

- **dev**: Builds images locally (`docker compose --profile dev up`)
- **prod**: Uses pre-built GHCR images (`docker compose --profile prod up`)

---

## Key Design Decisions

### 1. Source-Agnostic Entities

Every content entity has `source_type` (enum) + `source_id` (original
platform identifier). A `ChatMessage` works identically for WhatsApp,
Telegram, and Google Chat. This enables multi-source aggregation -- you can
search across all your conversations regardless of platform.

### 2. Media Paths, Not Blobs

Media files are never stored in the database. The `Media` table stores
`file_path` (absolute path on disk) and metadata. This keeps the database
lean, avoids blob storage complexity, and allows direct filesystem access
for serving.

### 3. Multiple Embeddings Per Entity

Most entities have two inline embedding columns:
- `embedding` (384d): optimized for text-to-text search
- `multimodal_embedding` (768d): shared text-image space for cross-modal search

Media additionally uses the `MediaEmbedding` table for specialized embeddings
(CLIP visual, OCR text, caption text). This allows searching images by their
text content, visual content, or generated descriptions.

### 4. Auto-Discovery via pkgutil

Both ingestion stages and processors use `pkgutil.iter_modules()` +
`importlib.import_module()` at import time to automatically discover and
register components. This means adding a new ingester or processor requires
zero configuration changes -- just create the file and it is discovered.

### 5. Handwritten Initial Migration

The initial Alembic migration is handwritten (not auto-generated) to maintain
explicit control over pgvector index parameters, tsvector trigger definitions,
and extension setup. Auto-generation would miss these PostgreSQL-specific
features.

### 6. VARCHAR Enums Over PostgreSQL Enums

All enum fields use `VARCHAR` storage (via `EnumStr` type decorator) rather
than PostgreSQL's native `ENUM` type. Adding a new value to a PostgreSQL enum
requires an `ALTER TYPE ... ADD VALUE` migration, which cannot run inside a
transaction. VARCHAR enums are simpler to evolve.

### 7. Naive UTC Timestamps

All timestamps are stored as `TIMESTAMP WITHOUT TIME ZONE` with values in
UTC. Source timezones are preserved separately. This avoids:
- asyncpg `DataError` when inserting timezone-aware datetimes
- Confusion about what timezone a stored timestamp is in
- Complexity in timezone-aware comparisons

### 8. Pydantic for DTOs, SQLModel for Entities

Configuration uses `pydantic-settings` (environment variables and `.env`).
Pipeline DTOs (`StageResult`, `SearchQuery`, etc.) use pure Pydantic models.
Database entities use SQLModel (which extends both SQLAlchemy and Pydantic).
This separation keeps the pipeline layer decoupled from the database layer.

### 9. Single Container for App + Worker

In development and simple deployments, the web server and Celery worker run
in the same container. This simplifies the deployment model while still
allowing separate scaling in production by splitting into multiple containers.

### 10. Domain Exceptions

All exceptions inherit from `PotluckError` with a clear hierarchy:

```
PotluckError
|-- ConfigurationError
|-- DatabaseError
|-- PipelineError
|   |-- IngestionError
|   +-- ProcessingError
+-- SearchError
    |-- InvalidSearchQueryError
    +-- NoSearchableEntitiesError
```

This enables precise error handling at every layer while allowing broad
catches at the top level.
