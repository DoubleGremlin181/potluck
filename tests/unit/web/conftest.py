"""Shared fixtures for web tests."""

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from itsdangerous import URLSafeTimedSerializer
from sqlalchemy.ext.asyncio import AsyncSession

from potluck.core.config import get_settings
from potluck.web.app import create_app
from potluck.web.dependencies import get_db


@pytest.fixture
def mock_db() -> AsyncMock:
    """Create a mock async database session."""
    session = AsyncMock(spec=AsyncSession)
    # Default: execute returns empty result
    result = MagicMock()
    result.scalar.return_value = 0
    result.scalar_one_or_none.return_value = None
    result.scalars.return_value.all.return_value = []
    result.scalars.return_value.unique.return_value.all.return_value = []
    session.execute.return_value = result
    return session


@pytest.fixture
def app(mock_db: AsyncMock) -> FastAPI:
    """Create a FastAPI app with mocked database."""
    test_app = create_app()

    async def override_get_db() -> AsyncGenerator[AsyncMock, None]:
        yield mock_db

    test_app.dependency_overrides[get_db] = override_get_db
    return test_app


@pytest.fixture
async def client(app: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    """Create an async HTTP test client."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac


@pytest.fixture
def auth_cookie() -> dict[str, str]:
    """Create a valid authentication cookie."""
    settings = get_settings()
    serializer = URLSafeTimedSerializer(settings.web_secret_key)
    token = serializer.dumps("authenticated")
    return {"session_token": token}


@pytest.fixture
async def authed_client(
    app: FastAPI,
    auth_cookie: dict[str, str],
) -> AsyncGenerator[AsyncClient, None]:
    """Create an async HTTP test client with auth cookie."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies=auth_cookie,
    ) as ac:
        yield ac
