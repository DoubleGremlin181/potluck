"""Search utility functions.

Consolidated utilities for search operations, including model discovery
and field introspection.
"""

from typing import Any, cast

from sqlalchemy import inspect
from sqlalchemy.orm import Mapper
from sqlmodel import SQLModel

from potluck.models import get_entity_type_model_map
from potluck.models.base import EntityType


def get_searchable_models() -> dict[EntityType, type[SQLModel]]:
    """Get all models that have search enabled.

    Returns:
        Mapping from EntityType to model class for all searchable entities.
    """
    entity_map = get_entity_type_model_map()
    return {
        et: model for et, model in entity_map.items() if getattr(model, "__searchable__", False)
    }


def get_searchable_entity_types() -> set[EntityType]:
    """Get all entity types that support search.

    Returns:
        Set of EntityType values that have __searchable__ = True.
    """
    return set(get_searchable_models().keys())


def get_model_text_fields(model: type[SQLModel]) -> list[str]:
    """Get text fields for a model, excluding configured exclusions.

    Auto-discovers string columns and filters out:
    - Fields in __search_exclude_fields__
    - Common non-searchable fields (ids, hashes, urls, paths, etc.)

    Args:
        model: SQLModel class to introspect.

    Returns:
        List of field names that should be included in FTS.
    """
    # Default exclusions for common non-text-searchable fields
    default_exclusions = {
        "id",
        "source_id",
        "content_hash",
        "file_hash",
        "url_hash",
        "perceptual_hash",
        "file_path",
        "photo_url",
        "source_url",
        "favicon_url",
        "thumbnail_url",
        "icon_uri",
        "google_maps_url",
        "conference_url",
        "permalink",
        "link_url",
        "referrer_url",
        "search_vector",
        "embedding",
        "multimodal_embedding",
        "merged_into_id",
        "thread_id",
        "sender_id",
        "author_id",
        "person_id",
        "media_id",
        "account_id",
        "folder_id",
        "parent_id",
        "reply_to_id",
        "cluster_id",
        "organizer_id",
        "payee_id",
        "transfer_account_id",
        "location_id",
        "post_id",
        "event_id",
        "ical_uid",
        "message_id",
        "comment_id",
        "crosspost_parent_id",
        "recurring_event_id",
        "source_media_id",
        "parent_comment_id",
        "linked_media_ids",
        "attachment_urls",
        "media_urls",
        "participant_ids",
        "reminder_minutes",
    }

    # Get model-specific exclusions
    model_exclusions: set[str] = getattr(model, "__search_exclude_fields__", set())
    all_exclusions = default_exclusions | model_exclusions

    # Get string columns from the model
    text_fields: list[str] = []

    try:
        mapper = cast(Mapper[Any], inspect(model))
        for column in mapper.columns:
            # Check if it's a string type column not in exclusions
            column_type = str(column.type)
            is_text_type = (
                "VARCHAR" in column_type or "TEXT" in column_type or "String" in column_type
            )
            if is_text_type and column.name not in all_exclusions:
                text_fields.append(column.name)
    except Exception:
        # Fallback: use model __annotations__ if inspection fails
        for field_name, field_type in getattr(model, "__annotations__", {}).items():
            type_str = str(field_type)
            if "str" in type_str.lower() and field_name not in all_exclusions:
                text_fields.append(field_name)

    return text_fields


def get_model_priority_fields(model: type[SQLModel]) -> set[str]:
    """Get priority fields for a model (weight 'A' in FTS).

    Args:
        model: SQLModel class to check.

    Returns:
        Set of field names that should have priority weighting.
    """
    return getattr(model, "__search_priority_fields__", set())


def get_model_date_fields(model: type[SQLModel]) -> set[str]:
    """Get date fields for a model (for date-range filtering).

    Args:
        model: SQLModel class to check.

    Returns:
        Set of field names that are date/datetime fields for filtering.
        Defaults to {"created_at"} if not specified.
    """
    date_fields: set[str] = getattr(model, "__search_date_fields__", set())
    if not date_fields:
        # Default to common date field patterns
        date_fields = {"created_at"}
        if hasattr(model, "occurred_at"):
            date_fields = {"occurred_at"}
    return date_fields


def get_primary_date_field(model: type[SQLModel]) -> str:
    """Get the primary date field for a model.

    Returns the first date field, preferring occurred_at over created_at.

    Args:
        model: SQLModel class to check.

    Returns:
        Field name to use for date filtering.
    """
    date_fields = get_model_date_fields(model)
    if "occurred_at" in date_fields:
        return "occurred_at"
    if "created_at" in date_fields:
        return "created_at"
    # Return first available
    return next(iter(date_fields), "created_at")


def build_search_text(entity: Any, model: type[SQLModel]) -> str:
    """Build searchable text from an entity for embedding generation.

    Concatenates all text fields, with priority fields first.

    Args:
        entity: Entity instance to extract text from.
        model: Model class for field configuration.

    Returns:
        Concatenated text for embedding.
    """
    priority_fields = get_model_priority_fields(model)
    text_fields = get_model_text_fields(model)

    parts: list[str] = []

    # Add priority fields first
    for field in priority_fields:
        value = getattr(entity, field, None)
        if value:
            parts.append(str(value))

    # Add remaining text fields
    for field in text_fields:
        if field not in priority_fields:
            value = getattr(entity, field, None)
            if value:
                parts.append(str(value))

    return " ".join(parts)
