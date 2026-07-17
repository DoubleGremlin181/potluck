# Potluck

**Privacy-first personal knowledge database for your AI** — local-first, MCP-native.

Potluck ingests your data exports — Google Takeout (Gmail, Keep, Calendar, Chat, Chrome
history, Photos metadata, Timeline), WhatsApp, Reddit, YNAB, plus generic folders of
images, notes, and mbox files — stores everything in one local SQLite database, and
exposes it three ways: a web app, a CLI, and
[MCP](https://modelcontextprotocol.io/) for AI assistants. Watched folders and scheduled
Google Drive pulls keep it current automatically. No cloud, no telemetry: nothing leaves
your machine.

## Status — v1 rewrite in progress

| Phase | Shippable increment | Status |
|---|---|---|
| P0 Reset & Walking Skeleton | `potluck serve` zero-config: SPA shell; CLI/API/MCP answer stats from one service layer; bench rig + CI | ✅ `v1.0.0-alpha.1` |
| P1 Storage Core & First Ingest | Google Keep from a Takeout archive (zip/tgz/dir); FTS search via CLI + MCP | ✅ `v1.0.0-alpha.2` |
| P2 Gmail at Scale & Search v1 | Multi-GB mbox ingested incrementally; filtered/snippeted search | ✅ `v1.0.0-alpha.3` |
| P3 MVP Interfaces | Real search/item/imports UI, MCP toolset v1 — **beta.1 = MVP** | ✅ `v1.0.0-beta.1` |
| P4 Source Expansion & Automation | Remaining planned sources, watch-folder, scheduled GDrive pull | — |
| P5 Semantic Search | Unified embedding space, HNSW index, hybrid RRF | — |
| P6 Vision & Media Enrichment | OCR, image embeddings, faces, media gallery | — |
| P7 Linkers, People & Timeline | Related-items everywhere, people review, timeline | — |
| P8 Hardening & 1.0 | Docs, backup/restore, doctor, perf sweep | — |

Full plan, architecture, and locked decisions: pinned
[issue #98](https://github.com/DoubleGremlin181/potluck/issues/98). v0 is archived at
[`archive/v0`](https://github.com/DoubleGremlin181/potluck/tree/archive/v0).

## Quickstart — 60 seconds to running

Requires [uv](https://docs.astral.sh/uv/) (Python is fetched automatically).

**1 — Start the server.** Straight from GitHub:

```bash
uvx --from git+https://github.com/DoubleGremlin181/potluck potluck serve
```

CLI, API, and MCP are fully functional in this form; only the web app is not
bundled (the page at `/` says so and points at the API docs). Release wheels
ship with the web app embedded:

```bash
uvx --from https://github.com/DoubleGremlin181/potluck/releases/download/v1.0.0-beta.1/potluck-1.0.0b1-py3-none-any.whl potluck serve
```

Or Docker (web app included; data persists in the `potluck-data` volume):

```bash
docker run -p 127.0.0.1:8765:8765 -v potluck-data:/data ghcr.io/doublegremlin181/potluck:1.0.0-beta.1
```

**2 — Import your data.** Feed it a [Google Takeout](https://takeout.google.com/)
export, a WhatsApp/Reddit/YNAB export, a bare Timeline.json, or any folder of
notes/images/mbox files (zip, tgz, unpacked dir, or single file) — reuse the same
`uvx --from … potluck` prefix for every command, or upload from the web app's
Imports page. Drop exports into a watched folder (see `watch_folders` in the config)
or set up [scheduled Google Drive pulls](docs/gdrive-setup.md) to automate it:

```bash
uvx --from git+https://github.com/DoubleGremlin181/potluck potluck import ~/Downloads/takeout-20260707T120000Z-001.zip
```

**3 — Search it.**

```bash
uvx --from git+https://github.com/DoubleGremlin181/potluck potluck search "garden fence"
```

The web app lives at <http://127.0.0.1:8765> — `potluck serve` opens it for
you. Search and browse everything, start imports, and watch their progress
live.

> [!WARNING]
> Potluck is localhost-only by design — there is no authentication in v1. Do not expose it
> to other machines or the internet.

## MCP for AI assistants

stdio (Claude Desktop, Claude Code, …):

```json
{
  "mcpServers": {
    "potluck": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/DoubleGremlin181/potluck", "potluck", "mcp"]
    }
  }
}
```

Streamable HTTP instead: `potluck serve` exposes the same tools at
`http://127.0.0.1:8765/mcp` — one server surface for web app, API and MCP.

Toolset: `search` (keyword search with ranked, snippeted hits), `list_items`
(browse/filter without a query), `get_item` (full content by id), `get_thread`
(the whole email conversation around an item), `get_stats` (database
overview), `list_sources` (what this build can ingest).

**Setup guides — Claude Desktop, Claude Code, OpenClaw, Hermes, direct REST
without MCP, and troubleshooting: [docs/ai-integration.md](docs/ai-integration.md).**

## CLI

```text
potluck import PATH  ingest an export (zip/tgz/dir/single file; source auto-detected)
potluck search Q     full-text search (--kind, --prefix, --cursor, --limit, --json)
potluck list         browse items without a query (--kind, --source, --since, --sort, --json)
potluck show ID      full item content + metadata (--thread: the whole conversation)
potluck status       database overview + per-import stats
potluck rm           delete items by id, --import run, or --source (confirms; --yes to skip)
potluck forget       rm + block the deleted content from ever re-importing
potluck gdrive       Google Drive Takeout auto-pull (auth / status; docs/gdrive-setup.md)
potluck serve        web app + API + MCP (/mcp) on one port (opens your browser)
potluck mcp          MCP server on stdio (HTTP lives at /mcp on the serve port)
potluck bench run    benchmark harness (smoke/full tiers)
potluck dev          source-plugin scaffolding (new-source / check-source)
```

Search queries can carry inline operators, combinable with each other and
free text: `from:alice@example.com` (or a name prefix: `from:alice`),
`source:gmail`, `kind:email`, `after:2024-01-01` (inclusive),
`before:2025-06-30` (exclusive), with quoted values supported. Source values
are case-insensitive and spaces map to underscores, so `source:"google keep"`
≡ `source:google_keep`. Invalid operator values are ignored — the response's
`warnings` list says what was dropped — and unknown `key:value` pairs are
searched as plain text. Operators alone (no free text) list the matching
items newest-first.

Search-as-you-type: `--prefix` (CLI) / `prefix=true` (MCP) matches the last
word as a prefix (`gar` finds garden/garlic/garnet). Pagination uses opaque
keyset cursors — pass a response's `next_cursor` back as `cursor` for the
next page; the result set is frozen at the first page, so items ingested
mid-pagination never shift, repeat, or hide existing hits.

Configuration is optional: defaults work out of the box. Override via `POTLUCK_*` env vars
or `~/.config/potluck/config.toml` (env > toml > defaults); the database lives at
`~/.local/share/potluck/potluck.db` by default.

## Development

```bash
git clone https://github.com/DoubleGremlin181/potluck && cd potluck
uv sync && uv run pre-commit install
(cd web && npm ci && npm run build)
uv run potluck serve
```

Tests: `uv run pytest` (unit tier), `-m browser` for the Playwright smoke. Conventions live
in [CLAUDE.md](CLAUDE.md); test patterns in [tests/README.md](tests/README.md).

## License

[MIT](LICENSE)
