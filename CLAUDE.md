# Potluck - AI Context

Privacy-first personal knowledge database exposing data to LLMs via MCP. All processing local.

## Architecture Principles

1. **Source-agnostic entities** - `ChatMessage` works for any platform, `source_type` tracks origin
2. **core/ = infrastructure only** - Domain base classes live with domains (`models/base.py`, `ingesters/base.py`)
3. **Pluggable ingesters** - Each source (Google Takeout, Reddit, etc.) has its own ingester package with auto-detection
4. **Media: paths only, Text: store raw** - No blobs in DB for media; text stored for FTS
5. **Multiple embeddings per entity** - Single table stores different embedding types (text, CLIP, OCR) per entity
6. **Hybrid search** - RRF fusion: 30% FTS + 70% pgvector

## CLI

```bash
potluck mcp      # MCP server (stdio)
potluck web      # Web UI (localhost:8000)
```

## Versioning

**Semantic versioning**: `MAJOR.MINOR.PATCH` (e.g., Phase 1 = `0.1.x`, Phase 12 = `1.0.x`)

- **Version in code**: `pyproject.toml` → `version` field
- **Tags**: `v{version}` format (e.g., `v0.1.0`)

**Workflow**:

1. Commit to `main` with issue references
2. When milestone complete, update `pyproject.toml` version and tag: `git tag v0.1.0`
3. Push tag to trigger GitHub release

**Git hooks** (run `scripts/setup-hooks.sh`):

- `pre-push`: Validates tag format matches semver, checks pyproject.toml version
- `commit-msg`: Warns if commit doesn't reference an issue

## Key Files

| Path | Purpose |
|------|---------|
| `core/config.py` | pydantic-settings |
| `models/base.py` | BaseEntity, TimestampedEntity, GeolocatedEntity |
| `ingesters/base.py` | BaseIngester protocol |
| `search/hybrid.py` | RRF fusion |

## References

- [PRD.md](PRD.md)
- [MCP Docs](https://modelcontextprotocol.io/)
