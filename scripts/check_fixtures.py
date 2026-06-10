#!/usr/bin/env python3
"""PII guard for committed fixtures (pre-commit + CI).

``tests/fixtures/`` may contain ONLY synthetic output of ``potluck.testing``
generators. This guard rejects:

- email addresses outside the allowed synthetic domains
  (``@potluck.test``, ``@example.com``)
- phone-number-shaped strings (``+``-prefixed digit runs, or 3-3-4 separator
  formats; plain integers and ISO timestamps do not trip it)
- files larger than 1 MiB (raw exports do not belong in the repo)

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
            continue  # binary content is governed by the size rule
        for lineno, line in enumerate(text.splitlines(), start=1):
            for match in EMAIL_RE.finditer(line):
                domain = match.group(1).lower()
                allowed = any(
                    domain == d or domain.endswith("." + d) for d in ALLOWED_EMAIL_DOMAINS
                )
                if not allowed:
                    yield f"{path}:{lineno}: disallowed email address ({match.group(0)})"
            for phone_re in PHONE_RES:
                if phone_re.search(line):
                    yield f"{path}:{lineno}: phone-number-like string"


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
