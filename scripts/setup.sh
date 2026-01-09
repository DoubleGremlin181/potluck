#!/bin/bash
# Potluck Development Setup Script
# For contributors who have cloned the repository
#
# Usage: ./scripts/setup.sh [--db-only] [--gpu]
#
# Options:
#   --db-only  Only start the database (for local development with uv run)
#   --gpu      Enable GPU support (requires NVIDIA GPU + nvidia-container-toolkit)
#
# For end-users (without git clone), use install.sh instead:
#   curl -fsSL https://raw.githubusercontent.com/DoubleGremlin181/potluck/main/scripts/install.sh | bash

set -e

DB_ONLY=false
GPU=false

for arg in "$@"; do
    case $arg in
        --db-only)
            DB_ONLY=true
            ;;
        --gpu)
            GPU=true
            ;;
    esac
done

echo "🍲 Setting up Potluck for development..."

# Setup git hooks (contributors only)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$SCRIPT_DIR/setup-hooks.sh" ]; then
    "$SCRIPT_DIR/setup-hooks.sh"
fi

# Check for required tools
command -v docker >/dev/null 2>&1 || { echo "❌ Docker is required. Install: https://docs.docker.com/get-docker/"; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "❌ Docker Compose is required."; exit 1; }

# Create .env if missing
if [ ! -f .env ]; then
    echo "📝 Creating .env from .env.example..."
    cp .env.example .env
    echo "   Edit .env to change password/ports before production use."
fi

# Load environment
set -a
source .env
set +a

# Start services
echo "🐳 Starting Docker services..."
if [ "$DB_ONLY" = true ]; then
    docker compose up -d db redis
else
    if [ "$GPU" = true ]; then
        echo "🎮 GPU support enabled (CUDA PyTorch, ~4.5GB image)"
        GPU=true docker compose build
    fi
    docker compose up -d
fi

# Wait for database
echo "⏳ Waiting for database..."
timeout=60
counter=0
until docker compose exec -T db pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null 2>&1; do
    counter=$((counter + 1))
    if [ $counter -ge $timeout ]; then
        echo "❌ Database timeout"; docker compose logs db; exit 1
    fi
    sleep 1
done

# Wait for encryption setup
until docker compose exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT 1" >/dev/null 2>&1; do
    sleep 1
done
echo "✅ Database ready"

# Run migrations
echo "🔄 Running migrations..."
if [ "$DB_ONLY" = true ]; then
    if command -v uv >/dev/null 2>&1; then
        SYNC_DATABASE_URL="postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@localhost:${POSTGRES_PORT:-5432}/${POSTGRES_DB}" \
            uv run alembic upgrade head
    else
        echo "⚠️  uv not installed. Run: uv run alembic upgrade head"
    fi
else
    docker compose exec -T app uv run alembic upgrade head
fi

echo ""
echo "✅ Potluck is ready!"
if [ "$DB_ONLY" = true ]; then
    echo "🗄️  Database: localhost:${POSTGRES_PORT:-5432}"
    echo "🚀 Start app: uv run potluck web"
else
    echo "📊 Web UI: http://localhost:${WEB_PORT:-8000}"
fi
echo "🛑 Stop: docker compose down"
echo ""
echo "⚠️  File-based encryption (dev only). Use Vault for production."
