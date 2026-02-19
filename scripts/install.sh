#!/bin/bash
# Potluck Install Script
# One-liner installation for end-users
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/DoubleGremlin181/potluck/main/scripts/install.sh | bash
#   curl -fsSL https://raw.githubusercontent.com/DoubleGremlin181/potluck/main/scripts/install.sh | bash -s -- --gpu
#
# Options:
#   --gpu    Enable GPU support (uses pre-built GPU image from GHCR, requires NVIDIA GPU + nvidia-container-toolkit)

set -e

POTLUCK_DIR="${POTLUCK_DIR:-$HOME/.potluck}"
REPO_URL="https://raw.githubusercontent.com/DoubleGremlin181/potluck/main"
GPU=false

# Parse arguments
for arg in "$@"; do
    case $arg in
        --gpu)
            GPU=true
            ;;
    esac
done

echo "🍲 Installing Potluck..."
echo ""

# Check for required tools
if ! command -v docker &> /dev/null; then
    echo "❌ Docker is required but not installed."
    echo "   Install Docker: https://docs.docker.com/get-docker/"
    exit 1
fi

if ! docker compose version &> /dev/null; then
    echo "❌ Docker Compose is required but not installed."
    echo "   Docker Compose is included with Docker Desktop, or install separately."
    exit 1
fi

# Create Potluck directory
echo "📁 Creating Potluck directory at $POTLUCK_DIR..."
mkdir -p "$POTLUCK_DIR"
cd "$POTLUCK_DIR"

# Download required files
echo "📥 Downloading configuration files..."
curl -fsSL "$REPO_URL/docker-compose.yml" -o docker-compose.yml
curl -fsSL "$REPO_URL/scripts/init-db.sql" -o init-db.sql
curl -fsSL "$REPO_URL/.env.example" -o .env

# Generate secure credentials and update .env
echo "🔐 Generating secure credentials..."
DB_PASSWORD=$(openssl rand -base64 32 | tr -dc 'a-zA-Z0-9' | head -c 32)
WEB_SECRET=$(openssl rand -base64 32 | tr -dc 'a-zA-Z0-9' | head -c 32)
sed -i "s/POSTGRES_PASSWORD=changeme_in_production/POSTGRES_PASSWORD=$DB_PASSWORD/" .env
sed -i "s/:changeme_in_production@/:$DB_PASSWORD@/g" .env
sed -i "s/# WEB_SECRET_KEY=change-me-in-production/WEB_SECRET_KEY=$WEB_SECRET/" .env

# Set production profile (uses pre-built GHCR images)
echo "COMPOSE_PROFILES=prod" >> .env

# Set GPU mode if requested (use GPU image tag from GHCR)
if [ "$GPU" = true ]; then
    echo "🎮 GPU support enabled (using pre-built GPU image from GHCR)"
    sed -i "s/GPU=false/GPU=true/" .env
    # Use GPU image tag instead of 'latest'
    echo "POTLUCK_VERSION=gpu" >> .env
fi

# Pull images
echo "🐳 Pulling Docker images (this may take a few minutes)..."
docker compose pull

# Start services
echo "🚀 Starting Potluck services..."
docker compose up -d

# Wait for database
echo "⏳ Waiting for database to be ready..."
timeout=60
counter=0
until docker compose exec -T db-prod pg_isready -U potluck -d potluck &> /dev/null; do
    counter=$((counter + 1))
    if [ $counter -ge $timeout ]; then
        echo "❌ Database did not become ready in time"
        docker compose logs db-prod
        exit 1
    fi
    sleep 1
done

# Wait for encryption setup
echo "⏳ Waiting for encryption setup..."
sleep 5  # Give pg_tde time to initialize

# Run migrations
echo "🔄 Running database migrations..."
docker compose exec -T app-prod uv run alembic upgrade head

echo ""
echo "✅ Potluck is ready!"
echo ""
echo "📊 Web UI: http://localhost:8000"
echo "📁 Data directory: $POTLUCK_DIR"
echo ""
echo "🔧 Useful commands:"
echo "   cd $POTLUCK_DIR && docker compose logs -f    # View logs"
echo "   cd $POTLUCK_DIR && docker compose down       # Stop Potluck"
echo "   cd $POTLUCK_DIR && docker compose up -d      # Start Potluck"
echo ""
echo "🤖 Add to Claude Desktop (claude_desktop_config.json):"
echo ""
cat << 'MCPCONFIG'
{
  "mcpServers": {
    "potluck": {
      "command": "docker",
      "args": ["exec", "-i", "potluck-app", "potluck", "mcp"]
    }
  }
}
MCPCONFIG
echo ""
echo "⚠️  NOTE: Using file-based encryption keys (development mode)"
echo "   For production, configure HashiCorp Vault. See documentation."
echo ""
