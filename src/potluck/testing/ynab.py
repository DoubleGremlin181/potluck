"""Deterministic synthetic YNAB budget-export generator.

Ships inside ``potluck.testing`` so tests, committed fixtures, and bench
scenarios share one deterministic source. Same arguments → identical bytes on
every machine, forever. Never put real personal data here — accounts and
categories are fixture names, payees/memos come from the shared WORDS list,
and every amount is a synthetic closed-form integer.

Row shapes are modular rules of the row index ``i`` (not RNG draws), so
expected parser outcomes have exact closed forms — every generated register
row parses to exactly one draft, and the milliunit total of a whole export is
pure int arithmetic (:func:`expected_milliunit_sum` — the assertion that
catches any float drift instantly). Per row ``i`` (first rule wins):

- ``i % 10 == 9`` (i > 0) → verbatim raw copy of row ``i-1`` (identical
  bytes: exercises the parser's raw-fingerprint ``#N`` identity suffixes;
  see :func:`expected_duplicate_suffix_count`)
- payee: ``i % 4 == 1`` → transfer (``Transfer : <account>``, uncategorized);
  ``i % 11 == 7`` → empty payee; otherwise WORDS-derived
- category: empty for transfers and at ``i % 6 == 0``; otherwise a rotating
  (group, category) pair, with the redundant combined column rendered
  ``"<Group>: <Category>"`` exactly like the real export
- memo: ``i % 7 == 2`` → multiline with commas and doubled quotes (csv
  quoting edge); ``i % 5 == 3`` → plain; otherwise empty (the real export is
  ~98% empty memos)
- amount (:func:`expected_amount_milliunits`): ``i % 17 == 11`` → zero;
  ``i % 9 == 4`` → inflow ≥ $2,000 (thousands separator, quoted cell);
  ``i % 8 == 6`` → outflow ≥ $1,234.56 (ditto); otherwise an everyday
  unquoted ``$D+.DD`` outflow — the dominant real-export shape

The member set mirrors the real zip: ``<budget> as of <stamp> - Register.csv``
plus the ``… - Plan.csv`` decoy the parser must never read. The Register is
written with the real export's exact dressing: UTF-8 BOM, CRLF, all text
cells quoted, amount cells unquoted unless a thousands comma forces quoting.

Regenerate the golden fixture::

    python -c "
    from pathlib import Path
    from potluck.testing.ynab import write_ynab_export
    write_ynab_export(Path('tests/fixtures/ynab'), rows=60, seed=13, fmt='dir')
    "
"""

from datetime import date, timedelta
from pathlib import Path
from typing import Literal

from potluck.testing.archives import write_archive
from potluck.testing.generators import WORDS

_BUDGET = "Synthetic Budget"
_STAMP = "2026-01-01 20-15"
_BASE_DATE = date(2025, 1, 5)

_ACCOUNTS = ("Synth Checking", "Synth Savings", "Synth Credit")
_CATEGORIES = (
    ("Bills", "Cellphone"),
    ("Bills", "Rent"),
    ("Fun Money", "Dining Out"),
    ("Groceries", "Weekly Shop"),
)
_HEADER = (
    "Account",
    "Flag",
    "Date",
    "Payee",
    "Category Group/Category",
    "Category Group",
    "Category",
    "Memo",
    "Outflow",
    "Inflow",
    "Cleared",
)


def _is_dup(i: int) -> bool:
    return i > 0 and i % 10 == 9  # i-1 is never itself a dup (i-1 % 10 == 8)


def expected_amount_milliunits(i: int) -> int:
    """The signed milliunit amount of logical row *i* (dup rows repeat row
    ``i-1``; resolve with ``i-1`` first). Pure int arithmetic — the parser
    must reproduce every value exactly."""
    if i % 17 == 11:
        return 0
    if i % 9 == 4:
        return 2_000_000 + (i * 7_010) % 1_000_000
    if i % 8 == 6:
        return -(1_234_560 + (i % 5) * 111_110)
    return -(4_990 + (i * 3_170) % 90_000)


def _effective(i: int) -> int:
    return i - 1 if _is_dup(i) else i


def expected_item_counts(rows: int) -> dict[str, int]:
    """Items-by-kind the parser yields for one generated export (every
    register row — duplicates included, via ``#N`` — parses to one draft)."""
    return {"transaction": rows} if rows else {}


def expected_milliunit_sum(rows: int) -> int:
    """Exact SUM(amount_milliunits) over one generated export."""
    return sum(expected_amount_milliunits(_effective(i)) for i in range(rows))


def expected_duplicate_suffix_count(rows: int) -> int:
    """Rows that import with a ``#N`` external-id suffix (raw duplicates)."""
    return sum(1 for i in range(rows) if _is_dup(i))


def _words(salt: int, i: int, offset: int, k: int) -> str:
    return " ".join(WORDS[(salt + i * 7 + (offset + j) * 3) % len(WORDS)] for j in range(k))


def _quote(cell: str) -> str:
    return '"' + cell.replace('"', '""') + '"'


def _amount_cell(milliunits: int) -> str:
    """Render non-negative *milliunits* the way the real export does:
    ``$D+.DD`` unquoted, thousands commas (which force csv quoting) beyond
    $999. Generated amounts are always whole cents (2 dp)."""
    units, frac = divmod(milliunits, 1000)
    text = f"${units:,}.{frac // 10:02d}"
    return _quote(text) if "," in text else text


def _row_line(i: int, salt: int) -> str:
    """The raw Register line for logical row *i* (never called for dups)."""
    account = _ACCOUNTS[i % 3]
    rendered_date = (_BASE_DATE + timedelta(days=i // 2)).strftime("%m/%d/%Y")

    transfer = i % 4 == 1
    if transfer:
        payee = f"Transfer : {_ACCOUNTS[(i + 1) % 3]}"
    elif i % 11 == 7:
        payee = ""
    else:
        payee = _words(salt, i, 0, 2).title()

    if transfer or i % 6 == 0:
        group = category = combined = ""
    else:
        group, category = _CATEGORIES[(i // 2) % len(_CATEGORIES)]
        combined = f"{group}: {category}"

    if i % 7 == 2:
        memo = f'{_words(salt, i, 20, 3)}, with "quoted" bits\n{_words(salt, i, 40, 2)}'
    elif i % 5 == 3:
        memo = _words(salt, i, 60, 4)
    else:
        memo = ""

    amount = expected_amount_milliunits(i)
    outflow = _amount_cell(-amount if amount < 0 else 0)
    inflow = _amount_cell(amount if amount > 0 else 0)
    cleared = ("Cleared", "Reconciled", "Uncleared")[i % 3]
    flag = "Red" if i % 8 == 5 else ""

    cells = [account, flag, rendered_date, payee, combined, group, category, memo]
    return ",".join([*(_quote(c) for c in cells), outflow, inflow, _quote(cleared)])


def _register_bytes(rows: int, salt: int) -> bytes:
    lines = [",".join(_quote(name) for name in _HEADER)]
    for i in range(rows):
        lines.append(_row_line(_effective(i), salt))
    body = "\r\n".join(lines) + "\r\n"
    return ("\ufeff" + body).encode("utf-8")


def _plan_bytes(salt: int) -> bytes:
    """The Plan decoy: real header, a few rows, must never become items."""
    header = ["Month", "Category Group/Category", "Category Group", "Category"]
    lines = [
        ",".join(_quote(c) for c in [*header, "Assigned", "Activity", "Available"]),
    ]
    months = ("Aug 2025", "Sep 2025", "Oct 2025")
    for month, (group, category) in zip(months, _CATEGORIES, strict=False):
        cells = [month, f"{group}: {category}", group, category]
        lines.append(",".join([*(_quote(c) for c in cells), "$500.00", "$0.00", "$500.00"]))
    body = "\r\n".join(lines) + "\r\n"
    return ("\ufeff" + body).encode("utf-8")


def ynab_members(*, rows: int, seed: int = 42) -> dict[str, bytes]:
    """The member set of one synthetic export ({posix_name: content})."""
    salt = seed * 1009
    prefix = f"{_BUDGET} as of {_STAMP}"
    return {
        f"{prefix} - Register.csv": _register_bytes(rows, salt),
        f"{prefix} - Plan.csv": _plan_bytes(salt),
    }


def write_ynab_export(
    dest_dir: Path,
    *,
    rows: int,
    seed: int = 42,
    fmt: Literal["zip", "tgz", "dir"] = "zip",
) -> Path:
    """Materialise a synthetic YNAB export archive in *dest_dir*.

    Returns the archive path (or the directory root for ``fmt="dir"``).
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    members = ynab_members(rows=rows, seed=seed)
    if fmt == "dir":
        dest = dest_dir / "ynab-synth-001"
        write_archive(dest, members, "dir")
        return dest
    ext = "zip" if fmt == "zip" else "tgz"
    dest = dest_dir / f"ynab-synth-001.{ext}"
    write_archive(dest, members, fmt)
    return dest
