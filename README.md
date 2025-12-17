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

```bash
# Clone the repository
git clone https://github.com/DoubleGremlin181/potluck.git
cd potluck

# Start with Docker Compose
docker compose up -d

# Or install locally with uv
uv sync
```

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

### Celery Worker (for background jobs)

```bash
potluck worker
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
└── scripts/           # Utility scripts
```

## License

MIT License - see [LICENSE](LICENSE) for details.
