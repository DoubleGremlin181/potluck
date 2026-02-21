# Tests

Three-tier test architecture: unit tests, integration tests (Docker DB), and browser E2E tests (Playwright).

For the full fixture hierarchy, marker system, page objects, and CI details, see [docs/TESTING.md](../docs/TESTING.md).

## Directory Structure

```
tests/
├── conftest.py                 # Root fixtures: images, paths, marker system
├── fixtures/
│   ├── generate_fixtures.py    # ML test image generator (run once)
│   ├── sample_text.png         # OCR test fixture
│   ├── sample_face.jpg         # Face detection test fixture
│   └── google_takeout/         # Ingestion test fixtures
├── unit/                       # Pure unit tests (no external deps)
│   ├── core/
│   ├── models/
│   ├── pipeline/
│   ├── search/
│   └── web/
├── integration/
│   ├── conftest.py             # DB lifecycle, credentials, migrations
│   ├── test_e2e_setup.py       # Validates Docker + DB + migrations
│   └── test_ingestion_pipeline.py
└── e2e/
    ├── conftest.py             # Live server, auth cookie setup
    ├── pages/                  # Page Object models
    ├── test_navigation.py
    ├── test_imports.py
    └── test_map.py
```

## Running Tests

```bash
# All tests (Docker)
docker compose --profile test run --rm test

# Unit tests only (local)
uv run pytest tests/unit/

# Integration tests (requires Docker DB)
uv run pytest tests/integration/ --run-e2e

# Browser E2E tests (requires Playwright)
uv run pytest tests/e2e/ -m browser

# ML-dependent tests
uv run pytest --run-ml
```

## Markers

| Marker | Flag | Purpose |
|--------|------|---------|
| `@pytest.mark.e2e` | `--run-e2e` | Integration tests requiring Docker DB |
| `@pytest.mark.browser` | `-m browser` (separate run) | Playwright browser tests |
| `@pytest.mark.ml` | `--run-ml` | Tests requiring ML dependencies |

Default config excludes `e2e` and `browser` markers. See `pyproject.toml` for details.
