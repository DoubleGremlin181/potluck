"""Tests for the YNAB budget-export source plugin.

Testing private helpers (_parse_milliunits, _parse_register) is intentional:
the money discipline (integer milliunits, no float anywhere), the raw-row
identity policy, and the CSV discipline are the public contract of this
module and must be covered at the unit level, from synthetic bytes.

Column headers here mirror the real 2026 YNAB export (shape only — all row
content is synthetic).
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest

from potluck.ingest.plugins import ParseContext, detect_sources, discover
from potluck.ingest.readers import open_archive
from potluck.ingest.sources.ynab import _parse_milliunits, _parse_register, parse
from potluck.models.drafts import TransactionDraft
from potluck.models.items import ItemKind
from potluck.testing.archives import write_archive

_HEADER = (
    '"Account","Flag","Date","Payee","Category Group/Category","Category Group",'
    '"Category","Memo","Outflow","Inflow","Cleared"\n'
)

_MEMBER = "My Synth Budget as of 2026-01-01 20-15 - Register.csv"


def _row(
    *,
    account: str = "Synth Checking",
    flag: str = "",
    date: str = "12/31/2025",
    payee: str = "Corner Bakery",
    group: str = "Fun Money",
    category: str = "Dining Out",
    memo: str = "",
    outflow: str = "$4.99",
    inflow: str = "$0.00",
    cleared: str = "Cleared",
) -> str:
    combined = f"{group}: {category}" if group or category else ""
    cells = [account, flag, date, payee, combined, group, category, memo]
    quoted = ",".join('"' + c.replace('"', '""') + '"' for c in cells)
    # Real-export shape: amount cells are unquoted — unless they contain a
    # comma, where csv rules force quoting.
    amounts = ",".join(f'"{a}"' if "," in a else a for a in (outflow, inflow))
    return f'{quoted},{amounts},"{cleared}"\n'


def _drafts(csv_text: str, member: str = _MEMBER) -> list[TransactionDraft]:
    return list(_parse_register(csv_text.encode(), member))


# ---------------------------------------------------------------------------
# Money discipline (THE acceptance criterion): decimal strings → integer
# milliunits with no float anywhere. Every expectation is exact.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # The real export's shape: "$D+.DD", unquoted, no thousands separators.
        ("$0.00", 0),
        ("$177.14", 177140),
        ("$35.00", 35000),
        # Thousands separators (quoting is the csv layer's job, not ours).
        ("$1,234.56", 1234560),
        ("$12,345,678.90", 12345678900),
        # Signs: leading minus, before or after the symbol.
        ("-$5.00", -5000),
        ("$-5.00", -5000),
        # Unicode minus/dash glyphs are SIGNS, never dressing — stripping
        # them would silently flip a negative positive.
        ("−5.00", -5000),  # U+2212 MINUS SIGN
        ("–5.00", -5000),  # U+2013 EN DASH
        ("€−1,234.56", -1234560),
        # Blank cells are zero (older exports leave zero amounts empty).
        ("", 0),
        ("   ", 0),
        # Symbol-less and fraction-only shapes.
        ("1234.56", 1234560),
        ("0.5", 500),
        (".50", 500),
        ("7", 7000),
        # Milliunit precision: YNAB is exact to 3 decimal places.
        ("$1.234", 1234),
        # Other currency symbols and space/NBSP separators vanish in the strip
        # — including letter-shaped symbols (kr, Kč) and alphabetic codes.
        ("€3.50", 3500),
        ("₹99.00", 99000),
        ("kr 5,00", 5000),
        ("USD 7.25", 7250),
        ("1 234,56", 1234560),
        # European style: both separators present makes the decimal comma
        # unambiguous; comma with a non-3-digit tail can only be a decimal.
        ("1.234,56", 1234560),
        ("€1.234.567,89", 1234567890),
        ("0,50", 500),
        ("1,5", 1500),
        ("12,34", 12340),
        # Multiple dots can only be grouping.
        ("1.234.567", 1234567000),
        # Lakh grouping (en-IN budgets): rightmost group of 3, then 2s.
        ("₹12,34,567.89", 1234567890),
        ("1,23,456.00", 123456000),
        # A lone comma with a 3-digit tail is grouping (the US shape the real
        # export uses); documented bias over the 3-dp decimal reading.
        ("1,234", 1234000),
    ],
)
def test_parse_milliunits_exact(raw: str, expected: int) -> None:
    assert _parse_milliunits(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "abc",  # no digits at all
        "$",  # symbol only
        "1-2",  # interior minus
        "5.00-",  # trailing minus
        "(1.23)",  # accounting negative: never guess a sign
        "5.00 DR",  # debit marker: sign-bearing letters, never dressing
        "5.00 CR",  # credit marker, ditto
        "cr 5.00",  # ditto in any case or position ("kr" stays a currency)
        "5.00db",  # ditto without a space
        "1..2",  # doubled separator
        "12,34,56",  # 2-digit groups without a 3-digit tail are no known format
        "$1.2345",  # >3 decimal places cannot be stored exactly
        "1,2345",  # ditto with a decimal comma
    ],
)
def test_parse_milliunits_rejects_inexact_and_malformed(raw: str) -> None:
    assert _parse_milliunits(raw) is None


def test_outflow_becomes_negative_amount() -> None:
    [d] = _drafts(_HEADER + _row(outflow="$177.14", inflow="$0.00"))
    assert d.amount_milliunits == -177140


def test_inflow_becomes_positive_amount() -> None:
    [d] = _drafts(_HEADER + _row(outflow="$0.00", inflow="$2,000.00"))
    assert d.amount_milliunits == 2000000


def test_zero_amount_row_is_kept() -> None:
    """Starting-balance style rows carry $0.00 both ways and are real rows."""
    [d] = _drafts(_HEADER + _row(outflow="$0.00", inflow="$0.00"))
    assert d.amount_milliunits == 0


def test_both_flows_populated_net_to_one_signed_amount() -> None:
    """Never observed in the real export (0 of 4051 rows), but defined:
    amount = Inflow − Outflow, exact to the milliunit."""
    [d] = _drafts(_HEADER + _row(outflow="$5.00", inflow="$2.00"))
    assert d.amount_milliunits == -3000


def test_unparseable_amount_skips_row_with_warning(caplog: pytest.LogCaptureFixture) -> None:
    """A transaction must never exist with a guessed amount — the row is
    dropped loudly, and its neighbours survive."""
    text = _HEADER + _row(outflow="$oops", payee="Bad Row") + _row(payee="Good Row")
    with caplog.at_level(logging.WARNING):
        drafts = _drafts(text)
    assert [d.payee for d in drafts] == ["Good Row"]
    assert any("amount" in r.message for r in caplog.records)


def test_large_amount_survives_exactly() -> None:
    """The classic float trap: 0.1 + 0.2 style drift can never appear."""
    [d] = _drafts(_HEADER + _row(outflow="$0.00", inflow="$9,007,199.25"))
    assert d.amount_milliunits == 9007199250


# ---------------------------------------------------------------------------
# Row mapping
# ---------------------------------------------------------------------------


def test_basic_row_mapping() -> None:
    [d] = _drafts(
        _HEADER
        + _row(
            account="Synth Checking",
            date="12/31/2025",
            payee="Corner Bakery",
            group="Fun Money",
            category="Dining Out",
            memo="team breakfast",
            outflow="$17.50",
            inflow="$0.00",
            cleared="Reconciled",
        )
    )
    assert d.kind is ItemKind.TRANSACTION
    assert d.ts == datetime(2025, 12, 31, tzinfo=UTC)
    assert d.title == "Corner Bakery"
    assert d.text == "team breakfast\nFun Money: Dining Out"
    assert d.amount_milliunits == -17500
    assert d.account == "Synth Checking"
    assert d.payee == "Corner Bakery"
    assert d.category == "Dining Out"
    assert d.category_group == "Fun Money"
    assert d.meta == {"budget": "My Synth Budget", "cleared": "Reconciled"}


def test_empty_payee_category_memo_become_none() -> None:
    [d] = _drafts(_HEADER + _row(payee="", group="", category="", memo=""))
    assert d.title is None
    assert d.payee is None
    assert d.category is None
    assert d.category_group is None
    assert d.text is None


def test_flag_lands_in_meta_only_when_present() -> None:
    [flagged] = _drafts(_HEADER + _row(flag="Red"))
    [plain] = _drafts(_HEADER + _row())
    assert flagged.meta["flag"] == "Red"
    assert "flag" not in plain.meta


def test_transfer_payee_kept_verbatim() -> None:
    [d] = _drafts(_HEADER + _row(payee="Transfer : Synth Savings", group="", category=""))
    assert d.payee == "Transfer : Synth Savings"
    assert d.title == "Transfer : Synth Savings"


def test_multiline_memo_survives_and_text_composes() -> None:
    memo = 'first line, with "quotes"\nsecond line'
    [d] = _drafts(_HEADER + _row(memo=memo))
    assert d.text == f"{memo}\nFun Money: Dining Out"


def test_unparseable_date_keeps_row_without_ts_and_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Identity is the raw-row fingerprint — content must survive a bad date."""
    with caplog.at_level(logging.WARNING):
        [d] = _drafts(_HEADER + _row(date="soonish"))
    assert d.ts is None
    assert d.amount_milliunits == -4990
    assert any("date" in r.message for r in caplog.records)


def test_iso_date_is_accepted() -> None:
    [d] = _drafts(_HEADER + _row(date="2025-12-31"))
    assert d.ts == datetime(2025, 12, 31, tzinfo=UTC)


def test_empty_date_yields_no_ts_and_no_warning(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        [d] = _drafts(_HEADER + _row(date=""))
    assert d.ts is None
    assert not caplog.records


def test_bom_and_crlf_are_tolerated() -> None:
    text = "\ufeff" + _HEADER.replace("\n", "\r\n") + _row().replace("\n", "\r\n")
    [d] = _drafts(text)
    assert d.account == "Synth Checking"


# ---------------------------------------------------------------------------
# Identity: raw-row fingerprint + first-seen #N
# ---------------------------------------------------------------------------


def test_identity_is_budget_anchored_raw_row_fingerprint() -> None:
    row = _row()
    [d] = _drafts(_HEADER + row)
    expected = hashlib.sha256(f"My Synth Budget\x1e{row[:-1]}".encode()).hexdigest()
    assert d.external_id == f"ynab:{expected}"


def test_identical_rows_get_first_seen_suffixes() -> None:
    """Raw-identical rows are REAL (same coffee twice a day) — both import."""
    row = _row()
    drafts = _drafts(_HEADER + row + row + row)
    eids = [d.external_id or "" for d in drafts]
    assert "#" not in eids[0]  # first sighting is the bare fingerprint
    assert eids[1] == eids[0] + "#2"
    assert eids[2] == eids[0] + "#3"


def test_fingerprint_uses_raw_bytes_not_parsed_values() -> None:
    """Formatting-only differences ($4.99 vs $4.990 — same milliunits) are
    DIFFERENT raw rows, so they get distinct identities, never a #N pair."""
    drafts = _drafts(_HEADER + _row(outflow="$4.99") + _row(outflow="$4.990"))
    assert drafts[0].amount_milliunits == drafts[1].amount_milliunits == -4990
    eids = {d.external_id for d in drafts}
    assert len(eids) == 2
    assert not any((e or "").endswith("#2") for e in eids)


def test_line_ending_style_does_not_change_identity() -> None:
    """A re-saved export (CRLF → LF) must not re-mint every identity."""
    crlf = (_HEADER + _row()).replace("\n", "\r\n")
    lf = _HEADER + _row()
    [a] = _drafts(crlf)
    [b] = _drafts(lf)
    assert a.external_id == b.external_id


def test_blank_line_before_row_does_not_change_its_identity() -> None:
    """csv skips blank lines; they must not leak into the NEXT row's raw
    fingerprint (a hand-added blank line must not re-mint what follows)."""
    [plain] = _drafts(_HEADER + _row())
    [after_blank] = _drafts(_HEADER + "\n" + _row())
    assert plain.external_id == after_blank.external_id


def test_same_budget_reexported_twice_in_one_archive_dedups(tmp_path: Path) -> None:
    """Two Register members with the SAME budget name are re-exports of one
    budget: per-member counters give both copies identical external_ids, so
    the engine dedups them instead of double-importing."""
    body = (_HEADER + _row() + _row()).encode()
    members = {
        "My Budget as of 2026-01-01 20-15 - Register.csv": body,
        "My Budget as of 2026-02-01 09-30 - Register.csv": body,
    }
    archive = write_archive(tmp_path / "export.zip", members, "zip")
    drafts = list(parse(open_archive(archive), ParseContext()))
    assert len(drafts) == 4
    assert len({d.external_id for d in drafts}) == 2


def test_different_budgets_never_collide() -> None:
    """The budget anchor keeps byte-identical rows in two budgets distinct."""
    row = _row()
    [a] = _drafts(_HEADER + row, "Budget A as of 2026-01-01 20-15 - Register.csv")
    [b] = _drafts(_HEADER + row, "Budget B as of 2026-01-01 20-15 - Register.csv")
    assert a.external_id != b.external_id


# ---------------------------------------------------------------------------
# CSV discipline
# ---------------------------------------------------------------------------


def test_member_with_missing_required_columns_is_skipped_with_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A renamed/foreign header must never misparse silently."""
    with caplog.at_level(logging.WARNING):
        drafts = _drafts('"Konto","Datum","Betrag"\n"a","1/1/2025","$1.00"\n')
    assert drafts == []
    assert any("column" in r.message for r in caplog.records)


def test_header_only_member_yields_nothing_without_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An empty register is a legitimate budget state, not a parse failure."""
    with caplog.at_level(logging.WARNING):
        assert _drafts(_HEADER) == []
    assert not caplog.records


def test_blank_trailing_lines_are_skipped_silently(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        drafts = _drafts(_HEADER + _row() + "\n\n")
    assert len(drafts) == 1
    assert not caplog.records


def test_missing_optional_columns_stay_benign() -> None:
    """Only Account/Date/Payee/Outflow/Inflow are required; a future export
    generation dropping Flag/Memo/Cleared must keep parsing."""
    header = '"Account","Date","Payee","Outflow","Inflow"\n'
    row = '"Synth Checking","12/31/2025","Corner Bakery",$4.99,$0.00\n'
    [d] = _drafts(header + row)
    assert d.amount_milliunits == -4990
    assert d.category is None
    assert d.meta == {"budget": "My Synth Budget"}


# ---------------------------------------------------------------------------
# Detection + parse() over archives
# ---------------------------------------------------------------------------


def test_detection_matches_export_layout_precisely(tmp_path: Path) -> None:
    matches = {
        # The real zip's member shape, flat or re-zipped one level down.
        "My Budget as of 2026-01-01 20-15 - Register.csv": True,
        "wrapper/My Budget as of 2026-01-01 20-15 - Register.csv": True,
        # The Plan CSV is out of scope and must not trigger detection alone.
        "My Budget as of 2026-01-01 20-15 - Plan.csv": False,
        # Generic names NEVER detect: the generic CSV ingester's (#150) and
        # other CSV exports' (reddit #143) territory.
        "Register.csv": False,
        "My Budget - Register.csv": False,
        "posts.csv": False,
        "comments.csv": False,
        "saved_posts.csv": False,
        "subscribed_subreddits.csv": False,
        "My Budget as of 2026-01-01 20-15 - Register.csv.bak": False,
    }
    plugin = discover()["ynab"]
    for name, expected in matches.items():
        assert plugin.detect.matches(name) is expected, name

    members = {"My Budget as of 2026-01-01 20-15 - Register.csv": _HEADER.encode()}
    archive = write_archive(tmp_path / "export.zip", members, "zip")
    assert [p.name for p in detect_sources(open_archive(archive))] == ["ynab"]


def test_parse_reads_register_and_skips_plan(tmp_path: Path) -> None:
    """The Plan CSV (category budgets per month) is not items — v1 skips it."""
    members = {
        "My Budget as of 2026-01-01 20-15 - Register.csv": (_HEADER + _row()).encode(),
        "My Budget as of 2026-01-01 20-15 - Plan.csv": (
            b'"Month","Category Group/Category","Category Group","Category",'
            b'"Assigned","Activity","Available"\n'
            b'"Aug 2025","Bills: Rent","Bills","Rent",$1000.00,$0.00,$1000.00\n'
        ),
    }
    archive = write_archive(tmp_path / "export.zip", members, "zip")
    drafts = list(parse(open_archive(archive), ParseContext()))
    assert len(drafts) == 1
    assert isinstance(drafts[0], TransactionDraft)
    assert drafts[0].payee == "Corner Bakery"


def test_parse_handles_nested_layout(tmp_path: Path) -> None:
    members = {
        "wrapper/My Budget as of 2026-01-01 20-15 - Register.csv": (_HEADER + _row()).encode()
    }
    archive = write_archive(tmp_path / "export.zip", members, "zip")
    assert [p.name for p in detect_sources(open_archive(archive))] == ["ynab"]
    drafts = list(parse(open_archive(archive), ParseContext()))
    assert len(drafts) == 1
    assert drafts[0].meta["budget"] == "My Budget"


def test_parse_empty_archive_yields_nothing(tmp_path: Path) -> None:
    archive = write_archive(tmp_path / "empty.zip", {"decoy/readme.md": b"x"}, "zip")
    assert list(parse(open_archive(archive), ParseContext())) == []
