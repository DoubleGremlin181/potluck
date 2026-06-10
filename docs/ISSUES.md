# Potluck Codebase Issues

Catalog of architectural issues, inconsistencies, code smells, and improvement opportunities
discovered during code review. Organized by severity, then by module.

Items that are part of the roadmap (tracked in GitHub Issues/Milestones) are not listed here.

**Last updated:** 2026-02-20
**Reviewed version:** v0.8.0 (branch `phase-9-dev`, commit `8a38f8a`)

---

## Table of Contents

- [Critical](#critical) (2 issues)
- [High](#high) (9 issues)
- [Medium](#medium) (18 issues)
- [Low](#low) (11 issues)
- [Summary Statistics](#summary-statistics)

---

## Critical

Issues that risk data corruption or represent security vulnerabilities in concurrent environments.

### CRIT-1: Cache Thread Safety

| Field | Value |
|-------|-------|
| **File** | `src/potluck/search/cache.py` |
| **Lines** | 23-31 (class docstring warning), 150-162 (`set`/`_evict_oldest`), 164-170 (`clear_all`), 185-198 (global singleton) |
| **Module** | search |

**Description:** The `SearchCache` class uses a plain `dict` (`self._cache`) without any locking
mechanism. The class docstring explicitly warns "This cache is not thread-safe" (line 29), yet
the global singleton `_search_cache` (line 186) is used in a multi-threaded context: FastAPI
runs retrievers in a `ThreadPoolExecutor` via `loop.run_in_executor()` (see
`src/potluck/search/__init__.py`, lines 220-255), and `invalidate_search_cache()` can be called
from any thread.

Specific race conditions:
- `_evict_oldest()` (line 156) iterates and deletes from `_cache` while another thread may be
  reading or writing
- `clear_all()` (line 164) calls `_cache.clear()` during concurrent `get()`/`set()` operations
- `get_search_cache()` (line 189) has a TOCTOU race on the `_search_cache is None` check

**Impact:** In production under concurrent search load, this can cause `RuntimeError: dictionary
changed size during iteration`, lost cache entries, or stale data served from partially cleared
caches.

**Suggested Fix:** Add a `threading.Lock` to `SearchCache` and wrap all `_cache` mutations in
`with self._lock:` blocks. Alternatively, use `get_search_cache()` as a proper singleton with
a module-level lock or initialize it eagerly at import time.

---

### CRIT-2: Vector Literal SQL Formatting

| Field | Value |
|-------|-------|
| **File** | `src/potluck/search/retrieval/vector.py` |
| **Lines** | 160-166 |
| **Module** | search |

**Description:** The vector query embedding is formatted directly into the SQL string using
f-string interpolation:

```python
vector_literal = f"[{','.join(str(x) for x in query_embedding)}]"
cosine_distance = emb_col.op("<=>")(text(f"'{vector_literal}'::vector"))
```

This bypasses SQLAlchemy's parameterized query mechanism. While not a traditional SQL injection
risk (the values come from ML model output, not user input), it is fragile: if any float value
is `NaN`, `inf`, or `-inf`, the resulting SQL will be malformed and the query will fail with a
database error. Additionally, float-to-string precision may vary across Python versions.

**Impact:** Production search queries could fail silently or with cryptic database errors when
embedding models produce edge-case values. The pattern also violates the principle of using
parameterized queries for all dynamic values.

**Suggested Fix:** Use pgvector's native SQLAlchemy integration for parameterized vector
comparison. If that is not feasible, at minimum validate that all embedding values are finite
floats before formatting, and use a consistent precision format (e.g., `f"{x:.8f}"`).

---

## High

Issues that cause incorrect behavior, significant performance problems, or security weaknesses.

### HIGH-1: Inconsistent IngestableEntity Application

| Field | Value |
|-------|-------|
| **File** | `src/potluck/models/messages.py` (line 43), `src/potluck/models/email.py` (line 34) |
| **Module** | models |

**Description:** The `IngestableEntity` marker class (defined in `src/potluck/models/base.py`,
line 69) is inconsistently applied across entity models that are yielded by ingesters:

| Model | Base Class(es) | Has `IngestableEntity`? | Yielded by Ingesters? |
|-------|---------------|-------------------------|----------------------|
| `Location` | `SQLModel, IngestableEntity` | Yes | Yes |
| `LocationVisit` | `SQLModel, IngestableEntity` | Yes | Yes |
| `LocationHistory` | `SQLModel, IngestableEntity` | Yes | Yes |
| `ChatThread` | `SimpleEntity` | Yes (via SimpleEntity) | Yes |
| `EmailThread` | `BaseEntity` | Yes (via BaseEntity -> SimpleEntity) | Yes |
| `ChatMessage` | `TimestampedEntity` | Yes (via inheritance chain) | Yes |

Upon closer inspection, `SimpleEntity` inherits from `IngestableEntity`, so `ChatThread` and
`EmailThread` do inherit it through the chain. However, the location models bypass `SimpleEntity`
entirely and inherit `IngestableEntity` directly alongside `SQLModel`, duplicating fields like
`id`, `created_at`, and `updated_at` manually (see `src/potluck/models/locations.py`, lines
62-75).

**Impact:** The location models duplicate fields from `SimpleEntity` instead of inheriting them,
creating maintenance burden and risk of field definitions diverging. If `SimpleEntity` gains new
fields or changes defaults, location models will not pick them up.

**Suggested Fix:** Refactor `Location`, `LocationVisit`, and `LocationHistory` to inherit from
`SimpleEntity` or `BaseEntity` (as appropriate) instead of duplicating their fields. Keep
`IngestableEntity` as a marker on classes that need it but do not inherit from `SimpleEntity`.

---

### HIGH-2: Offset/Limit Pagination Bug

| Field | Value |
|-------|-------|
| **File** | `src/potluck/search/__init__.py` |
| **Lines** | 152-166 |
| **Module** | search |

**Description:** Pagination is applied AFTER RRF fusion. The retriever limit is calculated as
`retriever_limit = query.limit * 3` (line 152), which does not account for `query.offset`. When
requesting deep pagination (e.g., `offset=100, limit=20`), each retriever still only fetches
`20 * 3 = 60` results, but after fusion the code tries to slice at `[100:120]` -- which returns
an empty list because there are only ~60 fused results.

**Impact:** Any paginated search request beyond the first page will return empty or incomplete
results.

**Suggested Fix:** Calculate the retriever limit as `(query.offset + query.limit) * 3` to ensure
enough results are fetched for the requested page. Alternatively, document that offset-based deep
pagination is not supported and suggest cursor-based pagination.

---

### HIGH-3: Hardcoded Field Exclusion List

| Field | Value |
|-------|-------|
| **File** | `src/potluck/search/utils.py` |
| **Lines** | 52-102 |
| **Module** | search |

**Description:** The `get_model_text_fields()` function contains a hardcoded set of ~50 field
names in `default_exclusions`. This list must be manually updated whenever a new model adds ID,
hash, URL, or embedding fields. There is no automated mechanism to keep it in sync with the
actual model definitions.

**Impact:** New models or fields may inadvertently be included in full-text search indexing
(e.g., a new `_url` field), degrading search quality. Conversely, removing a field from a model
without updating this list silently includes dead entries.

**Suggested Fix:** Generate exclusions from common patterns using naming conventions (e.g.,
fields ending in `_id`, `_hash`, `_url`, `_embedding`, `_vector`) or introspect field types
from base class annotations. Move model-specific exclusions to `__search_exclude_fields__` on
each model class.

---

### HIGH-4: install.sh Credential Security

| Field | Value |
|-------|-------|
| **File** | `scripts/install.sh` |
| **Lines** | 54-60 |
| **Module** | scripts |

**Description:** The install script generates credentials and writes them to `.env` without
setting restrictive file permissions. The default `umask` on most systems is `0022`, meaning
the `.env` file will be created with mode `0644` (world-readable). Additionally, the downloaded
`docker-compose.yml` and `init-db.sql` files are fetched without checksum verification, making
the install vulnerable to supply chain attacks if the download is intercepted.

**Impact:** Database passwords and session secrets are readable by any user on the system. On
shared servers, this is a direct credential exposure.

**Suggested Fix:** Add `chmod 600 .env` after generating credentials, or set `umask 077` before
the file creation block. Add SHA256 checksum verification for downloaded files.

---

### HIGH-5: No App Health Check in Docker

| Field | Value |
|-------|-------|
| **File** | `docker/Dockerfile` |
| **Lines** | 119 |
| **Module** | docker |

**Description:** The app container lacks a `HEALTHCHECK` directive. The `CMD` uses a
`{ ... & exec ... }` pattern that starts Celery in the background and the web server in the
foreground. If the Celery worker crashes or becomes unresponsive, the container will appear
healthy because only the foreground process (web server) is monitored by Docker.

**Impact:** Docker, Compose, and orchestrators (Kubernetes, Swarm) have no way to detect a
degraded container where the background Celery worker has died. Failed processing tasks will
silently accumulate.

**Suggested Fix:** Add a `HEALTHCHECK` directive that verifies both the web server
(`curl -f http://localhost:8000/`) and the Celery worker (e.g., `celery inspect ping`).
Consider running Celery as a separate container for proper process isolation.

---

### HIGH-6: Dedup Logic Duplication

| Field | Value |
|-------|-------|
| **File** | `src/potluck/pipeline/orchestrator.py` (lines 414-444) |
| **Module** | pipeline |

**Description:** Entity deduplication by `content_hash` is implemented in
`PipelineOrchestrator._is_duplicate()`. However, individual ingestion stages (particularly
Google Takeout stages) also perform their own deduplication logic. This creates two layers of
dedup with potentially different semantics.

**Impact:** Maintenance burden from having to update dedup logic in multiple places. Risk of
inconsistent dedup behavior between different code paths.

**Suggested Fix:** Consolidate all deduplication in the orchestrator layer. Ingestion stages
should yield all entities and let the orchestrator handle dedup uniformly.

---

### HIGH-7: No CSRF Protection

| Field | Value |
|-------|-------|
| **File** | `src/potluck/web/routers/auth.py`, `src/potluck/web/routers/imports.py` |
| **Module** | web |

**Description:** POST endpoints (login, file upload, import start, import cancel) accept form
submissions without CSRF token validation. The session cookie uses `samesite="lax"`, which
provides some protection but does not fully prevent CSRF on form submissions from cross-origin
pages.

**Impact:** Acceptable for a single-user local application, but becomes a vulnerability if the
app is exposed on a network or if multi-user support is added. A malicious page could trigger
imports or cancel running jobs.

**Suggested Fix:** If multi-user support is planned, add CSRF tokens to all forms using a
middleware like `starlette-csrf`. For single-user use, document this as an accepted risk.

---

### HIGH-8: No Rate Limiting

| Field | Value |
|-------|-------|
| **File** | `src/potluck/web/routers/auth.py` (line 31) |
| **Module** | web |

**Description:** The `POST /login` endpoint has no rate limiting. An attacker on the same
network can brute-force the password with unlimited attempts. No API endpoints have rate
limiting either.

**Impact:** If the web UI is exposed beyond localhost (which is the default -- `web_host`
defaults to `0.0.0.0` in `src/potluck/core/config.py`, line 47), the login endpoint is
vulnerable to brute-force attacks.

**Suggested Fix:** Add rate limiting middleware (e.g., `slowapi`) to the login endpoint with
progressive delays. Consider account lockout after N failed attempts.

---

### HIGH-9: Hardcoded errno Values

| Field | Value |
|-------|-------|
| **File** | `src/potluck/core/celery.py` |
| **Lines** | 30 |
| **Module** | core |

**Description:** The `is_transient_error()` function uses magic numbers for errno values:
`error.errno in (5, 28, 30)` instead of the named constants `errno.EIO`, `errno.ENOSPC`,
`errno.EROFS` from Python's `errno` module. The comment mentions the correct names but the code
uses raw integers.

**Impact:** Code is difficult to understand without looking up errno values. The values may also
differ across platforms (though Linux values are standard).

**Suggested Fix:** Replace with `import errno` and use `error.errno in (errno.EIO, errno.ENOSPC, errno.EROFS)`.

---

## Medium

Issues that affect maintainability, developer experience, or represent non-critical bugs.

### MED-1: stacklevel in Config Validator

| Field | Value |
|-------|-------|
| **File** | `src/potluck/core/config.py` |
| **Lines** | 75-80 |
| **Module** | core |

**Description:** The `_validate_auth_config` model validator uses `stacklevel=1` in its
`warnings.warn()` call (line 79). This causes the warning to point to the validator method
itself rather than the calling code that instantiated `Settings`.

**Impact:** When the warning fires, the traceback points to an unhelpful location inside
Pydantic validation internals, making it harder for users to identify where their configuration
is being loaded.

**Suggested Fix:** Change to `stacklevel=2` or higher to point the warning at the caller.
Note that the exact correct value depends on Pydantic's internal call depth, so test empirically.

---

### MED-2: SourceType.MANUAL Filtering Undocumented

| Field | Value |
|-------|-------|
| **File** | `src/potluck/core/cli.py` |
| **Lines** | 250 |
| **Module** | core |

**Description:** In `_validate_source_type()`, `SourceType.MANUAL` is explicitly excluded from
the list of valid source types with no comment explaining why:
```python
valid = [st.value for st in SourceType if st not in (SourceType.MANUAL,)]
```

**Impact:** Future developers may not understand the rationale and could accidentally remove or
modify this filter.

**Suggested Fix:** Add a comment explaining that `MANUAL` represents user-created content within
Potluck (notes, annotations) and is not a valid ingestion source type.

---

### MED-3: Unexported Exceptions

| Field | Value |
|-------|-------|
| **File** | `src/potluck/core/__init__.py` (lines 12-24), `src/potluck/core/exceptions.py` |
| **Module** | core |

**Description:** The following exceptions are defined in `core/exceptions.py` but not exported
via `core/__init__.py`:
- `ProcessingError` (line 39)
- `SearchError` (line 51)
- `InvalidSearchQueryError` (line 61)
- `NoSearchableEntitiesError` (line 71)
- `PipelineError` (line 20)

The `__all__` list in `core/__init__.py` only includes `PotluckError`, `ConfigurationError`,
`DatabaseError`, and `IngestionError`.

**Impact:** Users of the `core` package must import exceptions directly from
`potluck.core.exceptions` instead of the more convenient `potluck.core`. This inconsistency
means some exceptions have a shorter import path than others.

**Suggested Fix:** Add the missing exceptions to the imports and `__all__` list in
`src/potluck/core/__init__.py`.

---

### MED-4: JSON-Encoded Fields Without Type Safety

| Field | Value |
|-------|-------|
| **Files** | `src/potluck/models/email.py` (lines 52, 87, 155-166, 197), `src/potluck/models/social.py` (lines 165, 239), `src/potluck/models/calendar.py` |
| **Module** | models |

**Description:** Multiple entity models store structured data as JSON-encoded strings in
`VARCHAR` fields:

| Model | Field | Stored Content |
|-------|-------|---------------|
| `EmailThread` | `participant_emails` | List of email addresses |
| `EmailThread` | `labels` | List of labels/folders |
| `Email` | `to_addresses`, `cc_addresses`, `bcc_addresses` | List of addresses |
| `Email` | `labels` | List of labels |
| `SocialPost` | `media_urls` | List of media URLs |
| `SocialPost` | `tags` | List of tags |
| `CalendarEvent` | `reminder_minutes` | List of reminder times |

**Impact:** No JSON schema validation at the application level. IDE support and type checking are
lost. Queries filtering on these fields require JSON parsing. Deserialization errors at runtime
instead of at write time.

**Suggested Fix:** Use PostgreSQL JSONB columns with Pydantic validators for these fields, or
normalize into proper relational tables (e.g., `email_recipients`, `post_tags`).

---

### MED-5: Nullable Email.thread_id

| Field | Value |
|-------|-------|
| **File** | `src/potluck/models/email.py` |
| **Lines** | 117-122 |
| **Module** | models |

**Description:** `Email.thread_id` is `UUID | None` (nullable), while `ChatMessage.thread_id`
(in `src/potluck/models/messages.py`, line 140) is a required `UUID`. Semantically, every email
should belong to a thread (even if it is a single-message thread).

**Impact:** Inconsistent data model. Code that joins emails to threads must handle the nullable
case, and orphaned emails (without threads) may cause issues in thread-based views.

**Suggested Fix:** Make `Email.thread_id` required and ensure the mail ingester always creates
or resolves a thread before persisting emails.

---

### MED-6: LocationHistory.occurred_at Redundant

| Field | Value |
|-------|-------|
| **File** | `src/potluck/models/locations.py` |
| **Lines** | 355-364 (LocationHistory), 262-267 (LocationVisit) |
| **Module** | models |

**Description:** `LocationHistory` has both `timestamp` (line 355) and `occurred_at` (line 360)
fields that store the same value. The comment says "occurred_at aliases timestamp for consistent
filtering/search" (line 359). Similarly, `LocationVisit` has both `started_at` (line 250) and
`occurred_at` (line 263).

**Impact:** Doubled storage for the same data. Risk of the values diverging if one is updated
without the other. Extra maintenance burden for ingesters to populate both fields.

**Suggested Fix:** Use a `@property` or `@hybrid_property` for `occurred_at` that returns
`timestamp` or `started_at` respectively. This provides the search-compatible field name without
data duplication.

---

### MED-7: Bounding Box Validation Bypass

| Field | Value |
|-------|-------|
| **File** | `src/potluck/models/faces.py` |
| **Lines** | 163-174 |
| **Module** | models |

**Description:** `MediaPersonLink.validate_bbox_atomicity` is a `@model_validator` that ensures
all four bounding box fields are either all set or all `None`. However, SQLModel table classes
can bypass Pydantic validators when instantiated via `__init__` directly (without calling
`model_validate()`). Direct construction like `MediaPersonLink(bbox_x=10)` without the other
bbox fields would succeed at the Python level and only fail at database insert time (or not at
all, if constraints are absent).

**Impact:** Partial bounding box data could be persisted to the database, causing issues in face
display/cropping logic.

**Suggested Fix:** Add database-level CHECK constraints to enforce the all-or-nothing rule, or
use `model_validate()` consistently in all code paths that create `MediaPersonLink` instances.

---

### MED-8: N+1 Query Patterns

| Field | Value |
|-------|-------|
| **File** | `src/potluck/web/routers/dashboard.py` (lines 26-31), `src/potluck/web/routers/settings.py` (lines 30-40) |
| **Module** | web |

**Description:** The dashboard and settings pages issue separate `SELECT COUNT(*)` queries for
each entity type. With 17 entity types in `_ENTITY_TYPE_MODEL_MAP`, this means 17+ database
round-trips per page load. The dashboard additionally fetches 3 recent entities per type
(lines 35-47), adding up to 17 more queries.

**Impact:** Dashboard page load time scales linearly with the number of entity types. Currently
~34 queries per dashboard load.

**Suggested Fix:** Use a single query with `UNION ALL` to count all types in one round-trip, or
cache entity counts with a short TTL. For recent entities, consider a single query across all
types ordered by `created_at`.

---

### MED-9: Temp File Cleanup

| Field | Value |
|-------|-------|
| **File** | `src/potluck/web/routers/imports.py` |
| **Lines** | 147-173 |
| **Module** | web |

**Description:** The file upload handler creates a temp directory (line 147), cleans it on error
(line 172), but relies on the Celery worker for cleanup on success (comment on line 171). If the
Celery task is never picked up (e.g., worker is down, queue is full), the temp files will
accumulate indefinitely.

**Impact:** Disk space leak. Repeated uploads without a running Celery worker will eventually
fill the disk.

**Suggested Fix:** Implement a periodic cleanup job that removes temp directories older than a
configurable threshold (e.g., 24 hours). Or use a temp directory manager that tracks created
paths and cleans them on application shutdown.

---

### MED-10: Hardcoded Table Names in Sort

| Field | Value |
|-------|-------|
| **File** | `src/potluck/pipeline/orchestrator.py` |
| **Lines** | 484-508 |
| **Module** | pipeline |

**Description:** `_sort_by_dependencies()` uses a hardcoded dictionary of table names to
determine insertion priority. If a table is renamed or a new dependent table is added, this
mapping must be manually updated.

**Impact:** Schema changes that rename tables will silently break insertion ordering, potentially
causing foreign key constraint violations during batch persistence.

**Suggested Fix:** Derive dependency order from SQLAlchemy's `ForeignKeyConstraint` metadata
introspection, or use `__tablename__` constants from the model classes.

---

### MED-11: ArcFace Checkpoint Loading Fragile

| Field | Value |
|-------|-------|
| **File** | `src/potluck/pipeline/processing/core/ml.py` |
| **Lines** | 310-336 |
| **Module** | pipeline |

**Description:** The face encoder checkpoint loading performs multiple format conversions
(stripping `arcface.` prefix, `strict=False` loading) and only validates that fewer than 10 keys
are missing (line 332). There is no validation that the loaded embedding dimensions match the
expected 512 dimensions defined in `FACE_EMBEDDING_DIM`.

**Impact:** A corrupted or incompatible checkpoint could load without error but produce
embeddings of the wrong dimension, causing downstream failures in face clustering and similarity
search.

**Suggested Fix:** After loading weights, run a test inference with a dummy input and validate
that the output tensor has exactly `FACE_EMBEDDING_DIM` dimensions. Fail fast if the dimension
does not match.

---

### MED-12: RRF k Constant Possibly Suboptimal

| Field | Value |
|-------|-------|
| **File** | `src/potluck/search/ranking/rrf.py` (line 52), `src/potluck/search/dtos.py` (line 73) |
| **Module** | search |

**Description:** The default RRF k constant is 60 (`RankingConfig.rrf_k`, default in
`src/potluck/search/dtos.py`, line 73). Research literature on RRF typically uses values in the
range k=20-40 for better discrimination between top-ranked results. With k=60, the score
contribution from rank 1 is `weight/61` and from rank 10 is `weight/70` -- a ratio of only
~1.15x, meaning top results have very little advantage over results ranked 10th.

**Impact:** Search result quality may be suboptimal, with truly relevant top-ranked results not
sufficiently differentiated from lower-ranked results.

**Suggested Fix:** Benchmark with k values in the range 20-40 using representative queries and
a relevance evaluation set. Make the default configurable and document the tradeoffs.

---

### MED-13: Cache Expiration Lag

| Field | Value |
|-------|-------|
| **File** | `src/potluck/search/cache.py` |
| **Lines** | 117-121 |
| **Module** | search |

**Description:** Expired cache entries are only evicted on `get()` (lines 117-121). There is no
background expiration or eviction during `set()`. The `_evict_oldest()` method (line 156) is
only called when the cache reaches max capacity, and it evicts by age, not by TTL.

**Impact:** The cache can accumulate many expired entries that consume memory without providing
any benefit. In the worst case (many unique queries, few repeated), the cache fills with stale
entries before any are cleaned up.

**Suggested Fix:** Add TTL checking in `_evict_oldest()`, or add a periodic cleanup method that
removes all expired entries.

---

### MED-14: Source Type Filtering Incomplete

| Field | Value |
|-------|-------|
| **File** | `src/potluck/search/__init__.py`, `src/potluck/search/dtos.py` (line 52) |
| **Module** | search |

**Description:** `SearchQuery.source_types` is accepted and validated (line 52 of
`search/dtos.py`), and is included in the cache key (line 120 of `search/__init__.py`), but it
is never actually passed to or enforced by the FTS or Vector retrievers. The retrievers have no
`source_types` parameter.

**Impact:** Users who specify `source_types` in their search query will get unfiltered results
cached under a source-type-specific key, giving the false impression that filtering is applied.

**Suggested Fix:** Pass `source_types` to the retrievers and add a `WHERE source_type IN (...)`
clause to the retriever queries. Alternatively, remove the field from `SearchQuery` until
implemented.

---

### MED-15: to_text_repr() Truncation Inconsistent

| Field | Value |
|-------|-------|
| **Files** | `src/potluck/models/media.py` (line 56), `src/potluck/models/documents.py` (line 42), `src/potluck/models/messages.py` (line 133), `src/potluck/models/social.py` (line 286) |
| **Module** | models |

**Description:** Different models truncate content previews at different lengths in their
`to_text_repr()` implementations:

| Model | Truncation | Field |
|-------|-----------|-------|
| `Media` | 60 chars (57 + "...") | caption/ocr_text |
| `Document` | 100 chars | content |
| `ChatMessage` | 80 chars | content |
| `SocialComment` | 60 chars | body |
| `KnowledgeNote` | 100 chars | content |

**Impact:** Inconsistent behavior makes it harder to reason about MCP tool output formatting and
LLM context window usage.

**Suggested Fix:** Define a `PREVIEW_MAX_LEN` constant in `src/potluck/models/base.py` and use
it consistently, or add a `truncate_preview(text, max_len)` utility method to `SimpleEntity`.

---

### MED-16: Test DB Always Returns Empty

| Field | Value |
|-------|-------|
| **File** | `tests/unit/web/conftest.py` |
| **Lines** | 17-28 |
| **Module** | tests |

**Description:** The `mock_db` fixture returns empty results for all queries
(`result.scalar.return_value = 0`, `result.scalars.return_value.all.return_value = []`). Unit
tests for web routes can only verify that pages render without errors, not that they display
correct data.

**Impact:** Limited test coverage -- UI rendering with actual data is never tested at the unit
level. Bugs in template logic that depend on non-empty data will not be caught.

**Suggested Fix:** Create additional fixtures that return representative mock data for different
test scenarios (e.g., `mock_db_with_entities`, `mock_db_with_imports`).

---

### MED-17: Empty Database Browser Tests

| Field | Value |
|-------|-------|
| **File** | `tests/e2e/test_map.py` |
| **Lines** | 41, 66, 78 |
| **Module** | tests |

**Description:** Map E2E tests use conditional guards like `if markers.count() > 0:` (line 78)
and `if first_cb.count() > 0:` (line 41), meaning the test body is skipped when the database
is empty. Since the tests do not seed any data, they pass vacuously in a clean environment.

**Impact:** Tests provide false confidence -- they pass without actually exercising the
functionality under test.

**Suggested Fix:** Add test fixtures that seed the database with representative location/media
data before running map tests. Remove the conditional guards so tests actually assert on
expected content.

---

### MED-18: Hardcoded Migration Version

| Field | Value |
|-------|-------|
| **File** | `tests/integration/test_e2e_setup.py` |
| **Lines** | 83 |
| **Module** | tests |

**Description:** The test asserts an exact migration version string:
```python
assert result[0] == "001_initial_schema"
```

This will break as soon as a new migration is added.

**Impact:** Adding any new Alembic migration will cause this test to fail, requiring a manual
update to the expected version string.

**Suggested Fix:** Assert that a version exists (is not None/empty) rather than checking for a
specific version string. Or dynamically determine the expected head version from the Alembic
config.

---

## Low

Minor issues, style concerns, and improvements with minimal immediate impact.

### LOW-1: __version__ Mismatch

| Field | Value |
|-------|-------|
| **File** | `src/potluck/__init__.py` (line 3), `pyproject.toml` (line 3) |
| **Module** | root |

**Description:** `__version__` in `src/potluck/__init__.py` is `"0.1.0"` while `pyproject.toml`
declares `version = "0.8.0"`. These should be kept in sync.

**Impact:** Code that reads `potluck.__version__` (e.g., for logging, API headers, or user
display) will report the wrong version.

**Suggested Fix:** Use `importlib.metadata.version("potluck")` to dynamically read the version
from the installed package metadata, or add a build step that syncs the version from
`pyproject.toml` into `__init__.py`.

---

### LOW-2: No FK Cascade Behavior Specified

| Field | Value |
|-------|-------|
| **File** | Alembic migration (initial schema) |
| **Module** | db |

**Description:** Foreign keys are defined without explicit `ON DELETE` behavior. PostgreSQL
defaults to `RESTRICT`, which prevents deleting parent rows that have child references. This may
or may not be the intended behavior, but it is undocumented.

**Impact:** Deleting entities (e.g., a Person) will fail if any child records reference them.
This may cause confusing errors during data cleanup or entity merging.

**Suggested Fix:** Add explicit `ON DELETE CASCADE` or `ON DELETE SET NULL` to foreign keys based
on the desired behavior. Document the deletion strategy in the schema.

---

### LOW-3: No Validation on Numeric Ranges in Migration

| Field | Value |
|-------|-------|
| **File** | Alembic migration (initial schema) |
| **Module** | db |

**Description:** Latitude, longitude, width, and height columns do not have database-level
`CHECK` constraints. While the Pydantic models have `ge`/`le` validators (e.g.,
`latitude: float = Field(ge=-90, le=90)`), these are bypassed when writing directly via SQL
or when loading from the database.

**Impact:** Invalid data (e.g., latitude=999, width=-1) could be inserted via raw SQL or
migrations without triggering application-level validation.

**Suggested Fix:** Add `CHECK` constraints in the migration for critical numeric ranges.

---

### LOW-4: DiscoveryResult.stage Typed as Any

| Field | Value |
|-------|-------|
| **File** | `src/potluck/pipeline/dtos.py` |
| **Lines** | 137 |
| **Module** | pipeline |

**Description:** `DiscoveryResult.stage` is typed as `Any` with a comment explaining this is a
workaround for Pydantic forward reference issues. The intended type is
`type[BaseIngestionStage] | None`.

**Impact:** Loss of type safety and IDE autocompletion when accessing `stage` attributes.

**Suggested Fix:** Use `Annotated[type[BaseIngestionStage] | None, ...]` with Pydantic's
`ConfigDict(arbitrary_types_allowed=True)`, which is already set on the model.

---

### LOW-5: Order-Dependent Stage Detection

| Field | Value |
|-------|-------|
| **File** | `src/potluck/pipeline/ingestion/registry.py` |
| **Lines** | 38-57 |
| **Module** | pipeline |

**Description:** `detect_stage()` returns the first stage whose `FILENAME_PATTERNS` match the
input path name (line 53). The iteration order depends on the order stages were registered,
which depends on `pkgutil.iter_modules()` discovery order. There is no priority or scoring
system.

**Impact:** If two stages have overlapping filename patterns, the result is non-deterministic
depending on import order. Currently this is not an issue because patterns are distinct, but it
is fragile.

**Suggested Fix:** Add an optional `PRIORITY` class attribute to stages, or return all matching
stages and let the caller choose, or document that patterns must be non-overlapping.

---

### LOW-6: Process Cleanup in E2E Tests

| Field | Value |
|-------|-------|
| **File** | `tests/e2e/conftest.py` |
| **Lines** | 86-90 |
| **Module** | tests |

**Description:** The `live_server` fixture calls `proc.kill()` without `proc.join()` or
`proc.wait()` on both the timeout path (line 87) and the normal cleanup path (line 90). Killed
processes that are not waited on become zombie processes.

**Impact:** Running the E2E test suite repeatedly could accumulate zombie processes, eventually
exhausting the process table on resource-constrained CI environments.

**Suggested Fix:** Add `proc.join(timeout=5)` after `proc.kill()` to reap the child process.

---

### LOW-7: Network-Dependent Test Fixtures

| Field | Value |
|-------|-------|
| **File** | `tests/fixtures/generate_fixtures.py` |
| **Module** | tests |

**Description:** The face fixture generation script downloads test images from Wikipedia, which
requires an internet connection. This fails silently or with cryptic errors in offline
environments or CI environments without internet access.

**Impact:** Tests that depend on generated fixtures will fail in offline environments without
a clear error message.

**Suggested Fix:** Bundle a small set of test fixture files in the repository, or add a clear
skip with `pytest.importorskip` or `pytest.mark.skipif` when the network is unavailable.

---

### LOW-8: PostgreSQL-Specific Code

| Field | Value |
|-------|-------|
| **File** | `src/potluck/web/routers/settings.py` |
| **Lines** | 48 |
| **Module** | web |

**Description:** The settings page uses `pg_database_size(current_database())` to display
database size. This is PostgreSQL-specific and has no graceful fallback. The bare `except
Exception:` (line 50) catches the error but provides no indication of why it failed.

**Impact:** Minor -- Potluck only supports PostgreSQL. But if the query fails for other reasons
(permissions, connection issues), the generic exception handler masks the real error.

**Suggested Fix:** Catch `ProgrammingError` specifically and log a more descriptive message.

---

### LOW-9: Decimal vs Float Inconsistency

| Field | Value |
|-------|-------|
| **Files** | `src/potluck/models/financial.py`, `src/potluck/models/base.py` |
| **Module** | models |

**Description:** Financial amounts correctly use `Decimal` (in the `Transaction` and `Budget`
models), but latitude/longitude use `float` throughout (in `GeolocatedEntity`, `Location`,
`LocationVisit`, `LocationHistory`). While `float` is acceptable for coordinates, it is
inconsistent with the care taken for financial precision.

**Impact:** Minimal for coordinates (float64 provides ~15 decimal digits, more than enough for
geographic precision). This is primarily a style concern.

**Suggested Fix:** Document the intentional decision in a code comment explaining why `float` is
acceptable for coordinates.

---

### LOW-10: SocialPost.post_id vs permalink Semantics

| Field | Value |
|-------|-------|
| **File** | `src/potluck/models/social.py` |
| **Lines** | 87-99 |
| **Module** | models |

**Description:** `SocialPost` stores both `post_id` (platform-specific ID, line 87) and
`permalink` (permanent URL, line 96). It is unclear which field is the primary deduplication
key. The `content_hash` from `BaseEntity` is the actual dedup field, but neither `post_id`
nor `permalink` has a unique constraint.

**Impact:** Developers working on social media ingesters may be unsure which field to prioritize
for deduplication or external linking.

**Suggested Fix:** Add a comment or docstring clarifying the deduplication strategy: `content_hash`
is the primary dedup key, `post_id` is for platform-specific lookups, and `permalink` is for
user-facing links.

---

### LOW-11: Email Attachment media_id Nullable

| Field | Value |
|-------|-------|
| **File** | `src/potluck/models/email.py` |
| **Lines** | 284-288 |
| **Module** | models |

**Description:** `EmailAttachment.media_id` is nullable (`UUID | None`), allowing attachments
to exist without a corresponding `Media` record. Ideally, all attachments should be linked to
media, but the nullable design accommodates cases where the attachment file is not stored.

**Impact:** Code that assumes all attachments have media records will fail. Orphaned attachment
metadata may accumulate if media cleanup does not account for this.

**Suggested Fix:** Document the nullable design as intentional (for cases where attachment content
is not preserved), and add a query/report for orphaned attachments in the settings/admin page.

---

## Summary Statistics

| Severity | Count | Key Areas |
|----------|-------|-----------|
| Critical | 2 | Thread safety, SQL formatting |
| High | 9 | Pagination, security, Docker, dedup, field exclusions |
| Medium | 18 | Models, search, CLI, web, tests, caching |
| Low | 11 | Versioning, schema, style, test fixtures |
| **Total** | **40** | |

### Issues by Module

| Module | Critical | High | Medium | Low | Total |
|--------|----------|------|--------|-----|-------|
| search | 2 | 1 | 4 | 0 | 7 |
| models | 0 | 1 | 4 | 3 | 8 |
| core | 0 | 1 | 3 | 0 | 4 |
| web | 0 | 2 | 2 | 1 | 5 |
| pipeline | 0 | 1 | 2 | 2 | 5 |
| docker | 0 | 1 | 0 | 0 | 1 |
| scripts | 0 | 1 | 0 | 0 | 1 |
| db | 0 | 0 | 0 | 2 | 2 |
| tests | 0 | 0 | 3 | 2 | 5 |
| root | 0 | 0 | 0 | 1 | 1 |
| **Total** | **2** | **9** | **18** | **11** | **40** |
