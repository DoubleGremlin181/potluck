# Potluck

**Personal Knowledge Database MCP Server** - Personalize your AI assistant with Google Takeout, GDPR exports, and more.

> [!WARNING]
> This project is under active development. Not ready for production use.

## Overview

Potluck is a privacy-first personal knowledge management system that:

- **Ingests** data from Google Takeout, Reddit GDPR exports, WhatsApp, YNAB, and more
- **Deduplicates** content using perceptual hashing for media and content hashing for text
- **Links** entities across sources via temporal, spatial, and semantic relationships
- **Exposes** your data to LLMs via the Model Context Protocol (MCP)
- **Keeps** all data local - no external API calls for embeddings or processing

## Features

- **Source-agnostic entities**: A chat message model works for WhatsApp, Telegram, SMS, etc.
- **Hybrid search**: Combines PostgreSQL full-text search with pgvector similarity
- **Face recognition**: Link photos to people automatically
- **Multiple embeddings**: Support different embedding types (text, multimodal) per entity
- **Web UI**: View, search, and manage your data via FastAPI + HTMX interface

## Tech Stack

- **Python 3.12+** with FastAPI, SQLModel, Celery
- **PostgreSQL** (Percona flavor) with pgvector and pg_tde encryption
- **sentence-transformers** for text embeddings
- **CLIP** for multimodal embeddings
- **EasyOCR** for image text extraction
- **Docker Compose** for easy deployment

## Installation

### Quick Start (Docker)

```bash
# Clone the repository
git clone https://github.com/DoubleGremlin181/potluck.git
cd potluck

# Run the setup script (creates .env, starts services, runs migrations)
./scripts/setup.sh
```

The setup script will:

1. Prompt for configuration (password, ports) or use defaults if skipped
2. Create `.env` from `.env.example`
3. Start PostgreSQL (with pgvector and pg_tde encryption), Redis, and the app via Docker Compose
4. Wait for the database to be ready
5. Run Alembic migrations to create all tables with encryption enabled

Options:
- `--db-only` - Only start the database (useful for development/testing)
- `--non-interactive` - Skip prompts and use default values

### Manual Setup

```bash
# 1. Copy environment file and configure
cp .env.example .env
# Edit .env to set POSTGRES_PASSWORD

# 2. Start services
docker compose up -d

# 3. Run migrations (after db is healthy)
docker compose exec app alembic upgrade head
```

### Local Development (without Docker)

```bash
# Install dependencies with uv
uv sync

# Start database only (requires Docker)
./scripts/setup.sh --db-only

# Run migrations against local database
alembic upgrade head
```

## Encryption Key Management

Potluck uses [pg_tde](https://docs.percona.com/pg-tde/) for transparent data encryption. All database tables are encrypted at rest.

### Development (File-Based Keys)

By default, encryption keys are stored in a local file. This is **suitable for development only**:

```bash
# Default setup uses file-based keys
./scripts/setup.sh
```

You'll see a warning:
```
⚠️  SECURITY WARNING: Using file-based encryption keys
```

### Production (HashiCorp Vault)

For production, configure [HashiCorp Vault](https://www.vaultproject.io/) as the key provider:

1. **Set up Vault** with a KV v2 secrets engine:
   ```bash
   # Enable KV v2 secrets engine (if not already enabled)
   vault secrets enable -path=secret kv-v2
   ```

2. **Configure Potluck** with Vault credentials:
   ```bash
   # Option 1: Interactive setup
   ./scripts/setup.sh
   # When prompted, enter your Vault URL and token

   # Option 2: Manual configuration in .env
   VAULT_URL=https://vault.example.com:8200
   VAULT_TOKEN=your-vault-token
   VAULT_MOUNT=secret
   ```

3. **Start fresh** (required when switching key providers):
   ```bash
   docker compose down -v
   ./scripts/setup.sh
   ```

### Other Key Management Systems

pg_tde also supports KMIP-compatible KMS providers (AWS KMS, Azure Key Vault, etc.). See the [pg_tde documentation](https://docs.percona.com/pg-tde/global-key-provider-configuration/index.html) for configuration details.

## Testing

```bash
# Install dev dependencies
uv sync

# Run unit tests (no Docker required)
uv run pytest tests/ -v

# Run end-to-end tests (requires Docker)
uv run pytest tests/integration/ -v --run-e2e
```

The E2E tests verify:
- Docker containers start correctly (Percona PostgreSQL 17 with pgvector + pg_tde)
- PostgreSQL extensions are installed (vector, pg_tde, uuid-ossp)
- All tables are created with pg_tde encryption enabled
- Alembic migrations run successfully

## Usage

### MCP Server (for Claude Desktop)

Add to your Claude Desktop config:

```json
{
  "mcpServers": {
    "potluck": {
      "command": "potluck",
      "args": ["mcp"]
    }
  }
}
```

### Web UI

```bash
potluck web
# Visit http://localhost:8000
```

## Project Structure

```
potluck/
├── src/potluck/
│   ├── core/          # Config, logging, Celery, exceptions
│   ├── models/        # SQLModel entities
│   ├── ingesters/     # Source-specific data importers
│   ├── embeddings/    # Embedding providers
│   ├── processing/    # OCR, hashing, face detection
│   ├── search/        # Hybrid search implementation
│   ├── linkers/       # Entity relationship detection
│   ├── mcp/           # MCP server and tools
│   ├── web/           # FastAPI + HTMX web UI
│   └── db/            # Database session management
├── alembic/           # Database migrations
├── tests/             # Unit and integration tests
├── scripts/           # Setup and utility scripts
└── docker/            # Dockerfiles for app and database
```

## License

MIT License - see [LICENSE](LICENSE) for details.
