"""Tests for entity card configuration registry."""

from types import SimpleNamespace

import pytest

from potluck.models.base import EntityType
from potluck.web.entity_config import (
    ENTITY_CARD_CONFIG,
    CardConfig,
    get_entity_title,
)


class TestCardConfig:
    """Tests for CardConfig dataclass."""

    def test_card_config_defaults(self) -> None:
        """CardConfig has sensible defaults."""
        config = CardConfig(title_field="name")
        assert config.title_field == "name"
        assert config.title_fallbacks == []
        assert config.card_fields == []
        assert config.has_thumbnail is False

    def test_card_config_frozen(self) -> None:
        """CardConfig is frozen (immutable)."""
        config = CardConfig(title_field="name")
        with pytest.raises(AttributeError):
            config.title_field = "other"  # type: ignore[misc]


class TestEntityCardConfig:
    """Tests for ENTITY_CARD_CONFIG registry."""

    def test_all_entity_types_have_config(self) -> None:
        """Every EntityType should have a CardConfig entry."""
        for entity_type in EntityType:
            assert entity_type in ENTITY_CARD_CONFIG, f"Missing CardConfig for {entity_type.value}"

    def test_media_has_thumbnail(self) -> None:
        """Media should be the only type with has_thumbnail=True."""
        media_config = ENTITY_CARD_CONFIG[EntityType.MEDIA]
        assert media_config.has_thumbnail is True

        for et, config in ENTITY_CARD_CONFIG.items():
            if et != EntityType.MEDIA:
                assert config.has_thumbnail is False, f"{et.value} should not have thumbnail"

    def test_all_configs_have_title_field(self) -> None:
        """Every config should have a non-empty title_field."""
        for et, config in ENTITY_CARD_CONFIG.items():
            assert config.title_field, f"{et.value} has empty title_field"


class TestGetEntityTitle:
    """Tests for get_entity_title function."""

    def test_primary_field(self) -> None:
        """get_entity_title returns the primary field value."""
        entity = SimpleNamespace(caption="Beach Sunset", id="abc-123")
        config = CardConfig(title_field="caption")
        assert get_entity_title(entity, config) == "Beach Sunset"

    def test_fallback_field(self) -> None:
        """get_entity_title falls back when primary is None."""
        entity = SimpleNamespace(caption=None, original_filename="IMG_001.jpg", id="abc")
        config = CardConfig(
            title_field="caption",
            title_fallbacks=["original_filename"],
        )
        assert get_entity_title(entity, config) == "IMG_001.jpg"

    def test_multiple_fallbacks(self) -> None:
        """get_entity_title tries fallbacks in order."""
        entity = SimpleNamespace(
            caption=None, original_filename=None, url="https://example.com", id="abc"
        )
        config = CardConfig(
            title_field="caption",
            title_fallbacks=["original_filename", "url"],
        )
        assert get_entity_title(entity, config) == "https://example.com"

    def test_final_fallback_type_and_id(self) -> None:
        """get_entity_title falls back to type + ID when all fields are None."""
        entity = SimpleNamespace(caption=None, id="12345678-abcd-efgh")
        config = CardConfig(title_field="caption")
        result = get_entity_title(entity, config)
        assert "SimpleNamespace" in result
        assert "12345678" in result

    def test_truncation(self) -> None:
        """get_entity_title truncates long titles."""
        long_text = "A" * 200
        entity = SimpleNamespace(title=long_text, id="abc")
        config = CardConfig(title_field="title")
        result = get_entity_title(entity, config)
        assert len(result) == 120
        assert result.endswith("\u2026")

    def test_empty_string_skips_to_fallback(self) -> None:
        """get_entity_title treats empty string as falsy, uses fallback."""
        entity = SimpleNamespace(caption="", original_filename="photo.jpg", id="abc")
        config = CardConfig(
            title_field="caption",
            title_fallbacks=["original_filename"],
        )
        assert get_entity_title(entity, config) == "photo.jpg"

    def test_with_real_config(self) -> None:
        """get_entity_title works with actual configs from the registry."""
        config = ENTITY_CARD_CONFIG[EntityType.EMAIL]
        entity = SimpleNamespace(subject="Re: Meeting Tomorrow", id="abc")
        assert get_entity_title(entity, config) == "Re: Meeting Tomorrow"

        config = ENTITY_CARD_CONFIG[EntityType.TRANSACTION]
        entity = SimpleNamespace(payee="Coffee Shop", id="abc")
        assert get_entity_title(entity, config) == "Coffee Shop"
