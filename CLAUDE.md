# Potluck - AI Context

Privacy-first personal knowledge database exposing data to LLMs via MCP. All processing local.

## Architecture Principles

1. **Source-agnostic entities** - `ChatMessage` works for any platform, `source_type` tracks origin
2. **core/ = infrastructure only** - Domain base classes live with domains (`models/base.py`, `ingesters/base.py`)
3. **Pluggable ingesters** - Each source (Google Takeout, Reddit, etc.) has its own ingester package with auto-detection
4. **Media: paths only, Text: store raw** - No blobs in DB for media; text stored for FTS
5. **Multiple embeddings per entity** - Single table stores different embedding types (text, CLIP, OCR) per entity
6. **Hybrid search** - RRF fusion

**Workflow**:

1. Refer to GitHub issues for roadmap. Milestones break them into phases.
2. Create a new branch for each phase: `phase-1-dev`. Do not push to `main` directly.
3. Each commit should ~generally tie to one issue (feature or bug fix). Remember to include tests.
4. When milestone complete, update `pyproject.toml` version, merge to `main` and tag: `git tag v0.1.0`  (use semantic versioning, e.g. Phase 1 = `0.1.x`)
5. Push tag to trigger GitHub release and Docker image publishing

**Release Process** (triggered by pushing a tag):

1. Update version in `pyproject.toml`
2. Merge phase branch to `main`
3. Create and push tag:
   ```bash
   git tag v0.2.0
   git push origin v0.2.0
   ```
4. GitHub Actions will automatically:
   - Build and push `ghcr.io/doublegremlin181/potluck:<version>` (CPU app image)
   - Build and push `ghcr.io/doublegremlin181/potluck:<version>-gpu` (GPU app image)
   - Build and push `ghcr.io/doublegremlin181/potluck-db:<version>` (database image)
   - Create GitHub release with changelog

**CI Base Image Caching**:

- CI uses `docker/Dockerfile` with `--target deps` to cache dependencies in GHCR
- Base image tag: `ghcr.io/doublegremlin181/potluck-base:cpu-<hash>` (hash of pyproject.toml + uv.lock)
- Rebuilds only when dependencies change, not on code changes
- If dependencies change, CI builds new base image (~8-10 min); otherwise uses cached (~2-3 min)

**Reminders**:

- Code style: Ruff (format + lint) + mypy (strict). Type hints required, Pydantic for DTOs. Avoid `Any` type where possible.
- Do not make imports optional or conditional (e.g., TYPE_CHECKING guards, try/except ImportError). All dependencies are always available.
- Keep all imports at the top of the file. If you have an issue with circular imports, clarify the requirements since it is likely an architecture issue.
- ML dependencies are always installed as part of the Docker/setup. Use `uv` (not `pip`) for package management.
- Only add functionality as needed. Do not front load work. E.g. Only add exceptions to src/potluck/core/exceptions.py while creating the feature that will raise it.
- Raise Potluck specific exceptions that are defined in `core/exceptions.py`
- Tests should reuse as much code from the implementation as possible

## References

- [PRD.md](PRD.md)
- [MCP Docs](https://modelcontextprotocol.io/)
