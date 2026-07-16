"""Tests for potluck.ingest.plugins: Glob, SourcePlugin, source, discover, detect_sources."""

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
    """Minimal Archive stub for testing detect_sources."""

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


def test_glob_alternation_matches_any_of() -> None:
    """'|' separates alternative patterns (#142): export layouts legitimately
    vary per platform (WhatsApp Android vs iOS naming)."""
    from potluck.ingest.plugins import Glob

    g = Glob("*WhatsApp Chat*.txt|_chat.txt|*/_chat.txt")
    assert g.matches("WhatsApp Chat with Ada.txt") is True
    assert g.matches("_chat.txt") is True
    assert g.matches("WhatsApp Chat - Ada/_chat.txt") is True
    assert g.matches("my_chat.txt") is False
    assert g.matches("notes.txt") is False
    # single-pattern globs behave exactly as before
    assert Glob("*.json").matches("a.json") is True


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


def test_detect_sources_collects_all_matches(clean_registry: dict[str, Any]) -> None:
    """#195: combined archives (Keep+Mail) must surface EVERY matching plugin."""
    from potluck.ingest.plugins import Glob, detect_sources, source

    @source(name="zzz_mail", detect=Glob("*Mail/*.mbox"), kinds=(ItemKind.EMAIL,))
    def parse_mail(archive: Archive, ctx: ParseContext) -> Iterator[NoteDraft]:
        notes: list[NoteDraft] = []
        yield from notes

    @source(name="aaa_keep", detect=Glob("*Keep/*.json"), kinds=(ItemKind.NOTE,))
    def parse_keep(archive: Archive, ctx: ParseContext) -> Iterator[NoteDraft]:
        notes: list[NoteDraft] = []
        yield from notes

    combined = FakeArchive(
        ["Takeout/Mail/All mail.mbox", "Takeout/Keep/x.json", "Takeout/Other/y.txt"]
    )
    result = detect_sources(combined)
    # deterministic run order: sorted by plugin name
    assert [p.name for p in result] == ["aaa_keep", "zzz_mail"]

    single = FakeArchive(["Takeout/Keep/x.json"])
    assert [p.name for p in detect_sources(single)] == ["aaa_keep"]

    # Archive with no matching names -> empty list
    assert detect_sources(FakeArchive(["README.md", "data.csv"])) == []


def test_generic_flag_defaults_false_and_registers(clean_registry: dict[str, Any]) -> None:
    """#150: @source grows a generic flag; omitting it keeps the specific tier."""
    from potluck.ingest.plugins import Glob, source

    @source(name="spec_plugin", detect=Glob("*Spec/*.json"), kinds=(ItemKind.NOTE,))
    def parse_spec(archive: Archive, ctx: ParseContext) -> Iterator[NoteDraft]:
        notes: list[NoteDraft] = []
        yield from notes

    @source(name="gen_plugin", detect=Glob("*.txt"), kinds=(ItemKind.NOTE,), generic=True)
    def parse_gen(archive: Archive, ctx: ParseContext) -> Iterator[NoteDraft]:
        notes: list[NoteDraft] = []
        yield from notes

    assert clean_registry["spec_plugin"].generic is False
    assert clean_registry["gen_plugin"].generic is True


def _register_tiers(clean_registry: dict[str, Any]) -> None:
    """One specific (Keep-style) + two generic (txt/jpg) toy plugins."""
    from potluck.ingest.plugins import Glob, source

    @source(name="tier_keep", detect=Glob("*Keep/*.json"), kinds=(ItemKind.NOTE,))
    def parse_keep(archive: Archive, ctx: ParseContext) -> Iterator[NoteDraft]:
        notes: list[NoteDraft] = []
        yield from notes

    @source(name="tier_txt", detect=Glob("*.txt"), kinds=(ItemKind.NOTE,), generic=True)
    def parse_txt(archive: Archive, ctx: ParseContext) -> Iterator[NoteDraft]:
        notes: list[NoteDraft] = []
        yield from notes

    @source(name="tier_jpg", detect=Glob("*.jpg"), kinds=(ItemKind.PHOTO,), generic=True)
    def parse_jpg(archive: Archive, ctx: ParseContext) -> Iterator[NoteDraft]:
        notes: list[NoteDraft] = []
        yield from notes


def test_detect_sources_drops_generics_when_any_specific_matches(
    clean_registry: dict[str, Any],
) -> None:
    """Tier fallback (#150): a recognized export inside a messy folder wins —
    generic plugins never double-import members a specific source claims."""
    from potluck.ingest.plugins import detect_sources

    _register_tiers(clean_registry)
    mixed = FakeArchive(["Takeout/Keep/a.json", "loose-notes.txt", "photos/holiday.jpg"])
    assert [p.name for p in detect_sources(mixed)] == ["tier_keep"]


def test_detect_sources_generics_run_when_nothing_specific_matches(
    clean_registry: dict[str, Any],
) -> None:
    """A notes-only folder reaches the generic tier; every matching generic runs."""
    from potluck.ingest.plugins import detect_sources

    _register_tiers(clean_registry)
    folder = FakeArchive(["notes/a.txt", "pics/b.jpg", "misc/data.csv"])
    assert [p.name for p in detect_sources(folder)] == ["tier_jpg", "tier_txt"]

    txt_only = FakeArchive(["just-notes.txt"])
    assert [p.name for p in detect_sources(txt_only)] == ["tier_txt"]

    # Nothing matches at either tier -> empty list, unchanged.
    assert detect_sources(FakeArchive(["data.csv"])) == []


def test_registry_fingerprint_covers_generic_flag(clean_registry: dict[str, Any]) -> None:
    """The flag changes detection semantics, so it must change the fingerprint
    (#196 scan caching would otherwise serve stale tier decisions)."""
    import dataclasses

    from potluck.ingest.plugins import Glob, registry_fingerprint, source

    @source(name="fp_plugin", detect=Glob("*.txt"), kinds=(ItemKind.NOTE,), generic=True)
    def parse(archive: Archive, ctx: ParseContext) -> Iterator[NoteDraft]:
        notes: list[NoteDraft] = []
        yield from notes

    generic_fp = registry_fingerprint(dict(clean_registry))
    flipped = {
        name: dataclasses.replace(plugin, generic=False) for name, plugin in clean_registry.items()
    }
    assert registry_fingerprint(flipped) != generic_fp


def test_detect_sources_single_pass_early_exit(clean_registry: dict[str, Any]) -> None:
    """Iteration stops as soon as every registered plugin has matched."""
    from potluck.ingest.plugins import Glob, detect_sources, source

    @source(name="early_exit_plugin", detect=Glob("match.txt"), kinds=(ItemKind.NOTE,))
    def parse(archive: Archive, ctx: ParseContext) -> Iterator[NoteDraft]:
        notes: list[NoteDraft] = []
        yield from notes

    # The only registered plugin matches the first name — stop after 1 name.
    counting = CountingArchive(["match.txt", "second.txt", "third.txt"])
    result = detect_sources(counting)

    assert [p.name for p in result] == ["early_exit_plugin"]
    assert counting.served == 1, (
        f"Expected early exit after 1 archive name, got {counting.served} served"
    )
