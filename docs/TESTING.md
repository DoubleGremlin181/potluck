# Testing

## Test Architecture

Three-tier test architecture covering unit tests, integration tests (Docker DB), and browser E2E tests (Playwright).

## Running Tests

**All tests (Docker -- recommended):**
```bash
docker compose --profile test run --rm test
```

**Unit tests only (local, no Docker required):**
```bash
uv run pytest tests/unit/
```

**Integration tests (requires Docker DB):**
```bash
uv run pytest tests/integration/ --run-e2e
```

**Browser E2E tests (requires Playwright + running server):**
```bash
uv run pytest tests/e2e/ -m browser
```

**ML-dependent tests:**
```bash
uv run pytest --run-ml
```

## Marker System

| Marker | Flag | Purpose |
|--------|------|---------|
| `@pytest.mark.e2e` | `--run-e2e` | Integration tests requiring Docker DB |
| `@pytest.mark.browser` | Run separately: `-m browser` | Playwright browser tests |
| `@pytest.mark.ml` | `--run-ml` | Tests requiring torch, facenet-pytorch, etc. |

Default pytest config in `pyproject.toml` excludes both `e2e` and `browser`:

```toml
addopts = "-m 'not e2e and not browser'"
```

When `--run-e2e` is passed, e2e tests run but browser tests remain excluded (they require a separate invocation due to Playwright/asyncio event loop conflicts).

## Fixture Hierarchy

Fixtures are layered across three conftest files, each building on the previous:

### Root conftest (`tests/conftest.py`)
- Image fixtures: `sample_jpeg_path`, `sample_png_path`, `identical_images_different_formats`
- ML fixtures: `image_with_text`, `image_with_face` (require pre-generated files)
- Path fixtures: `google_takeout_fixtures_path`, `temp_media_path`
- Marker registration and collection modification

### Integration conftest (`tests/integration/conftest.py`)
- `db_credentials` -- reads from env vars (Docker/CI) or `.env` file (local)
- `ensure_db_available` -- starts Docker Compose locally, verifies DB in CI
- `db_connection` -- session-scoped psycopg2 connection
- `run_migrations` -- ensures Alembic migrations are applied

### E2E conftest (`tests/e2e/conftest.py`)
- `live_server` -- spawns FastAPI in a child process on port 8765
- `authenticated_page` -- Playwright Page with a pre-set signed session cookie
- Auto-skip logic when Playwright Chromium is not installed

## Fixture Generation

ML test images must be generated before running ML tests:

```bash
python tests/fixtures/generate_fixtures.py
```

This creates `sample_text.png` (OCR) and `sample_face.jpg` (face detection). The face image is downloaded from Wikipedia (public domain Obama portrait).

## Page Object Pattern

Browser tests use Page Objects to encapsulate page interactions:

```python
from tests.e2e.pages.imports_page import ImportsPage

def test_imports_polling(authenticated_page, live_server):
    page = ImportsPage(authenticated_page, live_server)
    page.navigate()
    page.wait_for_htmx_poll()
```

Available page objects:
- `ImportsPage` -- `/imports` page with HTMX polling assertions
- `MapPage` -- `/map` page with Leaflet marker interactions and filter controls

## CI Integration

GitHub Actions (`.github/workflows/ci.yml`) runs the same Docker-based tests:

1. Builds the app image (with CI base image caching)
2. Starts `db` and `redis` services
3. Runs Alembic migrations inside the test container
4. Runs unit + e2e tests: `pytest tests/ -v --run-e2e --run-ml`
5. Runs browser tests separately: `playwright install --with-deps chromium && pytest tests/e2e/ -v -m browser`
6. Uploads coverage to Codecov
