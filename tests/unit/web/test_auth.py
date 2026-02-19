"""Tests for authentication."""

import pytest
from itsdangerous import BadSignature, URLSafeTimedSerializer

from potluck.core.config import get_settings
from potluck.web.dependencies import SESSION_MAX_AGE


class TestAuthCookies:
    """Test signed cookie creation and validation."""

    def test_create_and_validate_token(self) -> None:
        """A token created by the serializer should validate successfully."""
        settings = get_settings()
        serializer = URLSafeTimedSerializer(settings.web_secret_key)

        token = serializer.dumps("authenticated")
        assert isinstance(token, str)

        data = serializer.loads(token, max_age=SESSION_MAX_AGE)
        assert data == "authenticated"

    def test_invalid_token_raises(self) -> None:
        """An invalid token should raise BadSignature."""
        settings = get_settings()
        serializer = URLSafeTimedSerializer(settings.web_secret_key)

        with pytest.raises(BadSignature):
            serializer.loads("invalid-token", max_age=SESSION_MAX_AGE)

    def test_different_secret_rejects(self) -> None:
        """A token signed with a different key should be rejected."""
        serializer1 = URLSafeTimedSerializer("secret-1")
        serializer2 = URLSafeTimedSerializer("secret-2")

        token = serializer1.dumps("authenticated")

        with pytest.raises(BadSignature):
            serializer2.loads(token, max_age=SESSION_MAX_AGE)
