"""Tests for potluck.ingest.plugins: Glob, SourcePlugin, source, discover, detect_source."""

from collections.abc import Iterator
from typing import IO, Any

import pytest

from potluck.ingest.plugins import ParseContext
from potluck.ingest.readers import Archive, Member
from potluck.models.drafts import NoteDraft
from potluck.models.items import ItemKind

# ---------------------------------------------------------------------------
# Fake archive helpers implementing the Archive Protocol
# ---------------------------------------------------------------------------


class FakeArchive:
    """Minimal Archive stub for testing detect_source."""

    def __init__(self, names: list[str]) -> None:
        self._names = names

    def iter_names(self) -> Iterator[str]:
        yield from self._names

    def iter_members(self, pattern: str) -> Iterator[tuple[Member, IO[bytes]]]:
        return iter([])


class CountingArchive:
    """Archive that records how many names were served — tests early-exit behaviour."""

    def __init__(self, names: list[str]) -> None:
        self._names = names
        self.served: int = 0

    def iter_names(self) -> Iterator[str]:
        for name in self._names:
            self.served += 1
            yield name

    def iter_members(self, pattern: str) -> Iterator[tuple[Member, IO[bytes]]]:
        return iter([])


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_glob_matches() -> None:
    from potluck.ingest.plugins import Glob

    g = Glob("*Keep/*.json")
    assert g.matches("Takeout/Keep/a.json") is True
    assert g.matches("Takeout/Keep/a.html") is False
    # fnmatch: '*' crosses '/', so 'Keep/a.json' also matches
    assert g.matches("Keep/a.json") is True
    assert g.matches("Other/a.json") is False


def test_glob_matches_case_sensitive_on_every_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    """Archive member names are virtual posix paths: matching must not pick up
    the host platform's case folding (os.path.normcase lowercases on Windows,
    which would make imports diverge per platform)."""
    import os.path

    from potluck.ingest.plugins import Glob

    monkeypatch.setattr(os.path, "normcase", lambda s: s.lower())  # simulate Windows
    assert Glob("*Keep/*.json").matches("Takeout/KEEP/a.JSON") is False
    assert Glob("*Keep/*.json").matches("Takeout/Keep/a.json") is True


def test_source_decorator_registers(clean_registry: dict[str, Any]) -> None:
    from potluck.ingest.plugins import Glob, SourcePlugin, source

    @source(
        name="test_reg_plugin",
        detect=Glob("*Test/*.txt"),
        kinds=(ItemKind.NOTE,),
        parser_version=1,
    )
    def parse(archive: Archive, ctx: ParseContext) -> Iterator[NoteDraft]:
        notes: list[NoteDraft] = []
        yield from notes

    assert "test_reg_plugin" in clean_registry
    plugin: SourcePlugin = clean_registry["test_reg_plugin"]
    assert plugin.name == "test_reg_plugin"
    assert plugin.parser_version == 1
    assert plugin.kinds == (ItemKind.NOTE,)
    # Decorator returns the function unchanged
    assert callable(parse)


def test_duplicate_name_raises(clean_registry: dict[str, Any]) -> None:
    from potluck.core.errors import DuplicateSourceError
    from potluck.ingest.plugins import Glob, source

    @source(name="dup_plugin", detect=Glob("*.txt"), kinds=(ItemKind.NOTE,))
    def parse1(archive: Archive, ctx: ParseContext) -> Iterator[NoteDraft]:
        notes: list[NoteDraft] = []
        yield from notes

    with pytest.raises(DuplicateSourceError, match="dup_plugin"):

        @source(name="dup_plugin", detect=Glob("*.txt"), kinds=(ItemKind.NOTE,))
        def parse2(archive: Archive, ctx: ParseContext) -> Iterator[NoteDraft]:
            notes: list[NoteDraft] = []
            yield from notes


def test_detect_source_first_hit(clean_registry: dict[str, Any]) -> None:
    from potluck.ingest.plugins import Glob, detect_source, source

    # aaa_plugin (sorted first): matches *Takeout/*.json
    @source(name="aaa_plugin", detect=Glob("*Takeout/*.json"), kinds=(ItemKind.NOTE,))
    def parse_aaa(archive: Archive, ctx: ParseContext) -> Iterator[NoteDraft]:
        notes: list[NoteDraft] = []
        yield from notes

    # zzz_plugin (sorted last): matches *Keep/*.json — overlaps with aaa_plugin on
    # members that satisfy both globs (e.g. "Takeout/Keep/x.json") to exercise
    # lexicographic tie-breaking.
    @source(name="zzz_plugin", detect=Glob("*Keep/*.json"), kinds=(ItemKind.NOTE,))
    def parse_zzz(archive: Archive, ctx: ParseContext) -> Iterator[NoteDraft]:
        notes: list[NoteDraft] = []
        yield from notes

    # Archive with a name matching aaa_plugin only; aaa_plugin wins (only match)
    archive = FakeArchive(["a.txt", "Takeout/Keep/x.json"])
    result = detect_source(archive)
    assert result is not None
    assert result.name == "aaa_plugin"

    # Tie-breaking: "Takeout/Keep/x.json" matches BOTH plugins; the
    # lexicographically smaller name (aaa_plugin < zzz_plugin) must win.
    tie_archive = FakeArchive(["Takeout/Keep/x.json"])
    tie_result = detect_source(tie_archive)
    assert tie_result is not None
    assert tie_result.name == "aaa_plugin", (
        f"Expected aaa_plugin (lex-first) but got '{tie_result.name}'"
    )

    # Archive with no matching names → None
    assert detect_source(FakeArchive(["README.md", "data.csv"])) is None


def test_detect_single_pass_early_exit(clean_registry: dict[str, Any]) -> None:
    from potluck.ingest.plugins import Glob, detect_source, source

    @source(name="early_exit_plugin", detect=Glob("match.txt"), kinds=(ItemKind.NOTE,))
    def parse(archive: Archive, ctx: ParseContext) -> Iterator[NoteDraft]:
        notes: list[NoteDraft] = []
        yield from notes

    # Matching name is first — iteration should stop after serving exactly 1 name
    counting = CountingArchive(["match.txt", "second.txt", "third.txt"])
    result = detect_source(counting)

    assert result is not None
    assert result.name == "early_exit_plugin"
    assert counting.served == 1, (
        f"Expected early exit after 1 archive name, got {counting.served} served"
    )
