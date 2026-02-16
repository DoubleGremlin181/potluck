# Pipeline Module - Development Guide

The `pipeline/` module provides a unified interface for data ingestion and media processing. It consolidates the former `ingesters/` and `processing/` modules into a cohesive architecture.

## Architecture Overview

```
pipeline/
├── __init__.py              # Public API exports
├── base.py                  # Abstract Stage base class
├── dtos.py                  # All DTOs and result types
├── orchestrator.py          # Main pipeline orchestration
├── tasks/                   # Celery task orchestration
│   ├── ingestion.py         # run_ingestion, cancel_ingestion
│   └── processing.py        # Pipeline functions, re-exports
├── ingestion/               # Data ingestion
│   ├── base.py              # BaseIngestionStage
│   ├── registry.py          # Stage registration
│   ├── google_takeout/      # Google Takeout stage (photos, chat, mail, calendar, chrome, location)
│   └── android_timeline/    # Android Timeline stage (Timeline.json location data)
├── processing/              # Media processing (self-contained)
│   ├── __init__.py          # Auto-discovery + exports
│   ├── core/                # Base infrastructure
│   │   ├── __init__.py      # Core exports
│   │   ├── base.py          # BaseProcessor + run_processor_task
│   │   ├── registry.py      # ProcessorRegistry
│   │   └── ml.py            # MLModels centralized loading
│   ├── processors/          # Actual processing implementations
│   │   ├── hashing.py       # HashingProcessor + Celery task
│   │   ├── metadata.py      # MetadataProcessor + Celery task
│   │   ├── ocr.py           # OCRProcessor + Celery task (ML)
│   │   ├── faces.py         # FaceProcessor + Celery task (ML)
│   │   ├── embeddings.py    # Embedding processors (text + media)
│   │   ├── captioning.py    # CaptioningProcessor + Celery task (ML)
│   │   └── clustering.py    # cluster_unassigned_faces task (ML)
│   ├── _arcface/            # Private ArcFace implementation
│   └── linkers/             # Cross-entity semantic linking
└── utils/                   # Shared utilities
    ├── archive.py           # ZIP/TAR extraction
    ├── hashing.py           # SHA256/content hashing
    └── parsers.py           # CSV, JSON, MBOX parsing
```

## Key Concepts

### Stage Pattern
All pipeline operations extend the abstract `Stage` base class:

```python
from potluck.pipeline import Stage

class Stage[InputT, OutputT](ABC):
    NAME: ClassVar[str]  # Unique identifier

    @abstractmethod
    def execute(self, input_data: InputT) -> OutputT:
        """Process input and return result."""

    def should_execute(self, input_data: InputT) -> bool:
        """Optional: check if stage should run."""
        return True
```

### Ingestion Stages
For importing data from external sources (Google Takeout, Reddit, etc.):

```python
from potluck.pipeline import BaseIngestionStage, register, DetectionResult

@register  # Register in the global registry
class MyIngestionStage(BaseIngestionStage):
    SOURCE_TYPE = SourceType.MY_SOURCE  # Required
    FILENAME_PATTERNS = [r"my-export-.*\.zip"]  # Required
    SUPPORTED_ENTITY_TYPES = {EntityType.MEDIA}  # Required

    def detect(self, path: Path) -> DetectionResult:
        """Scan path and return what entities are available."""
        count = sum(1 for f in path.rglob("*.jpg"))
        return DetectionResult(entity_counts={EntityType.MEDIA: count})

    def execute(self, path, entity_types, filters=None) -> Iterator[BaseEntity]:
        """Yield entities found in the path."""
        for file in path.rglob("*.jpg"):
            yield Media(file_path=str(file), ...)
```

### Processing Processors
Each processor is self-contained with business logic AND Celery task in one file:

```python
from potluck.pipeline.processing.core.base import BaseProcessor, run_processor_task
from potluck.pipeline.dtos import StageResult, StageStatus
from potluck.core.celery import celery_app

class MyProcessor(BaseProcessor):
    NAME = "my_processor"
    PERSIST_FIELDS = ["field1", "field2"]  # Auto-persists to Media model

    def execute(self, media: Media) -> StageResult:
        # Implementation
        return StageResult(
            item_id=media.id,
            stage_name=self.NAME,
            status=StageStatus.COMPLETED,
            data={"field1": "value", "field2": "value"},
        )

    def should_execute(self, media: Media) -> bool:
        return media.media_type == MediaType.IMAGE

# Celery task co-located with processor
@celery_app.task(bind=True, queue="process", ...)
def run_my_processor(self, media_id: str) -> dict[str, Any]:
    return run_processor_task(self, media_id, MyProcessor)
```

### Persistence Patterns

**Simple fields (PERSIST_FIELDS):** Declare which `result.data` keys map to Media model fields:
```python
class HashingProcessor(BaseProcessor):
    PERSIST_FIELDS = ["file_hash", "perceptual_hash"]
```

**Complex persistence (override persist_result):** For processors that create related records:
```python
class FaceProcessor(BaseProcessor):
    # No PERSIST_FIELDS - override instead

    def persist_result(self, session, media_id, result) -> dict[str, Any]:
        # Create MediaPersonLink records for detected faces
        for face_data in result.data.get("faces", []):
            face_link = MediaPersonLink(media_id=UUID(media_id), ...)
            session.add(face_link)
        session.commit()
        return {"faces_detected": len(faces), ...}
```

## DTOs Reference

| DTO | Purpose |
|-----|---------|
| `StageStatus` | Enum: PENDING, RUNNING, COMPLETED, FAILED, SKIPPED |
| `StageResult` | Single stage execution result |
| `BatchStageResult` | Batch processing result |
| `PipelineFilter` | Date range filtering (since, until) |
| `PipelineStats` | Counts: created, updated, skipped, failed |
| `PipelineResult` | Full pipeline result with import run |
| `DetectionResult` | Entity counts from detection phase |
| `DiscoveryResult` | Discovery result with source type |

## Celery Tasks

### Ingestion
```python
from potluck.pipeline import run_ingestion, cancel_ingestion

# Async ingestion
run_ingestion.delay("/path/to/data")

# Cancel running job
cancel_ingestion.delay(job_id)
```

### Processing
```python
from potluck.pipeline.tasks import (
    run_hashing_processor,
    run_metadata_processor,
    run_ocr_processor,
    run_faces_processor,
    run_captioning_processor,
    run_processing_pipeline,
    run_basic_processing,
    cluster_unassigned_faces,
)

# Full pipeline
run_processing_pipeline(media_id)

# Basic processing (no ML deps)
run_basic_processing(media_id)
```

## Adding a New Processor

1. Create `processing/processors/my_processor.py` (self-contained):
```python
from potluck.pipeline.processing.core.base import BaseProcessor, run_processor_task
from potluck.pipeline.dtos import StageResult, StageStatus
from potluck.core.celery import celery_app, ...

class MyProcessor(BaseProcessor):
    NAME = "my_processor"
    PERSIST_FIELDS = ["result_field"]

    def execute(self, media: Media) -> StageResult:
        # Implementation
        pass

@celery_app.task(bind=True, queue="process", ...)
def run_my_processor(self, media_id: str) -> dict[str, Any]:
    return run_processor_task(self, media_id, MyProcessor)
```

2. **Done.** Auto-discovery imports the module and registers the task.
   - No need to modify `processing/__init__.py`
   - No need to modify `tasks/processing.py`
   - Add to pipeline functions only if you want it in the default chain

## Auto-Discovery

Both ingestion stages and processing processors use `pkgutil` auto-discovery.

**Ingestion stages** (`ingestion/__init__.py`): Discovers all packages (directories
with `__init__.py`) under `ingestion/`. Each package's `@register` decorator runs
on import, registering the stage in the global registry:

```python
for module_info in pkgutil.iter_modules([str(ingestion_dir)]):
    if module_info.ispkg:  # Only packages, not base.py/registry.py
        importlib.import_module(f".{module_info.name}", __package__)
```

**Processing processors** (`processing/__init__.py`): Discovers all modules in the
`processors/` subdirectory. This triggers Celery task registration:

```python
for module_info in pkgutil.iter_modules([str(processors_dir)]):
    importlib.import_module(f".processors.{module_info.name}", __package__)
```

## ML Dependencies

All ML dependencies (EasyOCR, MTCNN, ArcFace, BLIP-2) are always available -
they are installed as part of the standard Docker/development setup via `uv sync --extra ml`.

All processors can be imported directly:
```python
from potluck.pipeline.processing import (
    HashingProcessor,
    MetadataProcessor,
    OCRProcessor,
    FaceProcessor,
    CaptioningProcessor,
)
```

## Testing

Tests are in `tests/unit/pipeline/`:
- `ingestion/` - Ingestion stage tests
- `processing/` - Processing processor tests

Use `@pytest.mark.ml` for tests requiring ML dependencies.

## Queue Configuration

Celery routes tasks to different queues:
- `ingest` queue: `run_ingestion`, `cancel_ingestion`
- `process` queue: All `run_*_processor` tasks

Configure in `core/celery.py`:
```python
task_routes = {
    "potluck.pipeline.tasks.ingestion.*": {"queue": "ingest"},
    "potluck.pipeline.tasks.processing.*": {"queue": "process"},
}
```

