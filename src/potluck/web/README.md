# Web Module

FastAPI web application serving an HTMX-powered UI with Jinja2 templates. Designed for single-user, local-first usage.

## Architecture

```
web/
├── app.py              # FastAPI factory, middleware, media serving
├── dependencies.py     # get_db, SESSION_MAX_AGE
├── utils.py            # Shared parsing (datetime, entity types)
├── routers/            # 13 route modules
│   ├── auth.py         # Login/logout (signed cookies)
│   ├── dashboard.py    # Home page with entity counts
│   ├── search.py       # Hybrid search UI
│   ├── media.py        # Media gallery with grid/detail views
│   ├── notes.py        # Notes listing
│   ├── people.py       # People + person detail
│   ├── timeline.py     # Chronological feed (infinite scroll)
│   ├── imports.py      # File upload + import management
│   ├── events.py       # SSE real-time progress stream
│   ├── map.py          # Location map (Leaflet.js)
│   ├── tags.py         # Tag browsing
│   ├── settings.py     # Application settings
│   └── entity.py       # Generic entity detail view
├── templates/
│   ├── base.html       # Root layout (nav, scripts, styles)
│   ├── pages/          # Full page templates
│   ├── partials/       # HTMX fragment responses
│   └── components/     # Reusable template components
└── static/
    ├── css/
    └── js/
```

## Routers

All routers are registered in `app.py` via `app.include_router()`. Each router is a self-contained `APIRouter` with its own tag.

| Router | Prefix | Purpose |
|--------|--------|---------|
| auth | `/login`, `/logout` | Password auth, session cookie |
| dashboard | `/` | Entity counts, overview |
| search | `/search` | Full-text + vector search |
| media | `/media` | Gallery grid, detail modal |
| notes | `/notes` | Note listing |
| people | `/people` | People list, person detail |
| timeline | `/timeline` | Chronological entity feed |
| imports | `/imports` | File upload, import history |
| events | `/events/progress` | SSE progress stream |
| map | `/map` | Location visualization |
| tags | `/tags` | Tag browsing |
| settings | `/settings` | App configuration |
| entity | `/entity` | Generic entity detail |

## Template Organization

Templates follow a three-level hierarchy:

- **`base.html`** -- Root layout with navigation, HTMX/Alpine.js scripts, CSS imports
- **`pages/`** -- Full page templates that extend `base.html` (e.g., `dashboard.html`, `search.html`)
- **`partials/`** -- HTMX fragment templates returned for XHR requests (e.g., `search_results.html`, `timeline_items.html`, `media_grid.html`)
- **`components/`** -- Reusable template snippets included by pages and partials (e.g., `entity_card.html`, `pagination.html`)

Custom Jinja2 filters registered in `app.py`:
- `basename` -- extracts filename from path
- `enum_val` -- extracts `.value` from Python enums

## HTMX Patterns

**Partial responses:** Routers check `HX-Request` header to return either a full page or just the HTMX fragment:

```python
if request.headers.get("HX-Request"):
    return templates.TemplateResponse(request, "partials/search_results.html", ctx)
return templates.TemplateResponse(request, "pages/search.html", ctx)
```

**SSE for real-time progress:** The `events` router streams import progress via Server-Sent Events. The client subscribes using HTMX's SSE extension. The stream creates a fresh DB session per poll to avoid holding connection pool slots.

**Infinite scroll:** The timeline uses `hx-trigger="revealed"` to load additional items as the user scrolls.

**Polling:** Active imports on the `/imports` page use `hx-trigger="every 3s"` to poll for status updates.

## Authentication

Auth is optional and controlled by the `WEB_PASSWORD` environment variable:

- **Disabled** (default): No `WEB_PASSWORD` set, all requests pass through
- **Enabled**: Password set, requires login via signed cookie

Implementation:
- `AuthMiddleware` in `app.py` intercepts all requests (except `/login`, `/static/*`, `/favicon.ico`)
- Password comparison uses `hmac.compare_digest()` (constant-time)
- Session tokens are signed with `URLSafeTimedSerializer` using `WEB_SECRET_KEY`
- Cookies are `httponly`, `samesite=lax`, with 30-day expiry (`SESSION_MAX_AGE`)
- `AuthMiddleware` handles all route protection centrally

## Dependencies

One FastAPI dependency is shared across routers:

- **`get_db`** -- yields an `AsyncSession` for database access

## Media Serving

Media files are served through database ID lookups (no filesystem paths exposed to clients):

- `GET /media/file/{media_id}` -- serves the original file
- `GET /media/thumb/{media_id}` -- serves a thumbnail (currently the original)

Both endpoints resolve the media ID to a filesystem path via the `Media` model.

## Design Decisions

- **No CSRF tokens**: This is a single-user, local-first application. The auth cookie uses `samesite=lax`, which prevents cross-origin form submissions. Combined with the local trust model, CSRF protection is unnecessary overhead.
- **No API docs**: `docs_url=None, redoc_url=None` since this is a UI application, not an API service.
- **Signed cookies over JWTs**: Simpler for single-user; `itsdangerous` handles signing and expiry in one package.
