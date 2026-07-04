# Connecting AI assistants to Potluck

Potluck is MCP-native: any [Model Context Protocol](https://modelcontextprotocol.io/)
client can search and read your knowledge base. One toolset, two transports:

| Transport | Command | Who it's for |
|---|---|---|
| **stdio** | `potluck mcp` | Clients that launch the server themselves (Claude Desktop, Claude Code, and most local assistants). Zero setup — the client spawns the process on demand. |
| **Streamable HTTP** | `potluck serve` → `http://127.0.0.1:8765/mcp` | One long-running server shared by the web app, the REST API, and every HTTP-capable MCP client. Requires `potluck serve` to be running. |

Both transports serve the same tools from the same database. Assistants
without MCP support can use the [REST API directly](#direct-rest-no-mcp).

> [!WARNING]
> Potluck has **no authentication in v1** and binds to `127.0.0.1` by design.
> Never expose the serve port (or a tunnel to it) to other machines or the
> internet. There are no tokens to configure — leave any auth fields in your
> client empty.

All examples below use synthetic data (`@potluck.test` addresses); substitute
your own paths where marked.

## What your assistant gets

| Tool | What it does |
|---|---|
| `search` | Keyword search with ranked, `[match]`-bracketed snippets. Inline operators combine with free text: `from:alice@potluck.test` (or a name prefix `from:alice`), `source:gmail`, `kind:email`, `after:2024-01-01` (inclusive), `before:2025-06-30` (exclusive); quote values with spaces (`source:"google keep"`). Invalid operator values are ignored, never errors — the response's `warnings` list says what was dropped. |
| `list_items` | Browse without keywords: recent items, date ranges, per-kind/source inventories. |
| `get_item` | Full content + metadata for one item id (search snippets are truncated; this isn't). |
| `get_thread` | The whole email conversation around an item, oldest-first, with reply links. |
| `get_stats` | Database overview: item/source/import counts, DB location and size. |
| `list_sources` | What this build can ingest (and the valid values for `source:`). |

Pagination is by opaque keyset cursor: when `search` returns a non-null
`next_cursor`, passing it back verbatim as `cursor` — with the exact same
query and parameters — fetches the next page. The result set is frozen at the
first page, so a walk never duplicates or skips hits.

A typical exchange (synthetic):

> **User:** Did Alice ever email me about the harbor budget?
>
> **Assistant** → `search(query="harbor budget from:alice@potluck.test kind:email")`
> ← 3 hits, best first: `{id: 82, title_highlight: "Re: [harbor] Q2 [budget]", snippet: "…the [harbor] [budget] figures are attached…"}`, `next_cursor: "eyJx…"`
>
> **Assistant** → `get_thread(item_id=82)`
> ← the full conversation, oldest first
>
> **Assistant:** Yes — Alice sent the harbor budget figures in March 2025;
> the thread has four messages, ending with your reply approving them.
> *(If you ask for more matches, it passes `next_cursor` back verbatim with
> the same query for page two.)*

## Claude Desktop (stdio)

Claude menu → **Settings… → Developer → Edit Config** opens
`claude_desktop_config.json`:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

Run straight from GitHub via [uv](https://docs.astral.sh/uv/)'s `uvx`:

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

Or from a local clone:

```json
{
  "mcpServers": {
    "potluck": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/potluck", "potluck", "mcp"]
    }
  }
}
```

Restart Claude Desktop completely after saving; the tools appear behind the
MCP indicator at the bottom of the message box. GUI apps often launch without
your shell's `PATH` — if the server never appears, replace `"uvx"` with its
absolute path (`which uvx`, e.g. `/Users/you/.local/bin/uvx`).

Claude Desktop manages remote/HTTP connectors through **Settings →
Connectors**, not this file — for a local Potluck the stdio config above is
the supported route.

## Claude Code

stdio — Claude Code launches the server on demand (the `--` separates
Claude's flags from the server command):

```bash
claude mcp add potluck -- uvx --from git+https://github.com/DoubleGremlin181/potluck potluck mcp
# or, from a local clone:
claude mcp add potluck -- uv run --directory /path/to/potluck potluck mcp
```

Streamable HTTP — with `potluck serve` already running:

```bash
claude mcp add --transport http potluck http://127.0.0.1:8765/mcp
```

Both default to the private `local` scope; add `--scope project` to share the
config with your team via a `.mcp.json` at the project root:

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

(For the HTTP form use `{"type": "http", "url": "http://127.0.0.1:8765/mcp"}`
— `"streamable-http"` is accepted as an alias for `"http"`.) Check the
connection with `claude mcp list`, or `/mcp` inside a session.

## OpenClaw

MCP servers live in `~/.openclaw/openclaw.json` under `mcp.servers`
(*schema verified against [docs.openclaw.ai](https://docs.openclaw.ai/cli/mcp),
July 2026 — check your version's docs if this has moved*):

```json
{
  "mcp": {
    "servers": {
      "potluck": {
        "command": "uvx",
        "args": ["--from", "git+https://github.com/DoubleGremlin181/potluck", "potluck", "mcp"]
      }
    }
  }
}
```

Streamable HTTP, against a running `potluck serve`:

```bash
openclaw mcp add potluck --url http://127.0.0.1:8765/mcp --transport streamable-http
```

which writes:

```json
{
  "mcp": {
    "servers": {
      "potluck": {
        "url": "http://127.0.0.1:8765/mcp",
        "transport": "streamable-http"
      }
    }
  }
}
```

Restart the gateway after editing the file by hand. Leave OpenClaw's `auth` /
OAuth options unset — Potluck has none.

## Hermes

MCP servers live in `~/.hermes/config.yaml` under `mcp_servers` (*schema
verified against the
[Hermes MCP config reference](https://hermes-agent.nousresearch.com/docs/reference/mcp-config-reference),
July 2026 — check your version's docs if this has moved*):

```yaml
mcp_servers:
  potluck:
    command: uvx
    args: ["--from", "git+https://github.com/DoubleGremlin181/potluck", "potluck", "mcp"]
```

Remote servers are declared with `url` instead of `command` — with
`potluck serve` running:

```yaml
mcp_servers:
  potluck:
    url: "http://127.0.0.1:8765/mcp"
```

Skip `headers` and `auth` — Potluck needs neither.

## Any other MCP client

You need exactly one of:

- **stdio**: command `uvx`, args
  `["--from", "git+https://github.com/DoubleGremlin181/potluck", "potluck", "mcp"]`
  (or `uv run --directory /path/to/potluck potluck mcp` for a local clone) —
  no environment variables required;
- **streamable HTTP**: URL `http://127.0.0.1:8765/mcp` with `potluck serve`
  running.

To smoke-test either transport without an assistant, the
[MCP Inspector](https://modelcontextprotocol.io/docs/tools/inspector) works
out of the box:

```bash
npx @modelcontextprotocol/inspector uvx --from git+https://github.com/DoubleGremlin181/potluck potluck mcp
```

or launch it bare (`npx @modelcontextprotocol/inspector`) and connect to the
HTTP URL from the transport dropdown.

## Direct REST (no MCP)

Everything the MCP tools do is also plain HTTP on the serve port, so any
assistant that can call REST APIs (or generate a client from OpenAPI) can use
Potluck without MCP:

- Interactive docs (Swagger UI): <http://127.0.0.1:8765/api/docs>
- Machine-readable schema: <http://127.0.0.1:8765/api/openapi.json>

A search walkthrough — free text plus inline operators (the same query
language as the `search` tool; structured params like `kind=` and `after=`
exist too and win over inline operators — see `/api/docs`):

```bash
curl -s --get http://127.0.0.1:8765/api/search \
  --data-urlencode 'q=harbor budget from:alice@potluck.test' \
  --data-urlencode 'limit=5'
```

```json
{
  "query": "harbor budget from:alice@potluck.test",
  "hits": [
    {
      "id": 82,
      "kind": "email",
      "source": "gmail",
      "title": "Re: harbor Q2 budget",
      "title_highlight": "Re: [harbor] Q2 [budget]",
      "snippet": "…the [harbor] [budget] figures are attached…",
      "score": -3.41
    }
  ],
  "next_cursor": "eyJxIjoiaGFyYm9y…",
  "warnings": []
}
```

Rules worth honoring in a client:

- **Hits arrive best-first**; `score` is raw BM25 (negative, more negative =
  better). Don't re-sort.
- **Check `warnings`**: invalid operator values (`after:notadate`) are dropped
  and reported there — the search ran without them, so fix the query rather
  than trusting an unfiltered result.
- **Cursor etiquette**: pass `next_cursor` back verbatim as `cursor` with
  every other parameter unchanged; `null` means exhausted. Cursors are bound
  to the exact query that produced them.

```bash
# page 2
curl -s --get http://127.0.0.1:8765/api/search \
  --data-urlencode 'q=harbor budget from:alice@potluck.test' \
  --data-urlencode 'limit=5' \
  --data-urlencode 'cursor=eyJxIjoiaGFyYm9y…'

# full item, then its whole conversation
curl -s http://127.0.0.1:8765/api/items/82
curl -s http://127.0.0.1:8765/api/items/82/thread
```

Every `/api/*` error is a uniform envelope — machine `code`, human `message`,
plus field-level `detail` on 422s — and never a stack trace:

```json
{"error": {"code": "item_not_found", "message": "item 999999 not found"}}
{"error": {"code": "invalid_cursor", "message": "cursor does not match this query"}}
```

| Status | Code | Meaning |
|---|---|---|
| 400 | `invalid_cursor` | Malformed cursor, or one replayed under different parameters. |
| 404 | `item_not_found` | No item with this id. |
| 422 | `validation_error` | Bad request parameters; `error.detail` lists the offenders. |

Browsing without keywords is `GET /api/items` (filters + `limit`/`offset` —
not cursors); `GET /api/stats` mirrors `get_stats`.

## Troubleshooting

**The server never connects (stdio).** GUI clients often launch without your
shell's `PATH`, so bare `uvx`/`uv` isn't found — use the absolute path from
`which uvx` as the `command`. Then test the exact command from your config in
a terminal: `uvx --from git+https://github.com/DoubleGremlin181/potluck
potluck mcp` should start and sit silently (Ctrl-C to exit). Claude Desktop's
logs live at `~/Library/Logs/Claude/mcp-server-potluck.log` (macOS) or
`%APPDATA%\Claude\logs` (Windows); `claude mcp list` health-checks Claude
Code's servers.

**First launch times out.** `uvx --from git+…` resolves and builds the
package on first run (up to a few minutes), which can exceed a client's
startup timeout. Pre-warm the cache once in a terminal:

```bash
uvx --from git+https://github.com/DoubleGremlin181/potluck potluck --version
```

**Transport mismatch.** `potluck mcp` speaks stdio only — an HTTP client
pointed at it (or at `http://127.0.0.1:8765` without `/mcp`) gets nothing.
The `/mcp` URL only answers while `potluck serve` is running — an assistant
configured with the URL loses the connection when you stop the server. Pick
one: stdio configs name a *command*, HTTP configs name a *URL*.

**`/mcp` vs `/mcp/`.** The exact URL is `/mcp/`; `/mcp` answers with a 307
redirect that MCP HTTP clients follow automatically. If yours surfaces a
redirect error, configure `http://127.0.0.1:8765/mcp/` (trailing slash)
directly.

**Tools work but everything is empty.** The MCP server is reading a different
database. All Potluck commands share one default DB
(Linux `~/.local/share/potluck/potluck.db`, macOS
`~/Library/Application Support/potluck/potluck.db`,
Windows `%LOCALAPPDATA%\potluck\potluck.db`) — but if you point one process
elsewhere via `POTLUCK_DB_PATH` or `config.toml`, the MCP server won't see it
unless it gets the same setting, e.g. in Claude Desktop:

```json
{
  "mcpServers": {
    "potluck": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/DoubleGremlin181/potluck", "potluck", "mcp"],
      "env": { "POTLUCK_DB_PATH": "/path/to/your/potluck.db" }
    }
  }
}
```

`potluck status` (with the same environment) prints which database is in use,
and `get_stats` reports the path the server actually opened.

**stdio corruption.** On stdio, stdout *is* the protocol — Potluck keeps it
clean (its own logs go to stderr), so don't wrap the command in a shell
script that echoes, prints banners, or activates environments noisily.

**Tokens / auth.** There are none in v1. Leave client auth fields empty, and
never publish the port beyond localhost — anyone who can reach it can read
your data. Some clients restrict plain `http://` URLs to localhost; that is
the only place Potluck should run anyway.
