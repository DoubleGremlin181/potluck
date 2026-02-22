"""Processor registry for pipeline configuration.

This module provides a registry for processors that enables:
- Auto-registration of processors via decorator
- Dynamic pipeline construction per entity type
- Priority-based ordering of processors

Each processor registers itself with its supported entity types and priority.
The registry builds processing pipelines dynamically at runtime.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, ClassVar

from potluck.core.logging import get_logger
from potluck.models.base import EntityType
from potluck.pipeline.processing.core.base import BaseProcessor

logger = get_logger(__name__)


@dataclass(frozen=True)
class ProcessorConfig:
    """Configuration for a registered processor.

    Attributes:
        processor_class: The processor class.
        priority: Execution order (lower = runs first). Default 100.
        batch_task_func: Celery task for batch processing. Used by
            the batch-by-processor pipeline to process all entities of a type
            in a single task, loading one model at a time.
    """

    processor_class: type[BaseProcessor]
    priority: int = 100
    batch_task_func: Callable[..., Any] | None = field(default=None)


class ProcessorRegistry:
    """Registry mapping entity types to their processing pipelines.

    This registry enables dynamic pipeline construction. Processors register
    themselves via the @register decorator, declaring which entity types they
    support. The registry then builds ordered pipelines per entity type.

    Example:
        @ProcessorRegistry.register(priority=10)
        class HashingProcessor(BaseProcessor):
            NAME = "hashing"
            SUPPORTED_ENTITY_TYPES = {EntityType.MEDIA}
            ...

        # Get batch pipeline for an entity type
        pipeline = ProcessorRegistry.get_batch_pipeline(EntityType.MEDIA)
    """

    _processors: ClassVar[dict[str, ProcessorConfig]] = {}

    @classmethod
    def register(
        cls,
        *,
        priority: int = 100,
    ) -> Callable[[type[BaseProcessor]], type[BaseProcessor]]:
        """Decorator to register a processor class.

        Args:
            priority: Execution order (lower = runs first). Default 100.

        Returns:
            Decorator function that registers the processor.

        Example:
            @ProcessorRegistry.register(priority=10)
            class MyProcessor(BaseProcessor):
                NAME = "my_processor"
                SUPPORTED_ENTITY_TYPES = {EntityType.MEDIA}
        """

        def decorator(processor_class: type[BaseProcessor]) -> type[BaseProcessor]:
            if not hasattr(processor_class, "NAME"):
                raise ValueError(f"Processor {processor_class.__name__} must define NAME attribute")

            if not hasattr(processor_class, "SUPPORTED_ENTITY_TYPES"):
                raise ValueError(
                    f"Processor {processor_class.__name__} must define "
                    "SUPPORTED_ENTITY_TYPES attribute"
                )

            name = processor_class.NAME
            if name in cls._processors:
                logger.warning(f"Overwriting existing processor: {name}")

            cls._processors[name] = ProcessorConfig(
                processor_class=processor_class,
                priority=priority,
            )

            logger.debug(
                f"Registered processor: {name} "
                f"(types={processor_class.SUPPORTED_ENTITY_TYPES}, priority={priority})"
            )

            return processor_class

        return decorator

    @classmethod
    def set_batch_task(cls, processor_name: str, batch_task_func: Callable[..., Any]) -> None:
        """Set the batch Celery task function for a registered processor.

        Args:
            processor_name: The processor NAME.
            batch_task_func: The Celery task function for batch processing.
        """
        if processor_name not in cls._processors:
            raise ValueError(f"Processor not registered: {processor_name}")

        config = cls._processors[processor_name]
        cls._processors[processor_name] = ProcessorConfig(
            processor_class=config.processor_class,
            priority=config.priority,
            batch_task_func=batch_task_func,
        )

    @classmethod
    def get_batch_pipeline(cls, entity_type: EntityType) -> list[ProcessorConfig]:
        """Get ordered list of processor configs with batch tasks for an entity type.

        Only returns processors that have a batch_task_func registered.
        Used by the batch-by-processor pipeline.

        Args:
            entity_type: The entity type to get pipeline for.

        Returns:
            List of ProcessorConfig (with batch_task_func) sorted by priority.
        """
        configs = [
            config
            for config in cls._processors.values()
            if entity_type in config.processor_class.SUPPORTED_ENTITY_TYPES
            and config.batch_task_func is not None
        ]
        return sorted(configs, key=lambda c: c.priority)

    @classmethod
    def clear(cls) -> None:
        """Clear all registered processors. Useful for testing."""
        cls._processors.clear()
