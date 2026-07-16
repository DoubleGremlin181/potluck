-- rm/forget data lifecycle (#153).
--
-- suppressed_hashes: content hashes the user has FORGOTTEN. The ingest
-- engine anti-joins every batch against this table, so forgotten content can
-- never re-ingest — not from a re-import of the same archive, not from a
-- fresh export that still contains it. Global by design (no source column):
-- the content hash covers kind, external_id, full content and every
-- satellite field, so identical hashes under two sources describe the same
-- logical content — forgetting it in one place means forgetting it, and a
-- per-source scope would silently resurrect the content via the other source.
CREATE TABLE suppressed_hashes (
    content_hash  TEXT PRIMARY KEY,
    suppressed_at TEXT NOT NULL
) STRICT;

-- Suppressed drafts get their own ledger counter, surfaced in status/UI —
-- never silently folded into items_skipped (the user asked for this content
-- to stay gone; the run must say so). Historical rows default to 0.
ALTER TABLE imports ADD COLUMN items_suppressed INTEGER NOT NULL DEFAULT 0;
