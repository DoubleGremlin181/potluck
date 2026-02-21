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
- **Hybrid search**: Combines PostgreSQL full-text search (keywords) with pgvector similarity (semantics)
- **Face recognition**: Link photos to people via auto-clustering (Google Photos-style)
- **Image captioning**: AI-generated alt-text descriptions for images
- **Multiple embeddings**: Support different embedding types (text, multimodal) per entity
- **Web UI**: View, search, and manage your data via FastAPI + HTMX interface

## Tech Stack

- **Python 3.12+** with FastAPI, SQLModel, Celery
- **PostgreSQL** (Percona flavor) with pgvector and pg_tde encryption
- **e5-small-v2** for text embeddings (via sentence-transformers)
- **SigLIP** for multimodal embeddings (text-to-image search)
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

## Encryption

All database tables are encrypted at rest via [pg_tde](https://docs.percona.com/pg-tde/). Development uses file-based keys (automatic). For production with HashiCorp Vault, see [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## Testing

```bash
# Run all tests
docker compose --profile test run --rm test

# Run with e2e tests (database integration)
docker compose --profile test run --rm test uv run pytest tests/ -v --run-e2e
```

See [tests/README.md](tests/README.md) for the full marker system, fixture hierarchy, and CI details.

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

## Documentation

- [Architecture](docs/ARCHITECTURE.md) -- System architecture, data flow, and design decisions
- [Issues](docs/ISSUES.md) -- Known issues and limitations
- [Deployment](docs/DEPLOYMENT.md) -- Encryption, production setup, key management
- [Releasing](docs/RELEASING.md) -- Version bumps, tagging, CI publishing
- [PRD](docs/PRD.md) -- Product requirements document

## License

MIT License - see [LICENSE](LICENSE) for details.
