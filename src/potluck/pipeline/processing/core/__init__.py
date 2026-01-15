"""Core processing infrastructure.

This module provides base classes and utilities for processing:
- BaseProcessor: Abstract base class for all processors
- ProcessorRegistry: Registry for processor configuration and pipelines
- MLModels: Centralized ML model loading with caching
- run_processor_task: Shared Celery task implementation
"""

from potluck.core.constants import (
    DEFAULT_CAPTIONING_MODEL,
    DEFAULT_MULTIMODAL_MODEL,
    DEFAULT_TEXT_EMBEDDING_MODEL,
)
from potluck.pipeline.processing.core.base import (
    BaseProcessor,
    run_batch_processor_task,
    run_processor_task,
)
from potluck.pipeline.processing.core.ml import (
    MLModels,
    get_device,
)
from potluck.pipeline.processing.core.registry import (
    ProcessorConfig,
    ProcessorRegistry,
)

__all__ = [
    # Base
    "BaseProcessor",
    "run_processor_task",
    "run_batch_processor_task",
    # Registry
    "ProcessorRegistry",
    "ProcessorConfig",
    # ML
    "MLModels",
    "get_device",
    "DEFAULT_TEXT_EMBEDDING_MODEL",
    "DEFAULT_MULTIMODAL_MODEL",
    "DEFAULT_CAPTIONING_MODEL",
]
