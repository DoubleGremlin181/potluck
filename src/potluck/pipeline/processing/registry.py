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
from dataclasses import dataclass
from typing import Any, ClassVar

from potluck.core.logging import get_logger
from potluck.models.base import EntityType
from potluck.pipeline.processing.base import BaseProcessor

logger = get_logger(__name__)


@dataclass
class ProcessorConfig:
    """Configuration for a registered processor.

    Attributes:
        processor_class: The processor class.
        task_func: The Celery task function for this processor.
        priority: Execution order (lower = runs first). Default 100.
    """

    processor_class: type[BaseProcessor]
    task_func: Callable[..., Any]
    priority: int = 100


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

        # Get pipeline for an entity type
        pipeline = ProcessorRegistry.get_pipeline(EntityType.MEDIA)
    """

    _processors: ClassVar[dict[str, ProcessorConfig]] = {}

    @classmethod
    def register(
        cls,
        *,
        priority: int = 100,
        task_func: Callable[..., Any] | None = None,
    ) -> Callable[[type[BaseProcessor]], type[BaseProcessor]]:
        """Decorator to register a processor class.

        Args:
            priority: Execution order (lower = runs first). Default 100.
            task_func: The Celery task function. Can be set later via set_task().

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

            # Create placeholder config - task_func may be set later
            cls._processors[name] = ProcessorConfig(
                processor_class=processor_class,
                task_func=task_func or _placeholder_task,
                priority=priority,
            )

            logger.debug(
                f"Registered processor: {name} "
                f"(types={processor_class.SUPPORTED_ENTITY_TYPES}, priority={priority})"
            )

            return processor_class

        return decorator

    @classmethod
    def set_task(cls, processor_name: str, task_func: Callable[..., Any]) -> None:
        """Set the Celery task function for a registered processor.

        This is typically called after the processor class is defined,
        when the Celery task is created.

        Args:
            processor_name: The processor NAME.
            task_func: The Celery task function.
        """
        if processor_name not in cls._processors:
            raise ValueError(f"Processor not registered: {processor_name}")

        config = cls._processors[processor_name]
        cls._processors[processor_name] = ProcessorConfig(
            processor_class=config.processor_class,
            task_func=task_func,
            priority=config.priority,
        )

    @classmethod
    def get_pipeline(cls, entity_type: EntityType) -> list[ProcessorConfig]:
        """Get ordered list of processor configs for an entity type.

        Returns processors that support the given entity type, sorted by priority
        (lower priority values run first).

        Args:
            entity_type: The entity type to get pipeline for.

        Returns:
            List of ProcessorConfig sorted by priority.
        """
        configs = [
            config
            for config in cls._processors.values()
            if entity_type in config.processor_class.SUPPORTED_ENTITY_TYPES
        ]
        return sorted(configs, key=lambda c: c.priority)

    @classmethod
    def get_processor(cls, name: str) -> ProcessorConfig | None:
        """Get a processor config by name.

        Args:
            name: The processor NAME.

        Returns:
            ProcessorConfig or None if not found.
        """
        return cls._processors.get(name)

    @classmethod
    def list_processors(cls) -> list[ProcessorConfig]:
        """Get all registered processor configs.

        Returns:
            List of all ProcessorConfig instances.
        """
        return list(cls._processors.values())

    @classmethod
    def clear(cls) -> None:
        """Clear all registered processors. Useful for testing."""
        cls._processors.clear()


def _placeholder_task(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Placeholder task for processors that haven't set their task yet."""
    raise NotImplementedError(
        "Processor task not set. Call ProcessorRegistry.set_task() after defining the Celery task."
    )
