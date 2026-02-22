"""Entity card configuration registry.

Provides a unified mapping from EntityType to display configuration,
replacing ad-hoc if/elif chains in templates. Each CardConfig declares
how to extract a title, which fields to show on cards, and whether
thumbnails are available.
"""

from dataclasses import dataclass, field

from potluck.models.base import EntityType


@dataclass(frozen=True)
class CardConfig:
    """Display configuration for entity cards and detail pages.

    Attributes:
        title_field: Primary field to use as card title.
        title_fallbacks: Fallback fields if primary is None/empty.
        card_fields: Fields to show on compact card view.
        has_thumbnail: Whether to show a media thumbnail.
    """

    title_field: str
    title_fallbacks: list[str] = field(default_factory=list)
    card_fields: list[str] = field(default_factory=list)
    has_thumbnail: bool = False


ENTITY_CARD_CONFIG: dict[EntityType, CardConfig] = {
    EntityType.MEDIA: CardConfig(
        title_field="caption",
        title_fallbacks=["original_filename"],
        card_fields=["media_type", "location_name"],
        has_thumbnail=True,
    ),
    EntityType.CHAT_MESSAGE: CardConfig(
        title_field="sender_name",
        title_fallbacks=[],
        card_fields=["content"],
    ),
    EntityType.EMAIL: CardConfig(
        title_field="subject",
        title_fallbacks=[],
        card_fields=["from_name", "folder"],
    ),
    EntityType.SOCIAL_POST: CardConfig(
        title_field="title",
        title_fallbacks=["body"],
        card_fields=["community_name", "score"],
    ),
    EntityType.SOCIAL_COMMENT: CardConfig(
        title_field="body",
        title_fallbacks=["post_title"],
        card_fields=["community_name"],
    ),
    EntityType.CALENDAR_EVENT: CardConfig(
        title_field="summary",
        title_fallbacks=[],
        card_fields=["location_name"],
    ),
    EntityType.TRANSACTION: CardConfig(
        title_field="payee",
        title_fallbacks=[],
        card_fields=["amount", "category"],
    ),
    EntityType.BROWSING_HISTORY: CardConfig(
        title_field="title",
        title_fallbacks=["url"],
        card_fields=["domain"],
    ),
    EntityType.BOOKMARK: CardConfig(
        title_field="title",
        title_fallbacks=["url"],
        card_fields=["domain"],
    ),
    EntityType.KNOWLEDGE_NOTE: CardConfig(
        title_field="title",
        title_fallbacks=["content"],
        card_fields=["note_type"],
    ),
    EntityType.DOCUMENT: CardConfig(
        title_field="title",
        title_fallbacks=[],
        card_fields=["file_extension"],
    ),
    EntityType.LOCATION: CardConfig(
        title_field="name",
        title_fallbacks=[],
        card_fields=["address"],
    ),
    EntityType.LOCATION_VISIT: CardConfig(
        title_field="place_name",
        title_fallbacks=["address"],
        card_fields=["duration_minutes"],
    ),
    EntityType.SOCIAL_FOLLOW: CardConfig(
        title_field="target_name",
        title_fallbacks=[],
        card_fields=["platform", "follow_type"],
    ),
    EntityType.BUDGET: CardConfig(
        title_field="category",
        title_fallbacks=[],
        card_fields=["year", "month"],
    ),
    EntityType.PERSON: CardConfig(
        title_field="display_name",
        title_fallbacks=[],
        card_fields=[],
    ),
    EntityType.TAG: CardConfig(
        title_field="name",
        title_fallbacks=[],
        card_fields=["description"],
    ),
}


def get_entity_title(entity: object, config: CardConfig) -> str:
    """Extract display title from an entity using config-driven field lookup.

    Tries the primary title_field first, then each fallback in order.
    Returns a type + truncated ID as final fallback.

    Args:
        entity: The entity instance.
        config: CardConfig for this entity type.

    Returns:
        A non-empty string to use as the entity's display title.
    """
    val = getattr(entity, config.title_field, None)
    if val:
        return _truncate(str(val))

    for fb in config.title_fallbacks:
        val = getattr(entity, fb, None)
        if val:
            return _truncate(str(val))

    entity_id = getattr(entity, "id", "")
    return f"{type(entity).__name__} {str(entity_id)[:8]}"


def _truncate(text: str, max_length: int = 120) -> str:
    """Truncate text to max_length, adding ellipsis if needed."""
    if len(text) <= max_length:
        return text
    return text[: max_length - 1] + "\u2026"
