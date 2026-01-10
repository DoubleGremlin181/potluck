# syntax=docker/dockerfile:1

# Potluck Unified Dockerfile
# Multi-stage build supporting CPU/GPU variants with CI caching
#
# Usage:
#   Local (CPU):     docker build -f docker/Dockerfile -t potluck .
#   Local (GPU):     docker build -f docker/Dockerfile -t potluck --build-arg GPU=true .
#   CI base cache:   docker build -f docker/Dockerfile --target deps -t potluck-base:cpu-<hash> .
#   CI with base:    docker build -f docker/Dockerfile -t potluck --build-arg BASE_IMAGE=ghcr.io/... .
#
# The 'deps' target can be cached in GHCR and reused across CI runs.
# Hash is computed from pyproject.toml + uv.lock.

ARG GPU=false
ARG BASE_IMAGE=""

# ============================================================================
# Stage 1: System dependencies
# ============================================================================
FROM python:3.12-slim AS base

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

# Copy only dependency files (changes trigger rebuild)
COPY pyproject.toml uv.lock README.md ./

# Create empty src structure for uv sync (needed for editable install)
RUN mkdir -p src/potluck && touch src/potluck/__init__.py

# ============================================================================
# Stage 2a: CPU dependencies (~1.5GB)
# Install torch from CPU index to avoid CUDA bloat
# ============================================================================
FROM base AS cpu-deps

# 1. Create venv and sync deps excluding torch/nvidia packages
# 2. Install CPU torch separately to avoid CUDA version from lockfile
RUN uv venv && \
    uv sync --all-extras --frozen \
        --no-install-package torch \
        --no-install-package torchvision \
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
        --no-install-package triton && \
    uv pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu && \
    rm -rf /root/.cache/uv /root/.cache/pip

# ============================================================================
# Stage 2b: GPU dependencies (~4.5GB, CUDA 12.4)
# ============================================================================
FROM base AS gpu-deps

# Create venv, install CUDA torch first, then sync remaining deps
RUN uv venv && \
    uv pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124 && \
    uv sync --all-extras --frozen && \
    rm -rf /root/.cache/uv /root/.cache/pip

# ============================================================================
# Stage aliases for GPU selection
# Maps GPU=false -> cpu-deps, GPU=true -> gpu-deps
# ============================================================================
FROM cpu-deps AS deps-false
FROM gpu-deps AS deps-true

# ============================================================================
# Deps target: Final dependencies stage (used for CI base image caching)
# CI builds this target with --target deps and pushes to GHCR
# ============================================================================
ARG GPU=false
FROM deps-${GPU} AS deps

# Verify PyTorch installation
RUN uv run python -c "import torch; print(f'PyTorch {torch.__version__}, CUDA available: {torch.cuda.is_available()}')"

# ============================================================================
# App target: Select base and add application code
# When BASE_IMAGE is provided (CI): use cached deps from GHCR
# When BASE_IMAGE is empty (local): use freshly built deps-${GPU}
# ============================================================================
ARG GPU=false
ARG BASE_IMAGE=""
FROM ${BASE_IMAGE:-deps-${GPU}} AS app

WORKDIR /app

# Copy application code (this is the fast part - only code changes)
COPY src/ src/
COPY alembic/ alembic/
COPY alembic.ini .

# Run migrations, download models (cached in volume), and start web server
CMD ["sh", "-c", "uv run alembic upgrade head && uv run potluck download-models && uv run potluck web"]
