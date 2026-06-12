# Potluck — AI Context

Privacy-first personal knowledge database for your AI. Local-first: SQLite + FTS5, FastAPI +
React SPA, MCP-native. v1 is a ground-up rewrite; the master plan (phases, architecture,
perf budgets, locked decisions) lives in pinned GitHub
[issue #98](https://github.com/DoubleGremlin181/potluck/issues/98) — update it as phases close.

## Architecture

```
src/potluck/
  core/      config (pydantic-settings), paths (platformdirs), errors
  models/    Pydantic DTOs only — NO ORM
  storage/   SQLite: pragmas, single writer thread, NNN_*.sql migrations (PRAGMA user_version)
  ingest/    streaming ETL plane: readers, sources/<name>/, content-hash ledger (P1+)
  enrich/    derived-data reconciler plane: anti-join work discovery, executors (P5+)
  search/    FTS5 query builder, VecIndex protocol, hybrid RRF (P1+)
  services/  THE shared layer — plain sync functions (ctx, req) -> resp with Pydantic DTOs
  api/ mcp/ cli/   thin adapters over services (enforced by import-linter)
  testing/   synthetic generators (shipped; reused by tests, fixtures, bench)
  bench/     scenario registry, runner, compare
web/         Vite + React + TS + Tailwind + shadcn/ui (dist built in CI, served by FastAPI)
```

## Absolute rules

1. **Service-layer rule**: `api/`, `mcp/`, `cli/` import only `services` + `models` (+ `core`
   infrastructure) — never `storage`/`ingest`/`enrich`/`search` directly. CI enforces this
   (import-linter contracts in `pyproject.toml`).
2. **No conditional imports. No optional dependencies / extras — ever.** All imports
   top-of-file. ML dependencies become core dependencies when their phase lands. One
   install shape for everyone.
3. **Batch-first**: data paths take batches (one `IN(...)` dedup query + one `executemany`
   per batch); no per-item DB round-trips. Ingestion stays a single-threaded loop until a
   bench scenario proves otherwise.
4. **All writes go through `Database.write()`** (single writer thread owns the sole write
   connection); reads use the per-thread query-only connections from `Database.read()`.
5. **Pydantic DTOs at boundaries; mypy strict; avoid `Any`.** Raise Potluck-specific
   exceptions from `core/errors.py`, adding each one only with the feature that raises it.
6. **Fixtures are generated**: `tests/fixtures/` contains only `potluck.testing` generator
   output. The PII guard (`scripts/check_fixtures.py`) runs in pre-commit + CI. Never commit
   real export content; consult real exports locally for shape only.

## Workflow

- Phases = milestones (P0–P8) tracked in #98. One issue ≈ one feature ≈ one commit; the
  commit body contains `Closes #N`.
- Branch `v1/p<N>-<slug>` per phase; PR to `main` at phase end; merge with a **merge commit**
  (preserves per-issue commits so `Closes` fires on main).
- **Never merge a PR yourself.** Open the PR and stop — Kavish reviews and merges. Tagging
  and releasing happen only after his merge.
- Phase end: bump `version` in `pyproject.toml`, tag (PEP 440 aware: version `1.0.0a1` ↔ tag
  `v1.0.0-alpha.1`), push tag → release workflow publishes the GitHub Release (wheel with
  embedded SPA) + GHCR image. `latest` Docker tag only on stable releases.
- TDD: tests accompany every commit and reuse implementation code; shared fixtures live in
  `tests/conftest.py` (documented in `tests/README.md`).

## Commands

- `uv sync` · `uv run pytest` (unit tier; `-n auto` must stay green) · `uv run pytest -m browser`
  (needs `web/dist` + playwright chromium)
- `uv run ruff check` · `uv run ruff format --check` · `uv run mypy` · `uv run lint-imports`
- `uv run potluck bench run --tier smoke|full --json out.json` ·
  `uv run potluck bench compare benchmarks/baselines-ci.json out.json --tolerance 30`
  (baselines are refreshed from CI artifacts only — never from a dev machine)
- Web: `cd web && npm ci && npm run lint && npm run build` (Node is dev-only)
- Always `uv`, never pip. Hooks: `uv run pre-commit install`.

## Performance budgets

Per-phase budget table lives in #98; budgets are enforced by bench scenarios as their
features land (PR CI = smoke tier ±30% vs baselines + scaling assertions; nightly = full tier).

## v0

Archived at `archive/v0` (tag `v0-final`) and `archive/v0-phase-9-webui`. Port format
knowledge from v0 parsers as specs; never import v0 code. The 40-defect catalog
(`docs/ISSUES.md` on the archive branch) doubles as an anti-checklist for reviews.
