-- Drive Takeout auto-pull tracking (#152, decision doc §4): which Drive file
-- ids the puller already downloaded, so daily cycles skip them. This is
-- bandwidth-saving, not correctness — the content-hash ledger short-circuits
-- re-imported bytes, so a lost row merely costs one re-download.
--
-- A tiny table, NOT an app_settings key: the KV holds JSON scalars with
-- absence-as-null semantics, while pruning (§6) needs per-file durable state
-- (pruned_at) plus a queryable join against the imports runs. Rows are ~6
-- export sets a year × a handful of parts.
--
-- No token material EVER lands here (acceptance: secrets never in the DB) —
-- the OAuth token lives in a 0600 file under config_dir().
CREATE TABLE gdrive_pulls (
    file_id    TEXT PRIMARY KEY,  -- Drive file id
    name       TEXT NOT NULL,     -- takeout-....zip part file name
    md5        TEXT,              -- Drive md5Checksum at pull time (integrity check)
    set_stem   TEXT NOT NULL,     -- parse_part_name grouping key (name for singles)
    local_path TEXT NOT NULL,     -- where the part landed (watcher imports it)
    pulled_at  TEXT NOT NULL,
    pruned_at  TEXT               -- set when files.delete removed the Drive copy
) STRICT;
