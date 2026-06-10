"""Tests for potluck.ingest.devtools: new_source scaffold and check_source validator."""

import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Combined fixture: clean registry + extended sources.__path__
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_sources(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    """Isolate sources discovery for devtools tests.

    - Swaps _registry to a fresh dict (restored by monkeypatch on teardown).
    - Appends tmp_path to potluck.ingest.sources.__path__ (restored by monkeypatch).
    - Removes any modules imported during the test from sys.modules on teardown.
    """
    import potluck.ingest.plugins as plugins_mod
    import potluck.ingest.sources as sources_pkg

    before_modules = set(sys.modules.keys())

    # Empty registry
    fresh: dict[str, Any] = {}
    monkeypatch.setattr(plugins_mod, "_registry", fresh)

    # Extend sources.__path__ so discover() finds modules in tmp_path
    new_path = list(sources_pkg.__path__) + [str(tmp_path)]
    monkeypatch.setattr(sources_pkg, "__path__", new_path)

    yield tmp_path

    # Remove modules imported from tmp_path during the test
    for key in list(sys.modules.keys()):
        if key not in before_modules:
            del sys.modules[key]


# ---------------------------------------------------------------------------
# Tests — new_source
# ---------------------------------------------------------------------------


_VALID_MODULE = '''\
"""toy_ok source plugin for Potluck.

TODO: Describe this source and its data format.
"""

from collections.abc import Iterator

from potluck.ingest.plugins import Glob, source
from potluck.ingest.readers import Archive
from potluck.models.drafts import NoteDraft
from potluck.models.items import ItemKind


@source(
    name="toy_ok",
    detect=Glob("*toy/*.txt"),
    kinds=(ItemKind.NOTE,),
    parser_version=1,
)
def parse(archive: Archive) -> Iterator[NoteDraft]:
    """Toy parser (yields nothing)."""
    notes: list[NoteDraft] = []
    yield from notes
'''

_NON_GENERATOR_MODULE = '''\
"""toy_bad source plugin for testing — parse is NOT a generator."""

from collections.abc import Iterator

from potluck.ingest.plugins import Glob, source
from potluck.ingest.readers import Archive
from potluck.models.drafts import NoteDraft
from potluck.models.items import ItemKind


@source(
    name="toy_bad",
    detect=Glob("*toy/*.txt"),
    kinds=(ItemKind.NOTE,),
    parser_version=1,
)
def parse(archive: Archive) -> list[NoteDraft]:  # type: ignore[return-value]
    """Not a generator — returns a list."""
    return []
'''


def test_new_source_scaffold(tmp_path: Path) -> None:
    from potluck.ingest.devtools import new_source

    path = new_source("toy_source", package_root=tmp_path)

    assert path == tmp_path / "toy_source.py"
    assert path.exists()

    content = path.read_text()
    assert "@source" in content
    assert "def parse" in content
    assert "Iterator[NoteDraft]" in content

    # Second call on same name refuses to overwrite
    with pytest.raises(FileExistsError):
        new_source("toy_source", package_root=tmp_path)


def test_scaffold_is_strict_clean(tmp_path: Path) -> None:
    """Scaffolded file must at minimum be syntactically valid Python."""
    from potluck.ingest.devtools import new_source

    path = new_source("syntax_check", package_root=tmp_path)
    source_code = path.read_text()

    # compile() raises SyntaxError if the template has syntax errors
    compile(source_code, str(path), "exec")

    # Key structural markers for a strict-clean module
    assert "from collections.abc import Iterator" in source_code
    assert "from potluck.ingest.plugins import" in source_code
    assert "from potluck.ingest.readers import Archive" in source_code
    assert "from potluck.models.drafts import NoteDraft" in source_code


# ---------------------------------------------------------------------------
# Tests — check_source
# ---------------------------------------------------------------------------


def test_check_source_ok(isolated_sources: Path) -> None:
    from potluck.ingest.devtools import check_source

    # Write a valid module to the isolated tmp sources directory
    (isolated_sources / "toy_ok.py").write_text(_VALID_MODULE)

    problems = check_source("toy_ok")
    assert problems == [], f"Expected no problems, got: {problems}"


def test_check_source_non_generator_parse(isolated_sources: Path) -> None:
    from potluck.ingest.devtools import check_source

    (isolated_sources / "toy_bad.py").write_text(_NON_GENERATOR_MODULE)

    problems = check_source("toy_bad")
    assert any("generator" in p.lower() for p in problems), (
        f"Expected a generator-related problem, got: {problems}"
    )


def test_check_source_missing_module(isolated_sources: Path) -> None:
    from potluck.ingest.devtools import check_source

    # No file written — module does not exist
    problems = check_source("no_such_source")
    assert len(problems) > 0
    assert any("no_such_source" in p for p in problems)
