"""Potluck command-line interface: thin Typer adapter over services."""

import json as _json
from pathlib import Path
from typing import Any, cast

import typer
import uvicorn
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from potluck import __version__
from potluck.api.app import create_app
from potluck.bench.compare import compare, load_report
from potluck.bench.registry import TIERS, Tier
from potluck.bench.runner import run_tier
from potluck.core.config import Settings
from potluck.core.errors import ItemNotFoundError, UnknownSourceError, UnsupportedArchiveError
from potluck.mcp.server import run_http, run_stdio
from potluck.models.items import ItemKind
from potluck.models.search import SearchRequest
from potluck.services import dev as dev_service
from potluck.services import imports as imports_service
from potluck.services import items as items_service
from potluck.services import search as search_service
from potluck.services import stats as stats_service
from potluck.services.context import create_context

console = Console()

app = typer.Typer(
    name="potluck",
    help="Privacy-first personal knowledge database for your AI.",
    no_args_is_help=True,
)

bench_app = typer.Typer(help="Benchmark harness.", no_args_is_help=True)
app.add_typer(bench_app, name="bench")

dev_app = typer.Typer(help="Developer tools for building source plugins.", no_args_is_help=True)
app.add_typer(dev_app, name="dev")


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
    """Import data from an archive or directory."""
    ctx = create_context()
    try:
        run = imports_service.import_path(ctx, path)
    except (UnknownSourceError, UnsupportedArchiveError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    finally:
        ctx.db.close()

    if as_json:
        print(run.model_dump_json(indent=2))
        return

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
    t.add_row("duration", f"{duration_s:.2f}s" if duration_s is not None else "-")
    t.add_row("path", run.path)
    console.print(t)


@app.command()
def search(
    query: str = typer.Argument(help="Full-text search query."),
    kinds: list[ItemKind] | None = typer.Option(None, "--kind", help="Filter by item kind."),
    limit: int = typer.Option(20, help="Maximum results to return (1-100)."),
    offset: int = typer.Option(0, help="Results offset."),
    as_json: bool = typer.Option(False, "--json", help="Print JSON output."),
) -> None:
    """Search items with full-text search."""
    ctx = create_context()
    try:
        req = SearchRequest(query=query, kinds=kinds, limit=limit, offset=offset)
        resp = search_service.search(ctx, req)
    finally:
        ctx.db.close()

    if as_json:
        print(resp.model_dump_json(indent=2))
        return

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


@app.command()
def show(
    item_id: int = typer.Argument(help="Item ID to display."),
    as_json: bool = typer.Option(False, "--json", help="Print JSON output."),
) -> None:
    """Show full details for a single item."""
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
    console.print(t)


@app.command()
def status(
    as_json: bool = typer.Option(False, "--json", help="Print JSON output."),
) -> None:
    """Show a database overview: counts, location, size, versions."""
    ctx = create_context()
    try:
        stats = stats_service.get_stats(ctx)
        import_runs = imports_service.list_imports(ctx)
    finally:
        ctx.db.close()

    if as_json:
        payload = {
            "stats": stats.model_dump(mode="json"),
            "imports": [r.model_dump(mode="json") for r in import_runs],
        }
        print(_json.dumps(payload, indent=2))
        return

    for key, value in stats.model_dump().items():
        typer.echo(f"{key}: {value}")

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
def mcp(
    http: bool = typer.Option(False, "--http", help="Serve streamable HTTP instead of stdio."),
    host: str = typer.Option("127.0.0.1", help="HTTP bind address."),
    port: int = typer.Option(8766, help="HTTP port."),
) -> None:
    """Start the MCP server (stdio by default; --http for streamable HTTP)."""
    if http:
        run_http(create_context(), host=host, port=port)
    else:
        run_stdio(create_context())


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
    regressions = compare(load_report(baseline), load_report(current), tolerance)
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
