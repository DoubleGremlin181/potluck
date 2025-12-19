"""CLI infrastructure using Typer."""

import typer

# Main CLI application
app = typer.Typer(
    name="potluck",
    help="Personal Knowledge Database - Expose your data to LLMs via MCP",
    no_args_is_help=True,
)


@app.command()
def mcp() -> None:
    """Start the MCP server (stdio transport for Claude Desktop)."""
    from potluck.mcp.server import run_mcp_server

    run_mcp_server()


@app.command()
def web(
    host: str = typer.Option(
        None,
        "--host",
        "-h",
        help="Host to bind to (default: from settings or 0.0.0.0)",
    ),
    port: int = typer.Option(
        None,
        "--port",
        "-p",
        help="Port to bind to (default: from settings or 8000)",
    ),
) -> None:
    """Start the web UI server."""
    from potluck.core.config import get_settings
    from potluck.web.app import run_web_server

    settings = get_settings()
    actual_host = host or settings.web_host
    actual_port = port or settings.web_port

    typer.echo(f"Starting web server on {actual_host}:{actual_port}")
    run_web_server(host=actual_host, port=actual_port)
