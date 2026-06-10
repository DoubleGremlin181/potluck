"""Potluck command-line interface: thin Typer adapter over services."""

from typing import Any

import typer
import uvicorn

from potluck import __version__
from potluck.api.app import create_app
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
def bench_run() -> None:
    """Run benchmark scenarios."""
    typer.echo("bench: no scenarios registered yet (the bench rig lands with #109)")


@bench_app.command("compare")
def bench_compare() -> None:
    """Compare two benchmark result files."""
    typer.echo("bench: no scenarios registered yet (the bench rig lands with #109)")
