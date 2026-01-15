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


@app.command("download-models")
def download_models(
    device: str = typer.Option(
        None,
        "--device",
        "-d",
        help="Device to load models on ('cpu' or 'cuda'). Default: auto-detect.",
    ),
) -> None:
    """Pre-download all ML models for offline use.

    Downloads and caches all ML models used by Potluck processors:
    - Text encoder (e5-small-v2, ~90MB)
    - Multimodal encoder (SigLIP, ~380MB)
    - Face detector (MTCNN)
    - Face encoder (ArcFace, ~250MB)
    - OCR reader (EasyOCR, ~100MB)
    - Captioning model (BLIP-2, ~2.7GB)

    Models are cached locally and shared across all Potluck processes.
    """
    # Late import to avoid circular dependency: core/__init__.py imports cli.py,
    # and pipeline imports would trigger models/__init__.py which imports from core
    from potluck.pipeline.processing.core.ml import MLModels

    typer.echo("Downloading ML models...")
    models = MLModels(device=device)
    models.download_all_models()
    typer.echo("All models downloaded successfully!")
