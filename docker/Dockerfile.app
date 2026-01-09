# syntax=docker/dockerfile:1

# Potluck Application Image
# Uses pre-built base image from GHCR for fast CI builds
# Falls back to building dependencies locally if base image unavailable
#
# Usage:
#   Local (CPU):     docker build -f docker/Dockerfile.app -t potluck .
#   Local (GPU):     docker build -f docker/Dockerfile.app -t potluck --build-arg GPU=true .
#   CI (with base):  docker build -f docker/Dockerfile.app -t potluck --build-arg BASE_IMAGE=ghcr.io/... .

ARG GPU=false
ARG BASE_IMAGE=""

# ============================================================================
# Fallback: Build base locally if no BASE_IMAGE provided
# ============================================================================
FROM python:3.12-slim AS local-base

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    git \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock README.md ./

RUN mkdir -p src/potluck && touch src/potluck/__init__.py

# ============================================================================
# Local CPU build - install torch FIRST from CPU index, then sync remaining deps
# ============================================================================
FROM local-base AS local-cpu-deps
# Install torch from CPU index first (before uv sync downloads default CUDA version)
RUN uv venv && uv pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
# Now sync remaining deps, skipping nvidia/triton packages (CUDA-only, not needed for CPU torch)
RUN uv sync --all-extras --frozen \
    --no-install-package nvidia-cublas-cu12 \
    --no-install-package nvidia-cuda-cupti-cu12 \
    --no-install-package nvidia-cuda-nvrtc-cu12 \
    --no-install-package nvidia-cuda-runtime-cu12 \
    --no-install-package nvidia-cudnn-cu12 \
    --no-install-package nvidia-cufft-cu12 \
    --no-install-package nvidia-curand-cu12 \
    --no-install-package nvidia-cusolver-cu12 \
    --no-install-package nvidia-cusparse-cu12 \
    --no-install-package nvidia-cusparselt-cu12 \
    --no-install-package nvidia-nccl-cu12 \
    --no-install-package nvidia-nvjitlink-cu12 \
    --no-install-package nvidia-nvtx-cu12 \
    --no-install-package nvidia-cufile-cu12 \
    --no-install-package nvidia-nvshmem-cu12 \
    --no-install-package triton
# Clean up cache to minimize image size
RUN rm -rf /root/.cache/uv /root/.cache/pip

# ============================================================================
# Local GPU build - install torch FIRST from CUDA index, then sync remaining deps
# ============================================================================
FROM local-base AS local-gpu-deps
# Install torch with CUDA 12.4 support first
RUN uv venv && uv pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124
# Now sync remaining deps - torch is already satisfied
RUN uv sync --all-extras --frozen
# Clean up cache
RUN rm -rf /root/.cache/uv /root/.cache/pip

# ============================================================================
# Stage aliases for GPU selection
# Maps GPU=false -> cpu-deps, GPU=true -> gpu-deps
# ============================================================================
FROM local-cpu-deps AS local-deps-false
FROM local-gpu-deps AS local-deps-true

# ============================================================================
# Select base: Use provided BASE_IMAGE or fall back to local build
# When BASE_IMAGE is provided (CI), use it directly
# When BASE_IMAGE is empty (local), use local-deps-{false,true} based on GPU arg
# ============================================================================
ARG GPU=false
ARG BASE_IMAGE=""
FROM ${BASE_IMAGE:-local-deps-${GPU}} AS deps

# ============================================================================
# Final application stage
# ============================================================================
FROM deps AS app

WORKDIR /app

# Copy application code (this is the fast part - only code changes)
COPY src/ src/
COPY alembic/ alembic/
COPY alembic.ini .

# Run migrations and start web server
CMD ["sh", "-c", "uv run alembic upgrade head && uv run potluck web"]
