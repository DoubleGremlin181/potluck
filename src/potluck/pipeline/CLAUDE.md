# Pipeline Module - Development Guide

The `pipeline/` module provides a unified interface for data ingestion and media processing. It consolidates the former `ingesters/` and `processing/` modules into a cohesive architecture.

## Architecture Overview

```
pipeline/
├── __init__.py              # Public API exports
├── base.py                  # Abstract Stage base class
├── dtos.py                  # All DTOs and result types
├── orchestrator.py          # Main pipeline orchestration
├── tasks/                   # Celery background tasks
│   ├── ingestion.py         # run_ingestion, cancel_ingestion
│   └── processing.py        # run_*_stage tasks
├── ingestion/               # Data ingestion
│   ├── base.py              # BaseIngestionStage
│   └── registry.py          # Stage registration
├── processing/              # Media processing
│   ├── base.py              # BaseProcessingStage
│   ├── hashing.py           # HashingStage
│   ├── metadata.py          # MetadataStage
│   ├── ocr.py               # OCRStage (ML)
│   ├── faces.py             # FaceStage (ML)
│   └── captioning.py        # CaptioningStage (ML)
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

class Stage(ABC, Generic[InputT, OutputT]):
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

### Processing Stages
For processing Media after ingestion:

```python
from potluck.pipeline import BaseProcessingStage
from potluck.pipeline.dtos import StageResult, StageStatus

class MyProcessingStage(BaseProcessingStage):
    NAME = "my_stage"

    def execute(self, media: Media) -> StageResult:
        # Do processing
        return StageResult(
            item_id=media.id,
            stage_name=self.NAME,
            status=StageStatus.COMPLETED,
            data={"result": "value"},
        )

    def should_execute(self, media: Media) -> bool:
        return media.media_type == MediaType.IMAGE
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
    run_hashing_stage,
    run_metadata_stage,
    run_ocr_stage,
    run_faces_stage,
    run_captioning_stage,
    run_processing_pipeline,
    run_basic_processing,
    cluster_unassigned_faces,
)

# Full pipeline
run_processing_pipeline(media_id)

# Basic processing (no ML deps)
run_basic_processing(media_id)
```

## Adding a New Processing Stage

1. Create `processing/my_stage.py`:
```python
from potluck.pipeline.processing.base import BaseProcessingStage
from potluck.pipeline.dtos import StageResult, StageStatus

class MyStage(BaseProcessingStage):
    NAME = "my_stage"

    def execute(self, media: Media) -> StageResult:
        # Implementation
        pass
```

2. Add Celery task in `tasks/processing.py`:
```python
@celery_app.task(bind=True, queue="process", ...)
def run_my_stage(self, media_id: str) -> dict[str, Any]:
    stage = MyStage()
    result = stage.execute(media)
    return {"status": result.status.value, ...}
```

3. Export if non-ML from `processing/__init__.py`

## ML Dependencies

OCR, face detection, and captioning require ML dependencies:
```bash
pip install potluck[ml]
```

These stages are NOT exported from the main `pipeline/__init__.py` to avoid import errors. Import directly:
```python
from potluck.pipeline.processing.ocr import OCRStage
from potluck.pipeline.processing.faces import FaceStage
from potluck.pipeline.processing.captioning import CaptioningStage
```

## Testing

Tests are in `tests/unit/pipeline/`:
- `ingestion/` - Ingestion stage tests
- `processing/` - Processing stage tests

Use `@pytest.mark.ml` for tests requiring ML dependencies.

## Queue Configuration

Celery routes tasks to different queues:
- `ingest` queue: `run_ingestion`, `cancel_ingestion`
- `process` queue: All `run_*_stage` tasks

Configure in `core/celery.py`:
```python
task_routes = {
    "potluck.pipeline.tasks.ingestion.*": {"queue": "ingest"},
    "potluck.pipeline.tasks.processing.*": {"queue": "process"},
}
```
