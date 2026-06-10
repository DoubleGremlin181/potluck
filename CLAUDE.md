# Potluck v1 — AI Context (interim)

Potluck v1 is a ground-up rewrite in progress. The master plan, architecture, and locked
decisions live in pinned GitHub [issue #98](https://github.com/DoubleGremlin181/potluck/issues/98)
(milestones P0–P8). This file is a stub until the full v1 CLAUDE.md lands at the end of P0 (#112).

## Rules that apply from day one

- **Service-layer rule**: `api/`, `mcp/`, `cli/` import only `services/` + `models/` (+ `core/` infrastructure).
- **No conditional imports. No optional dependencies / extras — ever.** All imports top-of-file.
- **Batch-first**: every data path takes batches; no per-item DB round-trips.
- **TDD**: tests accompany every commit. One issue ≈ one commit (`Closes #N` in the body).
- **uv only** (never pip). Ruff + mypy `--strict`; Pydantic DTOs; avoid `Any`.
- v0 is archived at `archive/v0` (tag `v0-final`) — consult it for format knowledge, never import from it.
