"""Inline query language: ``from:/source:/kind:/before:/after:`` operators.

Parsing NEVER raises — search must not fail on user input. Unknown keys stay
in the free-text terms; known keys with invalid values are dropped and noted
in ``errors`` (surfaced as SearchResponse.warnings, not exceptions).
"""

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Final

from potluck.models.items import ItemKind

_KEYS: Final = frozenset({"from", "source", "kind", "before", "after"})
# key:value or key:"quoted value" — keys are case-insensitive. (?<!\S) anchors
# the key at a token boundary: a key embedded mid-token (sent-from:x) is plain
# search text, not an operator.
_OPERATOR: Final = re.compile(r'(?<!\S)(?P<key>[A-Za-z]+):(?P<value>"[^"]*"|\S+)')


@dataclass(frozen=True)
class ParsedQuery:
    """A query string split into free-text terms and typed filters.

    ``after`` is inclusive (>=), ``before`` exclusive (<) — the same
    convention as ListItemsRequest.since/until.
    """

    terms: str
    kinds: tuple[ItemKind, ...] = ()
    sources: tuple[str, ...] = ()
    from_addrs: tuple[str, ...] = ()
    before: datetime | None = None
    after: datetime | None = None
    errors: tuple[str, ...] = field(default=())


def _parse_date(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC)


def parse_query(raw: str) -> ParsedQuery:
    """Split *raw* into free-text terms and typed filters. Never raises."""
    kinds: list[ItemKind] = []
    sources: list[str] = []
    from_addrs: list[str] = []
    before: datetime | None = None
    after: datetime | None = None
    errors: list[str] = []
    term_parts: list[str] = []

    pos = 0
    for match in _OPERATOR.finditer(raw):
        term_parts.append(raw[pos : match.start()])
        pos = match.end()

        key = match.group("key").lower()
        if key not in _KEYS:
            term_parts.append(match.group(0))  # unknown operator: plain text
            continue

        value = match.group("value").strip('"').strip()
        if not value:
            errors.append(f"{key}: empty value ignored")
        elif key == "kind":
            try:
                kinds.append(ItemKind(value.lower()))
            except ValueError:
                errors.append(f"kind: unknown kind '{value}' ignored")
        elif key == "source":
            # Registered source names are lowercase with underscores
            # (gmail, google_keep) — normalize so source:"Google Keep" hits.
            sources.append(value.lower().replace(" ", "_"))
        elif key == "from":
            from_addrs.append(value.lower())
        else:  # before / after — YYYY-MM-DD, UTC midnight; last occurrence wins
            try:
                parsed_date = _parse_date(value)
            except ValueError:
                errors.append(f"{key}: expected YYYY-MM-DD, got '{value}' — ignored")
            else:
                if key == "before":
                    before = parsed_date
                else:
                    after = parsed_date

    term_parts.append(raw[pos:])
    terms = " ".join(" ".join(term_parts).split())

    return ParsedQuery(
        terms=terms,
        kinds=tuple(kinds),
        sources=tuple(sources),
        from_addrs=tuple(from_addrs),
        before=before,
        after=after,
        errors=tuple(errors),
    )
