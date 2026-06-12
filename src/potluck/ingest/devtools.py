"""Developer tools for building and validating source plugins.

new_source  — scaffold a new source module from a clean template.
check_source — run a suite of validation checks against a registered plugin.
"""

import importlib
import inspect
from collections.abc import Iterator
from pathlib import Path
from typing import IO

import potluck.ingest.sources as _sources_pkg
from potluck.ingest.plugins import SourcePlugin, discover
from potluck.ingest.readers import Archive, Member

# ---------------------------------------------------------------------------
# Scaffold template
# ---------------------------------------------------------------------------

_TEMPLATE = '''\
"""{name} source plugin for Potluck.

TODO: Describe this source and its data format.
"""

from collections.abc import Iterator

from potluck.ingest.plugins import Glob, source
from potluck.ingest.readers import Archive
from potluck.models.drafts import NoteDraft  # TODO: replace with the appropriate draft type as more kinds land
from potluck.models.items import ItemKind


@source(
    name="{name}",
    detect=Glob("TODO/*.json"),  # TODO: update to match your archive layout
    kinds=(ItemKind.NOTE,),  # TODO: update kinds
    parser_version=1,
)
def parse(archive: Archive) -> Iterator[NoteDraft]:
    """Yield NoteDraft items from *archive*.

    TODO: implement parsing logic.
    """
    for _member, stream in archive.iter_members("TODO/*.json"):  # TODO: update pattern
        content = stream.read()
        # TODO: parse content and yield typed items
        yield NoteDraft(text=content.decode())
'''

# ---------------------------------------------------------------------------
# _EmptyArchive helper (used by check_source for safe validation)
# ---------------------------------------------------------------------------


class _EmptyArchive:
    """Minimal Archive stub that always returns empty iterators."""

    def iter_names(self) -> Iterator[str]:
        return iter([])

    def iter_members(self, pattern: str) -> Iterator[tuple[Member, IO[bytes]]]:
        return iter([])


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def new_source(name: str, package_root: Path | None = None) -> Path:
    """Scaffold ``src/potluck/ingest/sources/<name>.py`` from the standard template.

    *package_root* overrides the target directory (used by tests to avoid
    writing into the real sources package).

    Raises ``FileExistsError`` if the target file already exists.
    Returns the path of the written file.
    """
    if package_root is None:
        package_root = Path(_sources_pkg.__path__[0])

    target = package_root / f"{name}.py"
    if target.exists():
        raise FileExistsError(f"source module already exists: {target}")

    target.write_text(_TEMPLATE.format(name=name))
    return target


def check_source(name: str) -> list[str]:
    """Validate the source plugin named *name*; returns [] if everything is OK.

    Checks performed:
    1. Module ``potluck.ingest.sources.<name>`` is importable.
    2. Plugin is discoverable (present in registry after ``discover()``).
    3. ``kinds`` is non-empty.
    4. ``parse`` is a generator function (``inspect.isgeneratorfunction``).
    5. ``parse(empty_archive)`` yields nothing and raises nothing.
    6. ``parser_version >= 1``.
    """
    problems: list[str] = []

    # 1. Module importable — Exception, not ImportError: a broken module can
    # raise anything at import time (SyntaxError from a half-edited scaffold).
    full_name = f"potluck.ingest.sources.{name}"
    try:
        importlib.import_module(full_name)
    except Exception as exc:
        problems.append(f"module '{full_name}' not importable: {exc}")
        return problems  # further checks require a loaded module

    # 2. Discoverable
    plugins = discover()
    if name not in plugins:
        problems.append(
            f"plugin '{name}' not in registry after discover() — "
            f"make sure the module uses @source(name='{name}', ...)"
        )
        return problems

    plugin: SourcePlugin = plugins[name]

    # 3. Non-empty kinds
    if not plugin.kinds:
        problems.append("plugin.kinds must be non-empty")

    # 5. Generator function
    is_gen = inspect.isgeneratorfunction(plugin.parse)
    if not is_gen:
        problems.append(
            "plugin.parse must be a generator function (the function body must contain 'yield')"
        )

    # 6. parse(empty archive) yields nothing and raises nothing
    if is_gen:
        empty: Archive = _EmptyArchive()
        try:
            gen = plugin.parse(empty)
            first = next(iter(gen), None)
            if first is not None:
                problems.append(
                    f"parse yielded an unexpected item from an empty archive: {first!r}"
                )
        except Exception as exc:  # noqa: BLE001
            problems.append(
                f"parse raised {type(exc).__name__} when called on an empty archive: {exc}"
            )

    # 7. parser_version >= 1
    if plugin.parser_version < 1:
        problems.append(f"parser_version must be >= 1, got {plugin.parser_version}")

    return problems
