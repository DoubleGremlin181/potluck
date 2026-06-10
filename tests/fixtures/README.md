# Fixture policy

Everything in this directory MUST be output of the `potluck.testing`
generators (deterministic seeds, synthetic content). Never copy real exports
here — not even "anonymized" ones. Real exports are consulted locally for
*shape* only.

Rules, enforced by `scripts/check_fixtures.py` in pre-commit and CI:

- Email addresses only under `@potluck.test` or `@example.com`
- No phone-number-shaped strings
- No files over 1 MiB
- Raw export shapes (`Takeout/`, `*.mbox`, `*.zip` outside this tree) are
  gitignored as a second line of defense

From P1 onward every ingester ships its own fixture generator; regenerate via
the generator referenced by the tests that consume the fixture.
