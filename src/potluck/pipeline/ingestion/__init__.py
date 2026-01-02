"""Ingestion stages for importing data from various sources.

This module provides the ingestion infrastructure for importing data from
various sources (Google Takeout, Reddit, WhatsApp, etc.) into Potluck.

Public API:
    - register: Decorator to register an ingestion stage class
    - detect_stage: Find stage matching a path's filename pattern
    - get_stage: Get stage by source type
    - list_stages: List all registered stages
    - clear_registry: Clear all registered stages (for testing)
    - BaseIngestionStage: Abstract base class for implementing stages
"""

from potluck.pipeline.dtos import DetectionResult, PipelineFilter
from potluck.pipeline.ingestion.base import BaseIngestionStage
from potluck.pipeline.ingestion.registry import (
    clear_registry,
    detect_stage,
    get_stage,
    list_stages,
    register,
)

__all__ = [
    # Registration
    "register",
    "detect_stage",
    "get_stage",
    "list_stages",
    "clear_registry",
    # Base class
    "BaseIngestionStage",
    # DTOs (re-exported for convenience)
    "DetectionResult",
    "PipelineFilter",
]
