"""YNAB budget-export source plugin.

Parses the Register CSV inside the zip YNAB's "Export Budget" produces
(``<Budget name> as of <YYYY-MM-DD HH-MM> - Register.csv`` beside a
``… - Plan.csv``). Format spec (v1 authoritative; header and cell shapes
verified against a real 2026 export — v0's YNAB ingester read the same CSVs
and its semantics port: blank amount cells are zero, Outflow/Inflow collapse
to one signed amount, transfers keep their ``Transfer : …`` payee verbatim):

- Header (all cells quoted, CRLF, UTF-8 BOM): ``Account, Flag, Date, Payee,
  Category Group/Category, Category Group, Category, Memo, Outflow, Inflow,
  Cleared``.
- Text cells are quoted; amount cells are unquoted ``$D+.DD`` in the real
  export (no thousands separators observed — quoting is the csv layer's
  concern when they appear). Dates are ``MM/DD/YYYY``; Cleared is one of
  ``Uncleared``/``Cleared``/``Reconciled``; Flag is a colour name, empty when
  unflagged. ``Category Group/Category`` always equals ``"<Group>: <Category>"``
  (verified on every row of the real export) — redundant, never stored.
- The Plan CSV (category budgets per month) is NOT items and is skipped in
  v1: allocations are budget state, not personal records. v0 ingested it as
  Budget entities; that port is deliberately dropped until a use case exists
  (YAGNI, per #144).

Money discipline (#144's acceptance criterion): amounts parse to integer
milliunits with pure string/int math — no float, no rounding, ever. A cell
that cannot be represented exactly (>3 decimal places) or whose sign cannot
be trusted (accounting parentheses, DR/CR/DB markers) makes the ROW skip with
a warning: a transaction with a guessed amount — or a silently flipped sign —
is worse than a missing one. Unicode minus/dash glyphs count as the sign they
are, never as strippable dressing. Locale shapes accepted beyond
the verified ``$1,234.56``: any currency symbol/space strips away; European
``1.234,56`` (decimal comma unambiguous when both separators appear, or when
a comma tail isn't 3 digits); multi-dot grouping ``1.234.567``; lakh grouping
``12,34,567.89``. The one documented bias: a lone separator with a 3-digit
tail reads as the real export's US shape (``1,234`` = thousands, ``1.234`` =
3-dp decimal). ``amount = Inflow − Outflow`` (outflow negative).

Field placement: amount/account/payee/category/category group land in the
``transactions`` satellite (aggregation-worthy structured columns); memo and
the category path compose ``items.text`` (FTS-searchable); payee is the title
(FTS covers title at higher weight than text, so it is deliberately not
duplicated into text); Cleared status and Flag ride ``meta`` (display-grade
enums, not aggregation targets); the transaction date is ``items.ts`` (dates
carry no time or zone → midnight UTC, the plugin-wide policy for zoneless
sources). An empty Date cell is legal (undated row); a malformed one warns
and keeps the row undated — identity does not depend on it.

Identity policy (YNAB register rows have no ids):
``ynab:<sha256(budget name + raw row line(s))>`` with a first-seen ``#N``
suffix for identical rows (real: the same coffee twice a day). The
fingerprint hashes the RAW exported line(s) — never parsed values — with only
line terminators normalized (a CRLF→LF re-save must not re-mint identities;
quoted multiline memos hash all their physical lines). The budget name (the
member basename before `` as of``, stable across re-exports whose timestamped
filenames differ) anchors the hash so identical rows in two different budgets
never collide; renaming a budget re-mints its identities (documented, rare).
Occurrence counters are scoped PER MEMBER: two Register members in one
archive are re-exports of the same budget, so their copies must collide on
external_id and dedup rather than double-import.
"""

import csv
import hashlib
import logging
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Final

from pydantic import JsonValue

from potluck.ingest.identity import occurrence_suffix
from potluck.ingest.plugins import Glob, ParseContext, source
from potluck.ingest.readers import Archive
from potluck.models.drafts import TransactionDraft
from potluck.models.items import ItemKind

_logger = logging.getLogger(__name__)

# The " as of " infix and " - Register.csv" tail are both YNAB-generated, so
# the pattern is precise: generic CSVs ("Register.csv", reddit's members,
# #150's territory) can never match. '*' crosses '/', so re-zipped nested
# layouts match without extra alternatives. Plan CSVs never trigger detection.
_EXPORT_GLOB = Glob("* as of * - Register.csv")

_BUDGET_RE = re.compile(r"^(?P<budget>.+) as of .+ - Register\.csv$")

# Columns whose absence means the member is not the format we know — the
# remaining columns (Flag, Memo, Cleared, the category triple) are
# deliberately not required, so their removal in a future export generation
# stays benign.
_REQUIRED: Final = frozenset({"Account", "Date", "Payee", "Outflow", "Inflow"})

# MM/DD/YYYY is the verified real-export shape; ISO is accepted because it is
# unambiguous and costs nothing. DD/MM/YYYY is indistinguishable from
# MM/DD/YYYY row-by-row and is deliberately not guessed.
_DATE_FORMATS: Final = ("%m/%d/%Y", "%Y-%m-%d")

# Everything that is not a digit, separator, or sign is currency dressing
# ($, €, ₹, codes like "USD", spaces/NBSPs — which also erases space-style
# thousands separators).
_CURRENCY_DRESSING_RE = re.compile(r"[^0-9.,\-]")

# Sign-bearing glyphs the dressing strip must NOT eat. Unicode minus/dashes
# (U+2010–2015 dashes, U+2212 MINUS SIGN, small/fullwidth forms) normalize to
# ASCII '-' BEFORE the strip; accounting debit/credit letter markers make the
# cell unparseable — either would otherwise flip a negative positive silently.
# Any other letter run is currency dressing (codes like "USD", letter-shaped
# symbols like "kr"/"Kč") and strips away as before.
_SIGN_DASH_RE = re.compile(r"[\u2010-\u2015\u2212\ufe63\uff0d]")
_LETTER_RUN_RE = re.compile(r"[^\W\d_]+")
_SIGN_MARKERS: Final = frozenset({"dr", "cr", "db"})


def _valid_grouping(parts: list[str]) -> bool:
    """True when *parts* (an integer split on its group separator) form a
    valid grouped number: western ``1,234,567`` or lakh ``12,34,567``."""
    if len(parts) < 2 or not all(part.isdigit() for part in parts):
        return False
    if not 1 <= len(parts[0]) <= 3:
        return False
    body = parts[1:]
    if all(len(part) == 3 for part in body):
        return True
    # Lakh style: rightmost group of 3, every other group of 2.
    return len(parts[0]) <= 2 and len(body[-1]) == 3 and all(len(part) == 2 for part in body[:-1])


def _split_number(text: str) -> tuple[str, str] | None:
    """Split an unsigned numeric string into (integer digits, fraction digits).

    Resolves separator roles: with both present the LAST one is decimal and
    the other must group validly; a lone comma is grouping when it can be
    (the verified US shape) and decimal otherwise; a lone dot is decimal
    when single (US) and grouping when repeated. None = no known shape.
    """
    has_dot, has_comma = "." in text, "," in text
    if has_dot and has_comma:
        decimal_sep = "." if text.rfind(".") > text.rfind(",") else ","
        group_sep = "," if decimal_sep == "." else "."
        head, _, frac = text.rpartition(decimal_sep)
        if decimal_sep in head or not frac.isdigit():
            return None
        parts = head.split(group_sep)
        return ("".join(parts), frac) if _valid_grouping(parts) else None
    if has_comma:
        parts = text.split(",")
        if _valid_grouping(parts):
            return "".join(parts), ""
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            return parts[0], parts[1]  # grouping needs a 3-digit tail → decimal comma
        return None
    if has_dot:
        parts = text.split(".")
        if len(parts) == 2 and (parts[0].isdigit() or not parts[0]) and parts[1].isdigit():
            return parts[0] or "0", parts[1]
        return ("".join(parts), "") if _valid_grouping(parts) else None
    return (text, "") if text.isdigit() else None


def _parse_milliunits(raw: str) -> int | None:
    """Parse one amount cell to exact integer milliunits; None = unparseable.

    Pure string/int math — no float, no Decimal, no rounding. A blank cell is
    zero (older exports leave zero amounts empty; the real 2026 export renders
    ``$0.00``). More than 3 fraction digits cannot be stored exactly and is
    unparseable by definition. Sign carriers are never guessed away: unicode
    minus/dash glyphs count as ``-``, while accounting negatives — parentheses
    or DR/CR/DB letter markers — reject the cell outright (a silently flipped
    sign is worse than a skipped row).
    """
    if not raw.strip():
        return 0
    if "(" in raw or ")" in raw:
        return None
    if any(run.lower() in _SIGN_MARKERS for run in _LETTER_RUN_RE.findall(raw)):
        return None
    text = _CURRENCY_DRESSING_RE.sub("", _SIGN_DASH_RE.sub("-", raw))
    negative = text.startswith("-")
    if negative:
        text = text[1:]
    if not text or "-" in text:
        return None
    split = _split_number(text)
    if split is None:
        return None
    units, frac = split
    if len(frac) > 3:
        return None
    return (-1 if negative else 1) * (int(units or "0") * 1000 + int(frac.ljust(3, "0")))


def _parse_date(value: str, member_name: str, row_ordinal: int) -> datetime | None:
    """Resolve one Date cell; malformed values warn and become None (identity
    is the raw-row fingerprint — content must survive a bad date)."""
    if not value:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    _logger.warning(
        "ynab: unrecognized date %r on row %d in %r — stored without timestamp",
        value,
        row_ordinal,
        member_name,
    )
    return None


def _records_with_raw(text: str) -> Iterator[tuple[list[str], str]]:
    """Yield (cells, raw_block) per CSV record.

    raw_block is the record's physical line(s) exactly as exported, joined
    with ``\\n`` after terminator normalization (identity input: a CRLF→LF
    re-save must not re-mint fingerprints; quoted multiline memos span — and
    hash — all their lines). csv.reader is fed the normalized lines and pulls
    exactly the lines each record needs, so slicing by lines-consumed
    reconstructs the block. Blank lines csv.reader skips before a record are
    trimmed from its block — a hand-added blank line must not re-mint the
    identity of the row after it (a record's own first line is never empty:
    it starts with a cell, and empty lines inside a quoted memo are
    continuation lines, not block heads).
    """
    lines = [line.rstrip("\r") for line in text.split("\n")]
    if lines and lines[-1] == "":
        lines.pop()  # trailing-newline artifact

    consumed = 0

    def _feed() -> Iterator[str]:
        nonlocal consumed
        for line in lines:
            consumed += 1
            yield line + "\n"

    reader = csv.reader(_feed())
    start = 0
    for record in reader:
        block = lines[start:consumed]
        while block and not block[0]:
            block.pop(0)  # blank line(s) csv.reader skipped, not row content
        yield record, "\n".join(block)
        start = consumed


def _budget_name(member_name: str) -> str:
    """The budget name from the member basename (the identity anchor)."""
    base = member_name.rsplit("/", 1)[-1]
    match = _BUDGET_RE.match(base)
    return match["budget"] if match else base.removesuffix(".csv")


def _parse_register(data: bytes, member_name: str) -> Iterator[TransactionDraft]:
    """Yield TransactionDrafts from one Register member.

    CSV discipline: utf-8 with BOM tolerance, quoted multiline memos survive
    via stdlib csv, a header missing required columns logs one WARNING and
    skips the member (a renamed/foreign header must never misparse silently),
    csv errors are contained per member (rows already yielded stand). A
    header-only member yields nothing silently — an empty register is a
    legitimate budget state. Occurrence counters are member-scoped (module
    docstring: re-exports must dedup, not double-import).
    """
    text = data.decode("utf-8-sig", errors="replace")
    budget = _budget_name(member_name)
    counters: dict[str, int] = {}
    records = _records_with_raw(text)
    try:
        header = next(records, None)
        if header is None:
            _logger.warning("ynab: %r is empty — member skipped", member_name)
            return
        columns = {name.strip(): i for i, name in enumerate(header[0])}
        missing = _REQUIRED - columns.keys()
        if missing:
            _logger.warning(
                "ynab: %r is missing expected column(s) %s — member skipped",
                member_name,
                sorted(missing),
            )
            return

        for ordinal, (cells, raw_block) in enumerate(records, start=2):
            if not any(cell.strip() for cell in cells):
                continue  # blank line, not a row

            def cell(name: str, *, cells: list[str] = cells) -> str:
                index = columns.get(name, len(cells))
                return cells[index].strip() if index < len(cells) else ""

            outflow = _parse_milliunits(cell("Outflow"))
            inflow = _parse_milliunits(cell("Inflow"))
            if outflow is None or inflow is None:
                _logger.warning(
                    "ynab: unparseable amount (outflow=%r, inflow=%r) on row %d in %r "
                    "— row skipped, money is never guessed",
                    cell("Outflow"),
                    cell("Inflow"),
                    ordinal,
                    member_name,
                )
                continue

            fingerprint = hashlib.sha256(
                f"{budget}\x1e{raw_block}".encode("utf-8", errors="replace")
            ).hexdigest()
            suffix = occurrence_suffix(counters, fingerprint)

            payee = cell("Payee") or None
            category = cell("Category") or None
            category_group = cell("Category Group") or None
            category_path = ": ".join(p for p in (category_group, category) if p)
            memo = cell("Memo")
            text_parts = [part for part in (memo, category_path) if part]

            meta: dict[str, JsonValue] = {"budget": budget}
            if cleared := cell("Cleared"):
                meta["cleared"] = cleared
            if flag := cell("Flag"):
                meta["flag"] = flag

            yield TransactionDraft(
                external_id=f"ynab:{fingerprint}{suffix}",
                ts=_parse_date(cell("Date"), member_name, ordinal),
                title=payee,
                text="\n".join(text_parts) or None,
                amount_milliunits=inflow - outflow,
                account=cell("Account") or None,
                payee=payee,
                category=category,
                category_group=category_group,
                meta=meta,
            )
    except csv.Error as exc:
        _logger.warning("ynab: CSV error in %r: %s — remaining rows skipped", member_name, exc)


@source(
    name="ynab",
    detect=_EXPORT_GLOB,
    kinds=(ItemKind.TRANSACTION,),
    parser_version=1,
)
def parse(archive: Archive, ctx: ParseContext) -> Iterator[TransactionDraft]:
    """Yield TransactionDrafts from every Register member, one streaming pass.

    A single ``*.csv`` pattern pass keeps tar archives sequential; the Plan
    member (and anything else) is skipped unopened, so memory is bounded by
    one Register file (a decade-heavy budget is a few MB of CSV). ctx is part
    of the plugin contract but unused: there is nothing to parallelize.
    """
    for member, stream in archive.iter_members("*.csv"):
        if not _EXPORT_GLOB.matches(member.name):
            continue
        yield from _parse_register(stream.read(), member.name)
