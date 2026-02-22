"""CLI infrastructure using Typer."""

import json
import time
from datetime import datetime
from pathlib import Path
from uuid import UUID

import typer
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn
from rich.table import Table
from sqlmodel import Session, select

from potluck.core.config import get_settings
from potluck.db.session import get_engine
from potluck.mcp.server import run_mcp_server
from potluck.models.base import EntityType, SourceType
from potluck.models.sources import ImportRun
from potluck.pipeline import (
    DiscoveryResult,
    PipelineFilter,
    PipelineStats,
    detect_stage,
    discover,
    get_stage,
    start_ingestion,
)
from potluck.pipeline import (
    ingest as pipeline_ingest,
)
from potluck.pipeline.processing.core.ml import MLModels
from potluck.pipeline.utils.archive import extracted
from potluck.web.app import run_web_server

# Main CLI application
app = typer.Typer(
    name="potluck",
    help="Personal Knowledge Database - Expose your data to LLMs via MCP",
    no_args_is_help=True,
)

# Shared console for rich output
_console = Console()


@app.command()
def mcp() -> None:
    """Start the MCP server (stdio transport for Claude Desktop)."""
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
    - Multimodal encoder (SigLIP2, ~380MB)
    - Face detector (MTCNN)
    - Face encoder (ArcFace, ~250MB)
    - OCR reader (EasyOCR, ~100MB)
    - Captioning model (Florence-2, ~460MB)

    Models are cached locally and shared across all Potluck processes.
    """
    typer.echo("Downloading ML models...")
    models = MLModels(device=device)
    models.download_all_models()
    typer.echo("All models downloaded successfully!")


@app.command()
def ingest(
    path: Path = typer.Argument(
        ...,
        exists=True,
        help="Path to source file or directory to ingest",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        "-n",
        help="Preview only (discover available entities without importing)",
    ),
    source: str | None = typer.Option(
        None,
        "--source",
        "-S",
        help="Source type (google_takeout, android_timeline, reddit, whatsapp, ynab, generic)",
    ),
    entity_types: list[str] | None = typer.Option(
        None,
        "--type",
        "-t",
        help="Entity types to import (repeatable)",
    ),
    since: datetime | None = typer.Option(
        None,
        "--since",
        "-s",
        formats=["%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"],
        help="Only import entities after this date",
    ),
    until: datetime | None = typer.Option(
        None,
        "--until",
        "-u",
        formats=["%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"],
        help="Only import entities before this date",
    ),
    non_interactive: bool = typer.Option(
        False,
        "-y",
        "--yes",
        help="Non-interactive mode (import all types without prompts)",
    ),
    async_mode: bool = typer.Option(
        False,
        "--async",
        "-a",
        help="Queue to Celery and return immediately",
    ),
    wait_for_processing: bool = typer.Option(
        False,
        "--wait",
        "-w",
        help="Wait for processing to complete",
    ),
    resume_failed: bool = typer.Option(
        False,
        "--resume-failed",
        help="Retry failed imports",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Output as JSON (with --dry-run)",
    ),
) -> None:
    """Import data and run the full processing pipeline.

    By default, runs discovery to show available entities and prompts for
    selection. Use -y for non-interactive mode. Ingestion runs synchronously
    with processing/linking queued to Celery in the background.

    Examples:
        potluck ingest ./Takeout                    # Interactive import
        potluck ingest ./Takeout -y                 # Import all, no prompts
        potluck ingest ./Takeout --dry-run          # Preview only
        potluck ingest ./Takeout -t media -t email  # Import specific types
        potluck ingest ./data --source google_takeout  # Force source type
    """
    # 1. Resolve source type
    if source:
        source_type = _validate_source_type(source)
        stage_cls = get_stage(source_type)
        if stage_cls is None:
            raise typer.BadParameter(f"No ingester registered for source: {source}")
    else:
        stage_cls = detect_stage(path)
        if stage_cls is None:
            raise typer.BadParameter(
                f"Could not auto-detect source type for: {path}\nUse --source to specify manually."
            )

    # Wrap in a single extraction context to avoid double-extraction.
    # Both discover() and ingest() reuse the same extracted content_path.
    with extracted(path) as content_path:
        # 2. Run discovery (reuses extraction)
        result = discover(path, content_path=content_path)

        # 3. Handle --dry-run
        if dry_run:
            if json_output:
                _output_discovery_json(result)
            else:
                _display_discovery_table(result)
            return

        # 4. Select entity types (interactive or from flags)
        types_to_ingest = _resolve_entity_types(
            available=result.available_entities,
            requested=entity_types,
            interactive=not non_interactive,
        )
        if types_to_ingest is None:  # User quit
            raise typer.Abort()

        # 5. Build filter
        filters: PipelineFilter | None = None
        if since or until:
            filters = PipelineFilter(since=since, until=until)

        # 6. Validate date range (PipelineFilter already validates, but provide clearer error)
        if filters and filters.since and filters.until and filters.since > filters.until:
            raise typer.BadParameter("--since must be before --until")

        # 7. Run based on mode
        if async_mode:
            _run_async_ingest(path, types_to_ingest, filters)
        else:
            _run_sync_ingest(
                path, content_path, types_to_ingest, filters, resume_failed, wait_for_processing
            )


# -----------------------------------------------------------------------------
# Helper functions for ingest command
# -----------------------------------------------------------------------------


def _validate_source_type(source: str) -> SourceType:
    """Validate and convert source type string."""
    try:
        return SourceType(source)
    except ValueError:
        valid = [st.value for st in SourceType if st not in (SourceType.MANUAL,)]
        raise typer.BadParameter(
            f"Invalid source type: {source}\nValid types: {', '.join(valid)}"
        ) from None


def _output_discovery_json(result: DiscoveryResult) -> None:
    """Output discovery result as JSON."""
    data = {
        "source_type": result.source_type.value if result.source_type else None,
        "source_path": str(result.source_path),
        "entity_counts": {et.value: count for et, count in result.available_entities.items()},
        "total": sum(result.available_entities.values()),
        "metadata": result.metadata,
    }
    typer.echo(json.dumps(data, indent=2))


def _display_discovery_table(result: DiscoveryResult) -> None:
    """Display discovery results as Rich table."""
    source_name = result.source_type.value if result.source_type else "Unknown"
    _console.print(f"\nSource: [bold]{source_name}[/bold]")

    if not result.available_entities:
        _console.print("[yellow]No entities found[/yellow]")
        return

    table = Table(title="Available Entities")
    table.add_column("Entity Type", style="cyan")
    table.add_column("Count", justify="right")

    total = 0
    for entity_type, count in sorted(result.available_entities.items(), key=lambda x: -x[1]):
        table.add_row(entity_type.value, f"{count:,}")
        total += count

    table.add_section()
    table.add_row("[bold]Total[/bold]", f"[bold]{total:,}[/bold]")

    _console.print(table)


def _resolve_entity_types(
    available: dict[EntityType, int],
    requested: list[str] | None,
    interactive: bool,
) -> set[EntityType] | None:
    """Resolve entity types to ingest. Returns None if user quits."""
    if requested:
        return _validate_entity_types(requested, set(available.keys()))

    if not interactive:
        return set(available.keys())  # Import all

    return _select_entity_types_interactive(available)


def _select_entity_types_interactive(available: dict[EntityType, int]) -> set[EntityType] | None:
    """Interactive prompt for entity type selection."""
    sorted_types = sorted(available.items(), key=lambda x: -x[1])

    _console.print("\nAvailable entity types:")
    for i, (etype, count) in enumerate(sorted_types, 1):
        _console.print(f"  [{i}] {etype.value:<20} ({count:,} items)")

    selection = typer.prompt(
        "\nSelect types (comma-separated numbers, 'all', or 'q' to quit)",
        default="all",
    )

    if selection.lower() == "q":
        return None

    if selection.lower() == "all":
        return set(available.keys())

    # Parse numbers
    try:
        indices = [int(x.strip()) for x in selection.split(",")]
        selected: set[EntityType] = set()
        for idx in indices:
            if 1 <= idx <= len(sorted_types):
                selected.add(sorted_types[idx - 1][0])
            else:
                raise typer.BadParameter(f"Invalid selection: {idx}")
        return selected
    except ValueError as e:
        raise typer.BadParameter(f"Invalid input: {selection}") from e


def _validate_entity_types(types: list[str], available: set[EntityType]) -> set[EntityType]:
    """Validate and convert entity type strings."""
    result: set[EntityType] = set()
    for t in types:
        try:
            etype = EntityType(t)
            if etype not in available:
                raise typer.BadParameter(
                    f"Entity type '{t}' not available in this source. "
                    f"Available: {', '.join(et.value for et in available)}"
                )
            result.add(etype)
        except ValueError as e:
            valid = [et.value for et in EntityType]
            raise typer.BadParameter(
                f"Invalid entity type: {t}\nValid types: {', '.join(valid)}"
            ) from e
    return result


def _run_async_ingest(
    path: Path,
    entity_types: set[EntityType],
    filters: PipelineFilter | None,
) -> None:
    """Queue ingestion to Celery and return immediately."""
    task_id, import_run_id = start_ingestion(path, list(entity_types))

    typer.echo("\nQueued ingestion job.")
    typer.echo(f"Task ID: {task_id}")
    typer.echo(f"Import Run ID: {import_run_id}")


def _run_sync_ingest(
    path: Path,
    content_path: Path,
    entity_types: set[EntityType],
    filters: PipelineFilter | None,
    resume_failed: bool,
    wait_for_processing: bool,
) -> None:
    """Run synchronous ingestion with progress bar."""
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TextColumn("{task.completed}/{task.total}"),
        console=_console,
    ) as progress:
        task = progress.add_task("Importing...", total=None)

        def on_progress(current: int, total: int, message: str | None) -> None:
            progress.update(task, completed=current, total=total)
            if message:
                progress.update(task, description=f"Importing... ({message})")

        engine = get_engine()
        with Session(engine) as session:
            result = pipeline_ingest(
                path=path,
                session=session,
                entity_types=entity_types,
                filters=filters,
                on_progress=on_progress,
                resume_failed=resume_failed,
                content_path=content_path,
            )
            # Capture import_run_id while session is open to avoid DetachedInstanceError
            import_run_id = str(result.import_run.id)

    # Display results
    _display_import_stats(result.stats)
    _console.print(f"\nImport Run ID: {import_run_id}")

    if result.stats.entities_created > 0:
        _console.print(f"Processing queued ({result.stats.entities_created} tasks).")
        if not wait_for_processing:
            _console.print("Use --wait to block until complete.")

    # Wait for processing if requested
    if wait_for_processing and result.stats.entities_created > 0:
        _wait_for_processing(import_run_id)


def _display_import_stats(stats: PipelineStats) -> None:
    """Display import statistics."""
    table = Table(title="Import Complete")
    table.add_column("Metric", style="cyan")
    table.add_column("Count", justify="right")

    table.add_row("Created", f"{stats.entities_created:,}")
    table.add_row("Skipped", f"{stats.entities_skipped:,}")
    table.add_row("Failed", f"{stats.entities_failed:,}")

    _console.print(table)


def _wait_for_processing(import_run_id: str) -> None:
    """Poll until processing tasks complete.

    For MVP, this uses a simple spinner and checks ImportRun status.
    A future enhancement could track individual Celery task completion.
    """
    _console.print("\nWaiting for processing to complete...")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=_console,
    ) as progress:
        task = progress.add_task("Processing...", total=None)

        engine = get_engine()
        while True:
            time.sleep(2)

            # Check import run status
            with Session(engine) as session:
                stmt = select(ImportRun).where(ImportRun.id == UUID(import_run_id))
                run = session.exec(stmt).first()
                if run is None:
                    progress.update(task, description="Import run not found")
                    break

                # For now, just exit - processing runs async via Celery
                # A more sophisticated implementation would track task completion
                progress.update(task, description="Processing queued to Celery")
                break

    _console.print(
        "Processing tasks are running in background via Celery.\n"
        "Check Celery worker logs for progress."
    )
