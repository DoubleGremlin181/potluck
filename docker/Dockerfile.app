FROM python:3.12-slim

WORKDIR /app

# Install system dependencies including OpenCV requirements
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    git \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast package management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy project files (tests excluded by .dockerignore)
COPY pyproject.toml uv.lock README.md ./
COPY src/ src/
COPY alembic/ alembic/
COPY alembic.ini .

# Install ALL dependencies using uv sync
# Use CPU-only PyTorch to reduce image size (~1.5GB vs ~4.5GB)
ENV UV_EXTRA_INDEX_URL=https://download.pytorch.org/whl/cpu
RUN uv sync --all-extras --frozen

# Run migrations and start web server
CMD ["sh", "-c", "uv run alembic upgrade head && uv run potluck web"]
