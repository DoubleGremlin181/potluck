"""PII guard: seeded violations are caught; clean/synthetic content passes."""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GUARD = REPO_ROOT / "scripts" / "check_fixtures.py"


def _run(root: Path) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(GUARD), str(root)],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode, proc.stdout


def test_clean_synthetic_tree_passes(tmp_path: Path) -> None:
    (tmp_path / "note.json").write_text(
        '{"author": "user@potluck.test", "cc": "a@example.com", "text": "hello basil"}'
    )
    code, out = _run(tmp_path)
    assert code == 0, out


def test_disallowed_email_caught(tmp_path: Path) -> None:
    (tmp_path / "bad.json").write_text('{"from": "someone@gmail.com"}')
    code, out = _run(tmp_path)
    assert code == 1
    assert "disallowed email" in out


def test_phone_number_caught(tmp_path: Path) -> None:
    (tmp_path / "bad.txt").write_text("call me at +14155550123 soon")
    code, out = _run(tmp_path)
    assert code == 1
    assert "phone" in out


def test_separator_phone_format_caught(tmp_path: Path) -> None:
    (tmp_path / "bad2.txt").write_text("dial (415) 555-0123 today")
    code, out = _run(tmp_path)
    assert code == 1
    assert "phone" in out


def test_oversized_file_caught(tmp_path: Path) -> None:
    (tmp_path / "big.bin").write_bytes(b"x" * (1024 * 1024 + 1))
    code, out = _run(tmp_path)
    assert code == 1
    assert "bytes" in out


def test_timestamps_and_plain_ints_do_not_trip_phone_rule(tmp_path: Path) -> None:
    (tmp_path / "ok.json").write_text(
        '{"ts": "2020-01-01T00:05:00+00:00", "id": 4155550123, "lat": 37.774929}'
    )
    code, out = _run(tmp_path)
    assert code == 0, out


def test_missing_dir_passes(tmp_path: Path) -> None:
    code, _ = _run(tmp_path / "does-not-exist")
    assert code == 0


def test_real_fixtures_dir_is_clean() -> None:
    code, out = _run(REPO_ROOT / "tests" / "fixtures")
    assert code == 0, out
