# Test harness

Patterns every phase reuses. All fixtures live in [`conftest.py`](conftest.py).

## Fixtures

| Fixture | What it gives you | Notes |
|---|---|---|
| `isolated_dirs` (autouse) | Private `XDG_DATA_HOME`/`XDG_CONFIG_HOME` under `tmp_path`; clean `POTLUCK_*` env | Applies to every test automatically — tests never touch real user data and are xdist-safe |
| `settings` | Zero-config `Settings()` resolving inside the isolated dirs | Override fields via `Settings(field=...)` in the test instead when needed |
| `ctx` | `AppContext` on a fresh tmp SQLite DB (migrated), closed on teardown | THE fixture for service-layer tests and everything above |
| `api_client` | FastAPI `TestClient` over `ctx` (lifespan runs) | No SPA build present → `/` serves the fallback message |
| `runner` (in `tests/unit/test_cli.py`) | Typer `CliRunner` | CLI tests reuse the service layer end to end |
| MCP in-memory | `async with fastmcp.Client(create_mcp(ctx))` | See `tests/unit/mcp/test_server.py`; pytest-asyncio is in auto mode |
| `server_url` (browser tests) | Real `potluck serve` subprocess, free port, tmp DB | `tests/e2e/test_browser_smoke.py`, marker `browser` |

## Tiers / markers

Default run = unit tier only; `bench`, `e2e`, `browser` markers are excluded via
`addopts` in `pyproject.toml`. Select explicitly, e.g. `pytest -m browser`.
`tests/relevance/` (the golden-query ranking eval, see its README) runs in the
default tier off a session-scoped synthetic corpus — fast and xdist-safe.
Hard perf budgets live in `tests/unit/bench/test_p*_budgets.py` (`bench`
marker, nightly): the P2 set generates multi-GB corpora in tmp and measures
imports in subprocesses for honest RSS numbers.

## Rules

- Tests are parallel-safe: no shared state, no fixed ports, no real user dirs.
  `pytest -n auto` must always pass.
- Reuse implementation code (services, `create_app`, `create_mcp`) — never
  reimplement behavior in tests.
- `tests/fixtures/` may only contain synthetic generator output; the PII guard
  (`scripts/check_fixtures.py`) enforces this in pre-commit and CI.
- Shared helpers in `tests/conftest.py`: `insert_source`/`insert_import` (raw
  ledger rows for storage-level tests) and `ingest_keep_corpus` (imports a
  synthetic Keep archive through the real service path).
