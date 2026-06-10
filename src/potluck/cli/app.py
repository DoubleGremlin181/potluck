"""Potluck command-line interface: thin Typer adapter over services."""

from pathlib import Path
from typing import Any, cast

import typer
import uvicorn

from potluck import __version__
from potluck.api.app import create_app
from potluck.bench.compare import compare, load_report
from potluck.bench.registry import TIERS, Tier
from potluck.bench.runner import run_tier
from potluck.core.config import Settings
from potluck.mcp.server import run_http, run_stdio
from potluck.services import stats as stats_service
from potluck.services.context import create_context

app = typer.Typer(
    name="potluck",
    help="Privacy-first personal knowledge database for your AI.",
    no_args_is_help=True,
)

bench_app = typer.Typer(help="Benchmark harness.", no_args_is_help=True)
app.add_typer(bench_app, name="bench")


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


@app.command()
def status() -> None:
    """Show a database overview: counts, location, size, versions."""
    ctx = create_context()
    try:
        stats = stats_service.get_stats(ctx)
        for key, value in stats.model_dump().items():
            typer.echo(f"{key}: {value}")
    finally:
        ctx.db.close()


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
