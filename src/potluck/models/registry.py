"""Entity type to model class registry.

This module centralizes the mapping from EntityType enum values to their
corresponding SQLModel classes. It's separated from base.py to isolate
the circular import (all model modules import from base.py).

Usage:
    from potluck.models.registry import get_entity_type_model_map

    model_map = get_entity_type_model_map()
    MediaModel = model_map[EntityType.MEDIA]
"""

from functools import lru_cache

from sqlmodel import SQLModel

from potluck.models.base import EntityType


@lru_cache(maxsize=1)
def get_entity_type_model_map() -> dict[EntityType, type[SQLModel]]:
    """Get mapping from EntityType to model class.

    Returns a cached dict mapping EntityType enum values to their
    corresponding SQLModel classes.

    The imports are done inside this function because all model modules
    import from base.py, which would create a circular import if we
    imported them at module level.

    Returns:
        Dict mapping EntityType enum values to their corresponding model classes.
    """
    # These imports are intentionally inside the function to avoid circular imports.
    # All these modules import from potluck.models.base, so importing them at
    # module level would create: base.py -> registry.py -> media.py -> base.py
    from potluck.models.browsing import Bookmark, BrowsingHistory
    from potluck.models.calendar import CalendarEvent
    from potluck.models.email import Email
    from potluck.models.financial import Transaction
    from potluck.models.locations import LocationVisit
    from potluck.models.media import Media
    from potluck.models.messages import ChatMessage
    from potluck.models.notes import KnowledgeNote
    from potluck.models.people import Person
    from potluck.models.social import SocialComment, SocialPost

    return {
        EntityType.MEDIA: Media,
        EntityType.CHAT_MESSAGE: ChatMessage,
        EntityType.EMAIL: Email,
        EntityType.SOCIAL_POST: SocialPost,
        EntityType.SOCIAL_COMMENT: SocialComment,
        EntityType.KNOWLEDGE_NOTE: KnowledgeNote,
        EntityType.CALENDAR_EVENT: CalendarEvent,
        EntityType.TRANSACTION: Transaction,
        EntityType.LOCATION_VISIT: LocationVisit,
        EntityType.BROWSING_HISTORY: BrowsingHistory,
        EntityType.BOOKMARK: Bookmark,
        EntityType.PERSON: Person,
    }
