"""Tests for potluck.search.fts: sanitize_query and search_items."""

import sqlite3
from pathlib import Path

import pytest

from potluck.search.fts import sanitize_query
from potluck.storage.db import connect
from potluck.storage.migrate import apply_migrations

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _open_migrated(tmp_path: Path) -> sqlite3.Connection:
    """Open a migrated write connection for fuzz tests."""
    conn = connect(tmp_path / "fuzz.db")
    apply_migrations(conn)
    return conn


# ---------------------------------------------------------------------------
# sanitize_query: basic behaviour
# ---------------------------------------------------------------------------


def test_sanitize_basic() -> None:
    """Plain words → each quoted, space-joined."""
    result = sanitize_query("hello world")
    assert result == '"hello" "world"'


def test_sanitize_neutralizes_operators() -> None:
    """FTS5 operators in user input are neutralised (each \\w+ token is quoted)."""
    cases = [
        ("a AND b", '"a" "AND" "b"'),
        ("a OR b", '"a" "OR" "b"'),
        ("NOT a", '"NOT" "a"'),
        ("NEAR(x, 2)", '"NEAR" "x" "2"'),
        ("col:val", '"col" "val"'),
        ("wild*", '"wild"'),
        ('"unbalanced', '"unbalanced"'),
        ("(paren", '"paren"'),
        ("a-b", '"a" "b"'),
    ]
    for raw, expected in cases:
        result = sanitize_query(raw)
        assert result == expected, f"sanitize_query({raw!r}) = {result!r}, expected {expected!r}"


def test_sanitize_empty_inputs() -> None:
    """Inputs with no \\w+ tokens return None."""
    for raw in ["", "   ", '"', "()", "-", "***"]:
        assert sanitize_query(raw) is None, f"Expected None for {raw!r}"


def test_sanitize_unicode() -> None:
    """Unicode \\w+ tokens are preserved."""
    result = sanitize_query("café 検索")
    assert result is not None
    # Both tokens extracted: café → café, 検索 → 検索
    assert '"café"' in result
    assert '"検索"' in result


def test_equal_score_pagination_deterministic(tmp_path: Path) -> None:
    """Equal-scoring hits are ordered by id (explicit tiebreaker): LIMIT/OFFSET
    pages are disjoint, exhaustive, and stable across requests."""
    from potluck.search.fts import search_items

    conn = _open_migrated(tmp_path)
    conn.execute("INSERT INTO sources (name) VALUES ('s')")
    conn.execute(
        """INSERT INTO imports (source_id, path, parser_version, started_at)
           VALUES (1, '/tmp/x', 1, '2024-01-01T00:00:00Z')"""
    )
    # 10 byte-identical short notes → identical bm25 scores
    for i in range(10):
        conn.execute(
            """INSERT INTO items (source_id, import_id, kind, content_hash, title, text)
               VALUES (1, 1, 'note', ?, 'pear', 'pear tree')""",
            (f"h{i}",),
        )
    conn.commit()

    pages = [
        [
            int(row["id"])
            for row in search_items(conn, match='"pear"', kinds=None, limit=4, offset=off)
        ]
        for off in (0, 4, 8)
    ]
    collected = [item_id for page in pages for item_id in page]

    assert sorted(collected) == list(range(1, 11)), "pages must be disjoint and exhaustive"
    assert collected == sorted(collected), "equal scores must fall back to id order"
    conn.close()


def test_fuzz_no_operational_error(tmp_path: Path) -> None:
    """Nasty inputs never cause sqlite3.OperationalError on MATCH."""
    nasty_inputs = [
        # FTS5 operators
        "AND OR NOT NEAR",
        "NEAR(x, 2) AND y",
        "title:value",
        "col1 col2:val",
        # Wildcards and quotes
        "* ** ***",
        '"unbalanced',
        '"""',
        '"a" AND "b"',
        # Parentheses and special chars
        "((()))",
        "(a OR b) AND (c OR d)",
        "a-b-c",
        "a+b",
        "a.b.c",
        "a/b/c",
        "a\\b",
        # Emoji
        "hello 🎉 world",
        "🎊🎈🎁",
        "café résumé naïve",
        # CJK characters
        "日本語テスト",
        "中文搜索",
        "한국어 검색",
        # Control characters
        "hello\x00world",
        "test\x01\x02\x03",
        "foo\x1fbar",
        "\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f",
        "\x10\x11\x12\x13\x14\x15\x16\x17\x18\x19\x1a\x1b\x1c\x1d\x1e\x1f",
        # Very long string
        "word " * 500,
        "a" * 10000,
        # Mixed operator attacks
        "^anchor",
        "^",
        "NOT NOT NOT",
        "a OR OR b",
        "NEAR()",
        "NEAR(a b, 5) OR c",
        # Balanced but tricky
        "(a b c)",
        "a (b c) d",
        # Null-byte sequences
        "foo\x00",
        "\x00",
        # Numbers and punctuation
        "123 456",
        "!@#$%^&*()",
        "hello, world!",
        "a...b",
        "a:::b",
        # Empty/whitespace variants
        "   a   ",
        "\t\n\r",
        # Mixed
        "AND OR NOT * ^ ( ) : \" ' \\",
    ]

    conn = _open_migrated(tmp_path)
    try:
        for raw in nasty_inputs:
            match_expr = sanitize_query(raw)
            if match_expr is None:
                continue  # No tokens → skip
            # Must not raise OperationalError
            try:
                conn.execute(
                    "SELECT snippet(items_fts, -1, '[', ']', '…', 12) "
                    "FROM items_fts WHERE items_fts MATCH ?",
                    (match_expr,),
                ).fetchall()
            except sqlite3.OperationalError as exc:
                pytest.fail(
                    f"sanitize_query({raw!r}) → {match_expr!r} caused OperationalError: {exc}"
                )
    finally:
        conn.close()
