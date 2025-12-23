#!/bin/bash
# Potluck Setup Script
# Usage: ./scripts/setup.sh [--db-only] [--non-interactive]
#
# Options:
#   --db-only          Only start the database (for testing/development)
#   --non-interactive  Skip all prompts and use defaults

set -e

DB_ONLY=false
INTERACTIVE=true

for arg in "$@"; do
    case $arg in
        --db-only)
            DB_ONLY=true
            ;;
        --non-interactive)
            INTERACTIVE=false
            ;;
    esac
done

# Disable interactive mode if stdin is not a terminal
if [ ! -t 0 ]; then
    INTERACTIVE=false
fi

echo "🍲 Setting up Potluck..."

# Check for required tools
command -v docker >/dev/null 2>&1 || { echo "❌ Docker is required but not installed. Aborting."; exit 1; }
command -v docker compose >/dev/null 2>&1 || { echo "❌ Docker Compose is required but not installed. Aborting."; exit 1; }

# Create .env file if it doesn't exist
if [ ! -f .env ]; then
    echo "📝 Creating .env file..."
    cp .env.example .env

    # Interactive configuration
    if [ "$INTERACTIVE" = true ]; then
        echo ""
        echo "Configure your Potluck instance (press Enter to use defaults):"
        echo ""

        # Database password
        read -p "PostgreSQL password [changeme_in_production]: " DB_PASSWORD
        if [ -n "$DB_PASSWORD" ]; then
            sed -i "s/POSTGRES_PASSWORD=changeme_in_production/POSTGRES_PASSWORD=$DB_PASSWORD/" .env
            sed -i "s/:changeme_in_production@/:$DB_PASSWORD@/g" .env
        fi

        # Web port
        read -p "Web UI port [8000]: " WEB_PORT
        if [ -n "$WEB_PORT" ]; then
            sed -i "s/WEB_PORT=8000/WEB_PORT=$WEB_PORT/" .env
        fi

        # Database port
        read -p "PostgreSQL port [5432]: " DB_PORT
        if [ -n "$DB_PORT" ]; then
            sed -i "s/POSTGRES_PORT=5432/POSTGRES_PORT=$DB_PORT/" .env
        fi

        echo ""
    else
        echo "⚠️  Using default configuration. Review .env for production use."
    fi
fi

# Load environment variables
set -a
source .env
set +a

# Start services
echo "🐳 Starting Docker services..."
if [ "$DB_ONLY" = true ]; then
    docker compose up -d db redis
else
    docker compose up -d
fi

# Wait for database to be healthy (including init scripts)
echo "⏳ Waiting for database to be ready..."
timeout=120
counter=0

# First wait for pg_isready
until docker compose exec -T db pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null 2>&1; do
    counter=$((counter + 1))
    if [ $counter -ge $timeout ]; then
        echo "❌ Database did not become ready in time"
        docker compose logs db
        exit 1
    fi
    sleep 1
done

# Then wait for init scripts to complete by checking if pg_tde key is set
echo "⏳ Waiting for encryption setup to complete..."
until docker compose exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT pg_tde_is_encrypted('pg_catalog.pg_class');" >/dev/null 2>&1; do
    counter=$((counter + 1))
    if [ $counter -ge $timeout ]; then
        echo "❌ Database encryption setup did not complete in time"
        docker compose logs db
        exit 1
    fi
    sleep 1
done
echo "✅ Database is ready"

# Run migrations
echo "🔄 Running database migrations..."
if [ "$DB_ONLY" = true ]; then
    # For db-only mode, run migrations locally with uv
    if command -v uv >/dev/null 2>&1; then
        export SYNC_DATABASE_URL="postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@localhost:${POSTGRES_PORT:-5432}/${POSTGRES_DB}"
        uv run alembic upgrade head
    else
        echo "⚠️  uv not installed, skipping migrations. Run manually: alembic upgrade head"
    fi
else
    # For full mode, run via app container
    docker compose exec -T app alembic upgrade head
fi

echo ""
echo "✅ Potluck is ready!"
echo ""
if [ "$DB_ONLY" = true ]; then
    echo "🗄️  Database running at localhost:${POSTGRES_PORT:-5432}"
    echo "🔧 To view logs: docker compose logs -f db"
else
    echo "📊 Web UI: http://localhost:${WEB_PORT:-8000}"
    echo "🔧 To view logs: docker compose logs -f"
fi
echo "🛑 To stop: docker compose down"

# Warn about file-based encryption keys
echo ""
echo "⚠️  NOTE: Using file-based encryption keys (development mode)"
echo "   For production, configure HashiCorp Vault. See README.md"
echo ""
