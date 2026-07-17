"""Potluck command-line interface: thin Typer adapter over services."""

import json as _json
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, urlsplit

import typer
import uvicorn
from pydantic import TypeAdapter, ValidationError
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from potluck import __version__
from potluck.api.app import create_app
from potluck.bench.compare import compare, load_report
from potluck.bench.registry import TIERS, Tier, scenarios_for
from potluck.bench.runner import run_tier
from potluck.bench.scenarios import ALL_SCENARIOS
from potluck.core.config import Settings
from potluck.core.errors import ItemNotFoundError, PotluckError
from potluck.core.paths import gdrive_token_path
from potluck.mcp.server import run_stdio
from potluck.models.imports import ImportRun
from potluck.models.items import ItemKind, ItemSort, ListItemsRequest
from potluck.models.search import SearchRequest
from potluck.services import dev as dev_service
from potluck.services import gdrive as gdrive_service
from potluck.services import imports as imports_service
from potluck.services import items as items_service
from potluck.services import lifecycle as lifecycle_service
from potluck.services import search as search_service
from potluck.services import stats as stats_service
from potluck.services import threads as threads_service
from potluck.services import watch as watch_service
from potluck.services.context import create_context

console = Console()

# Module-level adapter: pydantic serializes DTO lists in one step (built once,
# not per call) — no model_dump_json → json.loads → json.dumps round trip.
_IMPORT_RUNS_JSON = TypeAdapter(list[ImportRun])

app = typer.Typer(
    name="potluck",
    help="Privacy-first personal knowledge database for your AI.",
    no_args_is_help=True,
)

bench_app = typer.Typer(help="Benchmark harness.", no_args_is_help=True)
app.add_typer(bench_app, name="bench")

dev_app = typer.Typer(help="Developer tools for building source plugins.", no_args_is_help=True)
app.add_typer(dev_app, name="dev")

gdrive_app = typer.Typer(
    help="Google Drive Takeout auto-pull (#152): one-time authorization and status.",
    no_args_is_help=True,
)
app.add_typer(gdrive_app, name="gdrive")


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        help="Show the version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """Potluck: privacy-first personal knowledge database for your AI."""


@app.command("import")
def import_(
    path: Path = typer.Argument(help="Path to the archive or directory to import."),
    as_json: bool = typer.Option(False, "--json", help="Print JSON output."),
) -> None:
    """Import data from an archive or directory (every detected source)."""
    ctx = create_context()
    try:
        runs = imports_service.import_path(ctx, path)
    except PotluckError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    finally:
        ctx.db.close()

    if as_json:
        print(_IMPORT_RUNS_JSON.dump_json(runs, indent=2).decode())
        return

    for run in runs:
        duration_s: float | None = None
        if run.finished_at is not None:
            duration_s = (run.finished_at - run.started_at).total_seconds()

        t = Table(show_header=True, header_style="bold")
        t.add_column("Field")
        t.add_column("Value")
        t.add_row("source", run.source)
        t.add_row("status", run.status)
        t.add_row("items_new", str(run.items_new))
        t.add_row("items_duplicate", str(run.items_duplicate))
        t.add_row("items_updated", str(run.items_updated))
        t.add_row("items_skipped", str(run.items_skipped))
        t.add_row("items_suppressed", str(run.items_suppressed))
        t.add_row("duration", f"{duration_s:.2f}s" if duration_s is not None else "-")
        t.add_row("path", run.path)
        console.print(t)


@app.command()
def search(
    query: str = typer.Argument(
        help=(
            "Full-text search query. Inline operators combine with free text: "
            "from:ADDR, source:NAME, kind:KIND, after:YYYY-MM-DD (inclusive), "
            "before:YYYY-MM-DD (exclusive)."
        )
    ),
    kinds: list[ItemKind] | None = typer.Option(None, "--kind", help="Filter by item kind."),
    prefix: bool = typer.Option(
        False, "--prefix", help="Search-as-you-type: the last token matches as a prefix."
    ),
    cursor: str | None = typer.Option(
        None, "--cursor", help="Pagination cursor from a previous result (excludes --offset)."
    ),
    limit: int = typer.Option(20, help="Maximum results to return (1-100)."),
    offset: int = typer.Option(0, help="Results offset."),
    as_json: bool = typer.Option(False, "--json", help="Print JSON output."),
) -> None:
    """Search items with full-text search."""
    ctx = create_context()
    try:
        req = SearchRequest(
            query=query, kinds=kinds, prefix=prefix, cursor=cursor, limit=limit, offset=offset
        )
        resp = search_service.search(ctx, req)
    except (ValidationError, PotluckError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    finally:
        ctx.db.close()

    if as_json:
        print(resp.model_dump_json(indent=2))
        return

    for warning in resp.warnings:
        console.print(f"[yellow]warning:[/] {escape(warning)}")

    if not resp.hits:
        console.print("No results found.")
        return

    t = Table(show_header=True, header_style="bold")
    t.add_column("ID")
    t.add_column("KIND")
    t.add_column("TS")
    t.add_column("TITLE")
    t.add_column("SNIPPET")

    for hit in resp.hits:
        ts_str = hit.ts.date().isoformat() if hit.ts is not None else "-"
        title_str = escape(hit.title) if hit.title is not None else "-"
        snippet_str = escape(hit.snippet)
        t.add_row(str(hit.id), hit.kind, ts_str, title_str, snippet_str)

    console.print(t)
    if resp.next_cursor is not None:
        console.print(f"more results: --cursor {resp.next_cursor}")


@app.command("list")
def list_(
    kinds: list[ItemKind] | None = typer.Option(
        None, "--kind", help="Filter by item kind (repeatable)."
    ),
    sources: list[str] | None = typer.Option(
        None, "--source", help="Filter by source name (repeatable)."
    ),
    since: datetime | None = typer.Option(
        None, help="Only items with ts on/after this (ISO-8601; naive means UTC)."
    ),
    until: datetime | None = typer.Option(
        None, help="Only items with ts before this (ISO-8601; naive means UTC)."
    ),
    sort: ItemSort = typer.Option(ItemSort.TS_DESC, "--sort", help="Sort order."),
    limit: int = typer.Option(20, help="Maximum results to return (1-100)."),
    offset: int = typer.Option(0, help="Results offset."),
    as_json: bool = typer.Option(False, "--json", help="Print JSON output."),
) -> None:
    """List items with filters — no search query needed."""
    ctx = create_context()
    try:
        req = ListItemsRequest(
            kinds=kinds,
            sources=sources,
            since=since,
            until=until,
            sort=sort,
            limit=limit,
            offset=offset,
        )
        resp = items_service.list_items(ctx, req)
    except ValidationError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    finally:
        ctx.db.close()

    if as_json:
        print(resp.model_dump_json(indent=2))
        return

    if not resp.items:
        console.print("No items found.")
        return

    t = Table(show_header=True, header_style="bold")
    t.add_column("ID")
    t.add_column("KIND")
    t.add_column("SOURCE")
    t.add_column("TS")
    t.add_column("TITLE")
    t.add_column("TEXT")

    for item in resp.items:
        ts_str = item.ts.date().isoformat() if item.ts is not None else "-"
        title_str = escape(item.title) if item.title is not None else "-"
        preview = escape(item.text_preview) if item.text_preview is not None else "-"
        t.add_row(str(item.id), item.kind, item.source, ts_str, title_str, preview)

    console.print(t)
    first = resp.offset + 1
    last = resp.offset + len(resp.items)
    console.print(f"showing {first}-{last} of {resp.total}")


@app.command()
def show(
    item_id: int = typer.Argument(help="Item ID to display."),
    thread: bool = typer.Option(
        False, "--thread", help="Show the whole email conversation containing the item."
    ),
    as_json: bool = typer.Option(False, "--json", help="Print JSON output."),
) -> None:
    """Show full details for a single item (or its whole conversation)."""
    if thread:
        _show_thread(item_id, as_json)
        return

    ctx = create_context()
    try:
        item = items_service.get_item(ctx, item_id)
    except ItemNotFoundError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    finally:
        ctx.db.close()

    if as_json:
        print(item.model_dump_json(indent=2))
        return

    t = Table(show_header=False)
    t.add_column("Field", style="bold")
    t.add_column("Value")
    t.add_row("id", str(item.id))
    t.add_row("kind", item.kind)
    t.add_row("source", item.source)
    t.add_row("external_id", item.external_id or "-")
    t.add_row("content_hash", item.content_hash)
    t.add_row("ts", item.ts.isoformat() if item.ts is not None else "-")
    t.add_row("title", escape(item.title) if item.title is not None else "-")
    t.add_row("text", escape(item.text) if item.text is not None else "-")
    t.add_row("meta", escape(_json.dumps(item.meta, indent=2)))
    if item.email is not None:
        e = item.email
        t.add_row("from", escape(_mailbox(e.from_addr, e.from_name)) or "-")
        t.add_row("to", escape(_mailboxes(e.to_addrs, e.to_names)) or "-")
        if e.cc_addrs:
            t.add_row("cc", escape(_mailboxes(e.cc_addrs, e.cc_names)))
        if e.bcc_addrs:
            t.add_row("bcc", escape(", ".join(e.bcc_addrs)))
        if e.labels:
            t.add_row("labels", escape(", ".join(e.labels)))
        t.add_row("message_id", escape(e.message_id) if e.message_id else "-")
        t.add_row("thread_key", escape(e.thread_key))
        if e.attachments:
            t.add_row(
                "attachments",
                escape(
                    "\n".join(
                        f"{a.filename} ({a.mime or 'unknown'}, {a.size_bytes or 0} bytes)"
                        for a in e.attachments
                    )
                ),
            )
    if item.message is not None:
        msg = item.message
        t.add_row("chat", escape(msg.chat_name or msg.chat_key))
        t.add_row("chat_key", escape(msg.chat_key))
        t.add_row("sender", escape(msg.sender) if msg.sender else "-")
        if msg.is_media:
            media = ", ".join(m.filename for m in msg.media)
            t.add_row("media", escape(media) if media else "(omitted from export)")
    if item.transaction is not None:
        txn = item.transaction
        t.add_row("amount", _milliunits(txn.amount_milliunits))
        t.add_row("account", escape(txn.account) if txn.account else "-")
        t.add_row("payee", escape(txn.payee) if txn.payee else "-")
        category = ": ".join(p for p in (txn.category_group, txn.category) if p)
        t.add_row("category", escape(category) if category else "-")
    console.print(t)


def _milliunits(amount: int) -> str:
    """Exact decimal rendering of integer milliunits — int math only, the
    sub-cent digit shown only when the amount actually carries one. No
    currency symbol: the register stores none (a budget-level setting)."""
    sign = "-" if amount < 0 else ""
    units, frac = divmod(abs(amount), 1000)
    if frac % 10:
        return f"{sign}{units}.{frac:03d}"
    return f"{sign}{units}.{frac // 10:02d}"


def _mailbox(addr: str | None, name: str | None) -> str:
    """'Name <addr>' when a display name exists, else the bare addr."""
    if addr is None:
        return ""
    return f"{name} <{addr}>" if name else addr


def _mailboxes(addrs: list[str], names: list[str]) -> str:
    """Render parallel addr/name lists; rows from before the #199 re-ingest
    may have fewer (or no) names than addrs."""
    padded = names + [""] * (len(addrs) - len(names))
    return ", ".join(_mailbox(addr, name) for addr, name in zip(addrs, padded, strict=False))


def _show_thread(item_id: int, as_json: bool) -> None:
    """Print the conversation containing *item_id*, oldest message first."""
    ctx = create_context()
    try:
        resp = threads_service.get_thread(ctx, item_id)
    except ItemNotFoundError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    finally:
        ctx.db.close()

    if as_json:
        print(resp.model_dump_json(indent=2))
        return

    t = Table("ID", "TS", "FROM", "TITLE", "PREVIEW")
    for entry in resp.entries:
        t.add_row(
            str(entry.id),
            entry.ts.date().isoformat() if entry.ts is not None else "-",
            entry.from_addr or "-",
            escape(entry.title) if entry.title is not None else "-",
            escape(entry.text_preview) if entry.text_preview is not None else "-",
        )
    console.print(t)
    key = resp.thread_key or "(not an email thread)"
    console.print(f"{len(resp.entries)} message(s) in thread {key}")


@app.command()
def status(
    as_json: bool = typer.Option(False, "--json", help="Print JSON output."),
) -> None:
    """Show a database overview: counts, location, size, versions."""
    ctx = create_context()
    try:
        stats = stats_service.get_stats(ctx)
        import_runs = imports_service.list_imports(ctx).runs
        watch = watch_service.get_watch_status(ctx)
    finally:
        ctx.db.close()

    if as_json:
        payload = {
            "stats": stats.model_dump(mode="json"),
            "imports": [r.model_dump(mode="json") for r in import_runs],
            "watch": watch.model_dump(mode="json"),
        }
        print(_json.dumps(payload, indent=2))
        return

    for key, value in stats.model_dump().items():
        typer.echo(f"{key}: {value}")

    # Watch-folder section (#151). Runtime fields (last scan / pending) are
    # meaningful only inside `potluck serve` — the sole process that polls —
    # so a CLI invocation truthfully reports 'never' / 'none'.
    state = "enabled" if watch.enabled else "disabled"
    typer.echo(f"watch: {state} ({watch.effective_enabled_source})")
    if not watch.folders:
        typer.echo("watch folders: no watch folders configured")
    else:
        typer.echo(f"watch interval: {watch.interval_s}s")
        for folder in watch.folders:
            typer.echo(f"watch folder: {folder.path} ({'ok' if folder.exists else 'missing'})")
        last_scan = (
            watch.last_scan_at.isoformat()
            if watch.last_scan_at
            else "never (in this process — the watcher polls inside `potluck serve`)"
        )
        typer.echo(f"watch last scan: {last_scan}")
        stabilizing = sum(1 for p in watch.pending if p.state == "stabilizing")
        backoff = sum(1 for p in watch.pending if p.state == "backoff")
        pending = f"{stabilizing} stabilizing, {backoff} backoff" if watch.pending else "none"
        typer.echo(f"watch pending: {pending}")

    if import_runs:
        t = Table(show_header=True, header_style="bold")
        t.add_column("ID")
        t.add_column("SOURCE")
        t.add_column("STATUS")
        t.add_column("NEW")
        t.add_column("DUP")
        t.add_column("UPD")
        t.add_column("STARTED")
        t.add_column("ERROR")
        for run in import_runs:
            error_str = run.error[:40] if run.error else "-"
            t.add_row(
                str(run.id),
                run.source,
                run.status,
                str(run.items_new),
                str(run.items_duplicate),
                str(run.items_updated),
                run.started_at.isoformat(),
                error_str,
            )
        console.print(t)


def _remove(
    item_ids: list[int] | None,
    import_id: int | None,
    source: str | None,
    *,
    yes: bool,
    as_json: bool,
    forget: bool,
) -> None:
    """Shared rm/forget body: validate the selector, confirm, delete, report."""
    selectors = sum([bool(item_ids), import_id is not None, source is not None])
    if selectors != 1:
        raise typer.BadParameter("pass exactly one of: item ids, --import, or --source")

    ctx = create_context()
    try:
        if import_id is not None:
            # Resolve before prompting: a typo'd id fails here, not post-confirm.
            run = imports_service.get_import(ctx, import_id)
            prompt = f"Delete import #{import_id} ({run.source}) and every item it ingested?"
        elif source is not None:
            prompt = f"Delete ALL items and the whole import history of source '{source}'?"
        else:
            assert item_ids is not None
            ids_str = ", ".join(str(i) for i in item_ids)
            prompt = f"Delete {len(item_ids)} item(s) [{ids_str}]?"
        if forget:
            prompt += " Their content will also be blocked from ever re-importing."
        if not yes:
            typer.confirm(prompt, abort=True)

        if import_id is not None:
            result = lifecycle_service.remove_import(ctx, import_id, forget=forget)
        elif source is not None:
            result = lifecycle_service.remove_source(ctx, source, forget=forget)
        else:
            assert item_ids is not None
            result = lifecycle_service.remove_items(ctx, item_ids, forget=forget)
    except PotluckError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    finally:
        ctx.db.close()

    if as_json:
        print(result.model_dump_json(indent=2))
        return
    summary = f"deleted {result.items_deleted} item(s), {result.imports_deleted} import run(s)"
    if forget:
        summary += f"; suppressed {result.hashes_suppressed} content hash(es)"
    typer.echo(summary)


@app.command()
def rm(
    item_ids: list[int] | None = typer.Argument(None, help="Item ids to delete."),
    import_id: int | None = typer.Option(
        None, "--import", help="Delete one import run and every item it ingested."
    ),
    source: str | None = typer.Option(
        None, "--source", help="Delete a source's items and its whole import history."
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    as_json: bool = typer.Option(False, "--json", help="Print JSON output."),
) -> None:
    """Delete items — by id, by import run, or by whole source.

    Satellite data, attachment metadata and the search index follow
    automatically. Plain rm means the content MAY return on a re-import of
    the same archive; use `potluck forget` to also block it from ever
    re-importing.
    """
    _remove(item_ids, import_id, source, yes=yes, as_json=as_json, forget=False)


@app.command()
def forget(
    item_ids: list[int] | None = typer.Argument(None, help="Item ids to forget."),
    import_id: int | None = typer.Option(
        None, "--import", help="Forget one import run and every item it ingested."
    ),
    source: str | None = typer.Option(
        None, "--source", help="Forget a source's items and its whole import history."
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    as_json: bool = typer.Option(False, "--json", help="Print JSON output."),
) -> None:
    """Delete items AND block their content from ever re-importing.

    Everything `potluck rm` does, plus the deleted items' content hashes are
    recorded in the suppression registry — future imports drop matching
    content (counted as items_suppressed in the run's summary).
    """
    _remove(item_ids, import_id, source, yes=yes, as_json=as_json, forget=True)


@app.command()
def serve(
    host: str | None = typer.Option(None, help="Bind address (default from config: 127.0.0.1)."),
    port: int | None = typer.Option(None, help="Port (default from config: 8765)."),
    no_browser: bool = typer.Option(False, "--no-browser", help="Do not open the browser."),
) -> None:
    """Start the Potluck server: web app + API on one port."""
    overrides: dict[str, Any] = {}
    if host is not None:
        overrides["host"] = host
    if port is not None:
        overrides["port"] = port
    ctx = create_context(Settings(**overrides))
    uvicorn.run(
        create_app(ctx, open_browser=not no_browser),
        host=ctx.settings.host,
        port=ctx.settings.port,
        log_level="info",
    )


@app.command()
def mcp() -> None:
    """Start the MCP server on stdio (for local AI clients).

    Streamable HTTP needs no separate command: `potluck serve` exposes the
    same tools at /mcp on the web/API port.
    """
    run_stdio(create_context())


# ---------------------------------------------------------------------------
# gdrive (#152): one-time OAuth authorization + status. The CLI owns exactly
# the UI mechanics of the loopback flow (browser, one-shot localhost listener,
# paste fallback); everything protocol-shaped lives in services.gdrive.
# ---------------------------------------------------------------------------

# Fixed loopback redirect for --no-browser: no listener is bound (the redirect
# fails to load wherever the browser runs — the user copies it from the
# address bar), but the URI must still be a loopback address and must match
# exactly between the consent URL and the code exchange.
_NO_BROWSER_REDIRECT_URI = "http://127.0.0.1:8085/"

_AUTH_LANDING_HTML = (
    b"<!doctype html><meta charset='utf-8'><title>Potluck</title>"
    b"<p>Authorization received &mdash; you can close this tab and return to the terminal.</p>"
)


class _LoopbackServer(HTTPServer):
    """One-shot localhost listener catching the OAuth redirect."""

    captured_path: str | None = None


class _RedirectHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - http.server API
        assert isinstance(self.server, _LoopbackServer)
        self.server.captured_path = self.path
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(_AUTH_LANDING_HTML)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - http.server API
        pass  # never log request lines (they carry the authorization code)


def _parse_redirect(url: str, expected_state: str) -> str:
    """The authorization code from a captured/pasted redirect URL.

    Verifies the ``state`` echo (CSRF guard) and surfaces Google's ``error``
    param (e.g. access_denied) as a clean CLI failure.
    """
    query = parse_qs(urlsplit(url).query)
    if "error" in query:
        raise typer.BadParameter(f"authorization refused by Google: {query['error'][0]}")
    code = query.get("code", [None])[0]
    state = query.get("state", [None])[0]
    if not code or not state:
        raise typer.BadParameter("redirect URL carries no code/state parameters")
    if state != expected_state:
        raise typer.BadParameter("state mismatch — not the redirect for this auth attempt")
    return code


@gdrive_app.command("auth")
def gdrive_auth(
    prune: bool = typer.Option(
        False,
        "--prune",
        help="Also request the FULL Drive scope so gdrive_prune can permanently "
        "delete pulled archives from Drive. Destructive capability — only grant "
        "it if you want that.",
    ),
    no_browser: bool = typer.Option(
        False,
        "--no-browser",
        help="Headless flow: print the consent URL, then paste the full redirect "
        "URL back (copy it from the browser's address bar — the 127.0.0.1 page "
        "failing to load there is expected).",
    ),
) -> None:
    """Authorize Potluck against your own Google OAuth client (one-time).

    Requires gdrive_client_id / gdrive_client_secret in config.toml — see
    docs/gdrive-setup.md for the Google Cloud console walkthrough. The token
    is written to a 0600 file under the config dir, never to the database.
    """
    ctx = create_context()
    try:
        if no_browser:
            auth = gdrive_service.build_authorization(
                ctx, prune=prune, redirect_uri=_NO_BROWSER_REDIRECT_URI
            )
            typer.echo("Open this URL in any browser and approve access:")
            typer.echo(auth.url)
            typer.echo(
                "The final redirect (to 127.0.0.1) will fail to load — that is "
                "expected. Copy the full URL from the address bar."
            )
            pasted = typer.prompt("Paste the full redirect URL")
            code = _parse_redirect(pasted, auth.state)
        else:
            with _LoopbackServer(("127.0.0.1", 0), _RedirectHandler) as server:
                auth = gdrive_service.build_authorization(
                    ctx, prune=prune, redirect_uri=f"http://127.0.0.1:{server.server_port}/"
                )
                typer.echo("Opening Google's consent page in your browser…")
                typer.echo(f"(if nothing opens, visit: {auth.url})")
                webbrowser.open(auth.url)
                while server.captured_path is None:
                    server.handle_request()  # one-shot; Ctrl+C aborts cleanly
                code = _parse_redirect(server.captured_path, auth.state)
        status = gdrive_service.complete_authorization(
            ctx, code=code, redirect_uri=auth.redirect_uri, code_verifier=auth.code_verifier
        )
    except PotluckError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Authorized. Token saved (0600) to {gdrive_token_path()}")
    if status.prune_scope_granted:
        typer.echo(
            "Full Drive scope granted: gdrive_prune = true in config.toml will "
            "PERMANENTLY delete pulled archives from Drive after import."
        )
    elif status.prune:
        typer.echo(
            "Note: gdrive_prune is enabled but only read access was granted — "
            "re-run with --prune to allow pruning.",
            err=True,
        )
    typer.echo("Takeout archives will be pulled while `potluck serve` runs.")


@gdrive_app.command("status")
def gdrive_status(
    as_json: bool = typer.Option(False, "--json", help="Print JSON output."),
) -> None:
    """Show Drive-pull configuration, auth state and runtime status.

    Runtime fields are meaningful only inside `potluck serve` — the sole
    process that pulls; this command reports the durable state.
    """
    ctx = create_context()
    status = gdrive_service.get_gdrive_status(ctx)
    if as_json:
        typer.echo(status.model_dump_json(indent=2))
        return
    typer.echo(f"gdrive: {'configured' if status.configured else 'not configured'}")
    typer.echo(f"gdrive auth: {status.auth_state}")
    typer.echo(f"gdrive enabled: {status.enabled} ({status.effective_enabled_source})")
    prune = "on (destructive)" if status.prune else "off"
    if status.prune and not status.prune_scope_granted:
        prune += " — scope NOT granted; re-run `potluck gdrive auth --prune`"
    typer.echo(f"gdrive prune: {prune}")
    typer.echo(f"gdrive folder: {status.folder_name}")
    typer.echo(f"gdrive interval: {status.interval_s}s")
    typer.echo(f"gdrive downloads dir: {status.downloads_dir}")
    typer.echo(f"gdrive pulled files: {status.pulled_files}")
    last_check = status.last_check_at.isoformat() if status.last_check_at else "never"
    last_pull = status.last_pull_at.isoformat() if status.last_pull_at else "never"
    typer.echo(f"gdrive last check: {last_check}")
    typer.echo(f"gdrive last pull: {last_pull}")
    if status.offline:
        typer.echo("gdrive connectivity: offline (will retry next cycle)")
    if status.backoff_cycles is not None:
        typer.echo(f"gdrive backoff: retry in {status.backoff_cycles} cycle(s)")
    if status.last_error:
        typer.echo(f"gdrive last error: {status.last_error}")


@bench_app.command("run")
def bench_run(
    tier: str = typer.Option("smoke", help=f"Scenario tier: {', '.join(TIERS)}."),
    json_out: Path | None = typer.Option(None, "--json", help="Write results JSON to this path."),
) -> None:
    """Run benchmark scenarios and print a summary."""
    if tier not in TIERS:
        raise typer.BadParameter(f"tier must be one of: {', '.join(TIERS)}")
    report = run_tier(cast(Tier, tier), json_out)
    for result in report.results:
        typer.echo(
            f"{result.name}: median {result.median_s * 1000:.1f} ms | "
            f"p95 {result.p95_s * 1000:.1f} ms | "
            f"{result.throughput_items_s:.0f} items/s | "
            f"peak RSS {result.peak_rss_kb // 1024} MiB"
        )
    if json_out is not None:
        typer.echo(f"results written to {json_out}")


@bench_app.command("compare")
def bench_compare(
    baseline: Path = typer.Argument(help="Baseline JSON (e.g. benchmarks/baselines-ci.json)."),
    current: Path = typer.Argument(help="Current run JSON."),
    tolerance: float = typer.Option(30.0, help="Allowed median regression in percent."),
) -> None:
    """Compare a bench run against a baseline; exit 1 on any regression."""
    current_report = load_report(current)
    # Scenarios the current tier never runs (full-only vs a smoke run) are not
    # "missing" — one full-tier baseline file serves both CI gates.
    in_tier = {s.name for s in scenarios_for(current_report.tier, ALL_SCENARIOS)}
    out_of_tier = frozenset(s.name for s in ALL_SCENARIOS if s.name not in in_tier)
    regressions = compare(load_report(baseline), current_report, tolerance, out_of_tier=out_of_tier)
    if not regressions:
        typer.echo(f"OK: no regressions beyond {tolerance:.0f}% tolerance")
        return
    for reg in regressions:
        if reg.metric == "missing":
            typer.echo(f"REGRESSION {reg.scenario}: missing from current run")
        else:
            typer.echo(
                f"REGRESSION {reg.scenario}: {reg.metric} {reg.baseline:.4f}s -> "
                f"{reg.current:.4f}s (+{reg.change_pct:.1f}% > {tolerance:.0f}%)"
            )
    raise typer.Exit(1)


@dev_app.command("new-source")
def dev_new_source(
    name: str = typer.Argument(help="Name of the new source plugin (e.g. my_source)."),
    package_dir: Path | None = typer.Option(
        None,
        "--dir",
        help="Override target directory (default: src/potluck/ingest/sources/).",
        hidden=True,
    ),
) -> None:
    """Scaffold a new source plugin module."""
    try:
        created = dev_service.new_source(name, package_root=package_dir)
    except (FileExistsError, FileNotFoundError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(str(created))


@dev_app.command("check-source")
def dev_check_source(
    name: str = typer.Argument(help="Name of the source plugin to validate."),
) -> None:
    """Validate a registered source plugin."""
    problems = dev_service.check_source(name)
    if not problems:
        typer.echo("OK")
        return
    for problem in problems:
        typer.echo(problem, err=True)
    raise typer.Exit(1)
