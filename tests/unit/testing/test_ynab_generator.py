"""Deterministic YNAB export generator (potluck.testing.ynab)."""

import csv
import io
from pathlib import Path

from potluck.testing.ynab import (
    expected_duplicate_suffix_count,
    expected_item_counts,
    expected_milliunit_sum,
    write_ynab_export,
    ynab_members,
)


def test_same_arguments_produce_identical_bytes() -> None:
    assert ynab_members(rows=24, seed=5) == ynab_members(rows=24, seed=5)


def test_different_seeds_produce_different_registers() -> None:
    a = ynab_members(rows=24, seed=5)
    b = ynab_members(rows=24, seed=6)
    [register_a] = [v for k, v in a.items() if k.endswith("- Register.csv")]
    [register_b] = [v for k, v in b.items() if k.endswith("- Register.csv")]
    assert register_a != register_b


def test_member_set_mirrors_real_export_shape() -> None:
    members = ynab_members(rows=4)
    assert sorted(members) == [
        "Synthetic Budget as of 2026-01-01 20-15 - Plan.csv",
        "Synthetic Budget as of 2026-01-01 20-15 - Register.csv",
    ]
    for content in members.values():
        assert content.startswith("﻿".encode())  # BOM, like the real zip
        assert b"\r\n" in content  # CRLF line endings


def test_register_rows_and_duplicates_match_closed_forms() -> None:
    rows = 40
    members = ynab_members(rows=rows, seed=7)
    [register] = [v for k, v in members.items() if k.endswith("- Register.csv")]
    records = list(csv.reader(io.StringIO(register.decode("utf-8-sig"), newline="")))
    header, data = records[0], records[1:]
    assert header[0] == "Account"
    assert len(header) == 11
    assert len(data) == rows

    dup_indices = [i for i in range(rows) if i > 0 and i % 10 == 9]
    assert len(dup_indices) == expected_duplicate_suffix_count(rows)
    for i in dup_indices:
        assert data[i] == data[i - 1]  # verbatim copies (raw-identity input)

    # The combined column always restates "Group: Category", like every row
    # of the real export.
    for record in data:
        combined, group, category = record[4], record[5], record[6]
        assert combined == (f"{group}: {category}" if group or category else "")


def test_closed_forms_are_pure_ints() -> None:
    assert expected_item_counts(0) == {}
    assert expected_item_counts(60) == {"transaction": 60}
    assert isinstance(expected_milliunit_sum(60), int)
    # A sum over zero rows is zero; adding rows moves it by exact amounts.
    assert expected_milliunit_sum(0) == 0
    assert expected_milliunit_sum(1) == -4990


def test_write_export_dir_layout(tmp_path: Path) -> None:
    root = write_ynab_export(tmp_path, rows=6, seed=3, fmt="dir")
    assert root.name == "ynab-synth-001"
    names = sorted(p.name for p in root.iterdir())
    assert names == [
        "Synthetic Budget as of 2026-01-01 20-15 - Plan.csv",
        "Synthetic Budget as of 2026-01-01 20-15 - Register.csv",
    ]
