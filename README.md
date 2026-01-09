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
- **Face recognition**: Link photos to people via auto-clustering (Google Photos-style)
- **Image captioning**: AI-generated alt-text descriptions for images
- **Multiple embeddings**: Support different embedding types (text, multimodal) per entity
- **Web UI**: View, search, and manage your data via FastAPI + HTMX interface

## Tech Stack

- **Python 3.12+** with FastAPI, SQLModel, Celery
- **PostgreSQL** (Percona flavor) with pgvector and pg_tde encryption
- **sentence-transformers** for text embeddings
- **CLIP** for multimodal embeddings
- **EasyOCR** for image text extraction
- **MTCNN + ArcFace** for face detection and embedding
- **BLIP-2** for AI image captioning
- **Docker Compose** for easy deployment

## Installation

### Quick Install (Recommended)

Install and run Potluck with a single command:

```bash
curl -fsSL https://raw.githubusercontent.com/DoubleGremlin181/potluck/main/scripts/install.sh | bash
```

This will:
- Create `~/.potluck/` with all configuration files
- Generate secure database credentials
- Pull and start all Docker containers
- Run database migrations
- Print MCP config for Claude Desktop

**With GPU support** (requires NVIDIA GPU + nvidia-container-toolkit):
```bash
curl -fsSL https://raw.githubusercontent.com/DoubleGremlin181/potluck/main/scripts/install.sh | bash -s -- --gpu
```

After installation:
- **Web UI**: http://localhost:8000
- **Logs**: `cd ~/.potluck && docker compose logs -f`
- **Stop**: `cd ~/.potluck && docker compose down`

### Development Setup

For contributors who want to modify the code:

```bash
git clone https://github.com/DoubleGremlin181/potluck.git
cd potluck
./scripts/setup.sh        # Start all services
# or
./scripts/setup.sh --db-only  # Start only DB, run app locally with: uv run potluck web
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

All tests run in Docker to ensure consistency between local development and CI.

```bash
# Run all tests
docker compose --profile test run --rm test

# Run specific test file
docker compose --profile test run --rm test uv run pytest tests/unit/models/ -v

# Run with e2e tests (database integration)
docker compose --profile test run --rm test uv run pytest tests/ -v --run-e2e
```

GitHub Actions uses the same configuration with the `test` profile.

The tests verify:
- Docker containers start correctly (Percona PostgreSQL 17 with pgvector + pg_tde)
- PostgreSQL extensions are installed (vector, pg_tde, uuid-ossp)
- All tables are created with pg_tde encryption enabled
- Alembic migrations run successfully
- ML processing (OCR, face detection, captioning) works correctly

## Usage

### MCP Server (for Claude Desktop)

Add to your Claude Desktop config (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "potluck": {
      "command": "docker",
      "args": ["exec", "-i", "potluck-app", "potluck", "mcp"]
    }
  }
}
```

The install script prints this config automatically.

### Web UI

Visit http://localhost:8000 after installation.

## Project Structure

```
potluck/
├── src/potluck/
│   ├── core/          # Config, logging, Celery, exceptions
│   ├── models/        # SQLModel entities
│   ├── pipeline/      # Ingestion + processing pipeline
│   │   ├── ingestion/ # Source-specific data importers
│   │   ├── processing/# OCR, hashing, faces, captioning
│   │   ├── tasks/     # Celery background tasks
│   │   └── utils/     # Archive extraction, parsers
│   ├── embeddings/    # Embedding providers
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
