# Pipeline Module

Unified interface for data ingestion and entity processing. Handles importing data from external sources, running ML processing pipelines, and creating cross-entity links.

## Directory Structure

```
pipeline/
├── base.py                     # Stage[InputT, OutputT] abstract base
├── dtos.py                     # All DTOs (StageResult, PipelineResult, etc.)
├── orchestrator.py             # PipelineOrchestrator (batch, dedup, flush)
├── ingestion/
│   ├── base.py                 # BaseIngestionStage (FILENAME_PATTERNS, detect, execute)
│   ├── registry.py             # @register decorator, detect_stage(), get_stage()
│   ├── google_takeout/         # Photos, chat, mail, calendar, chrome, location, keep
│   ├── android_timeline/       # Timeline.json location data
│   ├── reddit/                 # Posts, comments, subscriptions
│   ├── whatsapp/               # Messages, media
│   ├── ynab/                   # Budget, transactions
│   ├── mbox/                   # Email from MBOX files
│   ├── image_folder/           # Loose image directories
│   └── text_files/             # Plain text/markdown documents
├── processing/
│   ├── core/
│   │   ├── base.py             # BaseProcessor, run_batch_processor_task(), run_batch_stage_task()
│   │   ├── registry.py         # ProcessorRegistry with priority ordering
│   │   └── ml.py               # MLModels centralized loading
│   ├── processors/
│   │   ├── hashing.py          # HashingProcessor (priority 10)
│   │   ├── metadata.py         # MetadataProcessor (priority 20)
│   │   ├── ocr.py              # OCRProcessor (priority 30)
│   │   ├── faces.py            # FaceProcessor (priority 40)
│   │   ├── captioning.py       # CaptioningProcessor (priority 50)
│   │   ├── embeddings.py       # Text + media embedding processors (priority 60+)
│   │   └── clustering.py       # Face clustering task
│   ├── linkers/
│   │   ├── base.py             # BaseLinker abstract class
│   │   ├── temporal.py         # TemporalLinker (time proximity)
│   │   ├── spatial.py          # SpatialLinker (location proximity)
│   │   └── semantic.py         # SemanticLinker (embedding similarity)
│   └── _arcface/               # Vendored ArcFace implementation
├── tasks/
│   ├── ingestion.py            # run_ingestion, cancel_ingestion Celery tasks
│   └── processing.py           # Pipeline orchestration, linker tasks, re-exports
└── utils/
    ├── archive.py              # ZIP/TAR extraction (context manager)
    ├── hashing.py              # SHA256, content hashing
    ├── parsers.py              # CSV, JSON, MBOX parsing
    ├── media.py                # Media type detection
    └── email.py                # Email parsing utilities
```

## Stage Pattern

All pipeline operations extend the generic `Stage` base class:

```python
class Stage[InputT, OutputT](ABC):
    NAME: ClassVar[str]

    @abstractmethod
    def execute(self, input_data: InputT) -> OutputT: ...

    def should_execute(self, input_data: InputT) -> bool:
        return True
```

## Ingestion

### BaseIngestionStage

Each data source implements `BaseIngestionStage` with three required class attributes:

```python
@register
class GoogleTakeoutStage(BaseIngestionStage):
    SOURCE_TYPE = SourceType.GOOGLE_TAKEOUT
    FILENAME_PATTERNS = [r"takeout-.*\.zip"]
    SUPPORTED_ENTITY_TYPES = {EntityType.MEDIA, EntityType.EMAIL, ...}

    def detect(self, path: Path) -> DetectionResult: ...
    def execute(self, input_data, entity_types, filters) -> Iterator[IngestableEntity]: ...
```

- `FILENAME_PATTERNS` enables auto-detection: the registry matches filenames against these regex patterns
- `detect()` scans the extracted content and returns entity type counts
- `execute()` yields entities one by one for batch persistence

### Registry

The `@register` decorator adds stages to a global `_STAGES` dict keyed by `SourceType`. Two lookup modes:

- `detect_stage(path)` -- matches path name against all registered `FILENAME_PATTERNS`
- `get_stage(source_type)` -- direct lookup by `SourceType` enum

Auto-discovery: `ingestion/__init__.py` uses `pkgutil.iter_modules()` to import all sub-packages, triggering their `@register` decorators.

### Supported Sources

| Source | Entity Types |
|--------|-------------|
| Google Takeout | Media, chat, mail, calendar, chrome bookmarks, location, keep notes |
| Android Timeline | Location history |
| Reddit | Posts, comments, subscriptions |
| WhatsApp | Messages, media |
| YNAB | Budget, transactions |
| MBOX | Email |
| Image Folder | Media |
| Text Files | Documents |

### PipelineOrchestrator

The orchestrator handles the full ingestion lifecycle:

1. **Archive extraction** -- `extracted()` context manager handles ZIP/TAR
2. **Source detection** -- matches path against registered stages
3. **Entity discovery** -- calls `stage.detect()` for counts
4. **Duplicate checking** -- source-level (file hash) and entity-level (content hash)
5. **Batch persistence** -- entities are sorted by FK dependency order, flushed in batches (default 100)
6. **Processing queue** -- persisted entities are queued as batches per entity type for Celery processing
7. **Linker queue** -- batch linkers are queued after import completes

## Processing

### BaseProcessor

Each processor is self-contained in one file with business logic and Celery task:

```python
@ProcessorRegistry.register(priority=10)
class HashingProcessor(BaseProcessor):
    NAME = "hashing"
    SUPPORTED_ENTITY_TYPES = {EntityType.MEDIA}
    PERSIST_FIELDS = ["file_hash", "perceptual_hash"]

    def execute(self, input_data: SQLModel) -> StageResult: ...
```

**Priority ordering** determines execution sequence (lower = runs first):

| Priority | Processor | Purpose |
|----------|-----------|---------|
| 10 | Hashing | SHA256 + perceptual hash |
| 20 | Metadata | EXIF, dimensions, GPS |
| 30 | OCR | Text extraction from images |
| 40 | Faces | Face detection + recognition |
| 50 | Captioning | Image caption generation |
| 60+ | Embeddings | Text (e5-small-v2) + multimodal (SigLIP) |

**Persistence patterns:**
- Simple: set `PERSIST_FIELDS` to auto-map `result.data` keys to entity model fields
- Complex: override `persist_result()` for creating related records (e.g., `FaceProcessor` creates `MediaPersonLink` entries)

### ProcessorRegistry

Two-phase registration:
1. `@ProcessorRegistry.register(priority=N)` -- registers the class
2. `ProcessorRegistry.set_batch_task(name, batch_task_func)` -- sets the batch Celery task after it is defined

`get_batch_pipeline(entity_type)` returns an ordered list of processors for a given entity type (only those with a `batch_task_func` registered), sorted by priority.

### Auto-Discovery

`processing/__init__.py` uses `pkgutil.iter_modules()` to import all modules in `processors/`. This triggers both class registration (via `@ProcessorRegistry.register`) and Celery task registration.

## Linkers

Linkers run after import completes to create `EntityLink` records between related entities:

- **TemporalLinker** -- links entities that occurred close together in time
- **SpatialLinker** -- links entities at nearby locations
- **SemanticLinker** -- links entities with similar embeddings

Linkers extend `BaseLinker` which provides `find_links()` (within a type) and `find_cross_type_links()` (across types), plus deduplication-aware persistence.

## Task Orchestration

### Ingestion Tasks (`tasks/ingestion.py`)
- `run_ingestion` -- Celery task that creates `ImportSource`/`ImportRun`, extracts archives, and runs the orchestrator
- `cancel_ingestion` -- Cancels a running import by revoking the Celery task

### Processing Tasks (`tasks/processing.py`)
- `run_entity_pipeline(entity_type, entity_id)` -- convenience wrapper around `run_batch_entity_pipeline` for single-entity reprocessing
- `run_batch_entity_pipeline(entity_type, entity_ids)` -- builds a Celery chain from the ProcessorRegistry batch pipeline
- `run_linkers_batch(import_run_id, entity_ids_by_type)` -- runs all linkers on entities from an import
- Individual processor tasks are re-exported for direct access

## Data Flow

```
Upload/CLI
    |
    v
extract archive (ZIP/TAR)
    |
    v
detect source (filename pattern match)
    |
    v
discover entities (stage.detect -> counts)
    |
    v
ingest (stage.execute -> yield entities)
    |
    v
batch + dedup + sort by FK deps + flush to DB
    |
    v
queue batch processing pipeline per entity type
    |
    v
Hashing (batch) -> Metadata (batch) -> Media Embedding (batch) -> OCR (batch) -> Faces (batch) -> Captioning (batch)
    |
    v
queue batch linkers (Temporal, Spatial, Semantic)
```

## Utils

- **`archive.py`** -- `extracted()` context manager for automatic ZIP/TAR extraction and cleanup, with nested archive support
- **`hashing.py`** -- SHA256 file hashing, content hashing for deduplication
- **`parsers.py`** -- CSV, JSON, MBOX file parsing utilities
- **`media.py`** -- Media type detection from file extensions and MIME types
- **`email.py`** -- Email header parsing, body extraction
