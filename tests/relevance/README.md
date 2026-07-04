# Relevance mini-eval

~30 golden queries (`golden_queries.py`) run against a deterministic synthetic
corpus (400 Keep notes + 600 Gmail messages, seed 7) on every CI run. They are
the guardrail for FTS5 ranking quality: BM25 weight changes, tokenizer tweaks,
or query-builder refactors that break "obviously right" results fail here with
the full ranked list printed for debugging.

## Adding a golden query

1. Find ground truth in the GENERATORS, never in search output: inspect
   `potluck.testing.keep.synthetic_keep_notes(400, seed=7)` /
   `potluck.testing.mbox.synthetic_mbox_messages(600, seed=7)` for distinctive
   content (three-word titles are near-unique; single words appear ~30-40×).
2. Add a `GoldenQuery` to `GOLDEN`:
   - `expect_titles`: any-of title match in the top `k` (use for Keep notes —
     their external_ids are member-path-derived and unwieldy — and for email
     threads where any `Re:` member satisfies the intent).
   - `expect_external_ids`: any-of (`mid:synth-<seed>-<index>@potluck.test`
     for Gmail — deterministic by index unless the message is in the ~4%
     missing-Message-ID slice, which gets a `noid:` fingerprint).
   - `prefix=True` for search-as-you-type queries; `k` defaults to 5.
3. Run `uv run pytest tests/relevance/` — a failing entry prints the ranked
   results so you can tell a bad expectation from a bad ranking.

The corpus is built once per session (per xdist worker) in a tmp dir; growing
it (or the suite) is cheap. Extend the suite in every search phase (P5 hybrid
ranking will reuse these queries as the FTS baseline).
