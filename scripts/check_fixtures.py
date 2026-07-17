#!/usr/bin/env python3
"""PII guard for committed fixtures (pre-commit + CI).

``tests/fixtures/`` may contain ONLY synthetic output of ``potluck.testing``
generators. This guard rejects:

- email addresses outside the allowed synthetic domains
  (``@potluck.test``, ``@example.com``)
- phone-number-shaped strings (``+``-prefixed digit runs, or 3-3-4 separator
  formats; plain integers and ISO timestamps do not trip it)
- files larger than 1 MiB (raw exports do not belong in the repo)

Binary members (the generated media fixtures of #149) are NOT skipped
wholesale: their printable-ASCII runs — where EXIF/XMP-style embedded
metadata lives — are scanned with the same email/phone rules, so a real
photo dropped into fixtures cannot slip through just by being binary.

Exit 0 = clean, 1 = violations (listed on stdout). Stdlib only — it must run
before any dependency is installed.
"""

import argparse
import re
import sys
from collections.abc import Iterator
from pathlib import Path

MAX_SIZE_BYTES = 1024 * 1024
ALLOWED_EMAIL_DOMAINS = ("potluck.test", "example.com")

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
# Phones require an explicit + prefix or 3-3-4 separators so synthetic
# timestamps, IDs, and coordinates never trip the guard.
PHONE_RES = (
    re.compile(r"(?<![\w.+])\+\d{7,15}(?!\w)"),
    re.compile(r"(?<![\w.])\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}(?![\w.])"),
)
# Printable-ASCII runs inside binary members: long enough to hold an email
# or phone, short enough not to miss metadata packed between raw bytes.
ASCII_RUN_RE = re.compile(rb"[ -~]{6,}")


def _segment_violations(segment: str, where: str) -> Iterator[str]:
    """The email/phone rules for one text segment (a line or an ASCII run)."""
    for match in EMAIL_RE.finditer(segment):
        domain = match.group(1).lower()
        allowed = any(domain == d or domain.endswith("." + d) for d in ALLOWED_EMAIL_DOMAINS)
        if not allowed:
            yield f"{where}: disallowed email address ({match.group(0)})"
    for phone_re in PHONE_RES:
        if phone_re.search(segment):
            yield f"{where}: phone-number-like string"


def iter_violations(root: Path) -> Iterator[str]:
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name == "README.md":
            continue
        size = path.stat().st_size
        if size > MAX_SIZE_BYTES:
            yield f"{path}: file is {size} bytes (max {MAX_SIZE_BYTES})"
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            # Binary member: scan its printable-ASCII runs (embedded EXIF/XMP
            # metadata) instead of skipping — see the module docstring.
            for run in ASCII_RUN_RE.finditer(path.read_bytes()):
                yield from _segment_violations(
                    run.group().decode("ascii"), f"{path}: binary metadata"
                )
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            yield from _segment_violations(line, f"{path}:{lineno}")


def main(root: Path | None = None) -> int:
    target = root if root is not None else Path("tests/fixtures")
    if not target.exists():
        return 0
    violations = list(iter_violations(target))
    for violation in violations:
        print(violation)
    if violations:
        print(f"\n{len(violations)} fixture policy violation(s); see tests/fixtures/README.md")
        return 1
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PII guard for committed fixtures.")
    parser.add_argument("root", nargs="?", type=Path, default=None)
    sys.exit(main(parser.parse_args().root))
