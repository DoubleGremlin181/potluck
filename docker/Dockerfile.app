FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast package management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy project files
COPY pyproject.toml .
COPY src/ src/
COPY alembic/ alembic/
COPY alembic.ini .
COPY tests/ tests/

# Install ALL dependencies (ml + dev for testing)
# This ensures tests run in the exact same environment as production
RUN uv pip install --system -e ".[all]"

# Run migrations and start web server
CMD ["sh", "-c", "alembic upgrade head && potluck web"]
