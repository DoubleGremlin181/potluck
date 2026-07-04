-- Emails satellite: per-message headers and threading state, one row per
-- email item. ON DELETE CASCADE: satellite rows die with their item.
CREATE TABLE emails (
    item_id     INTEGER PRIMARY KEY REFERENCES items(id) ON DELETE CASCADE,
    message_id  TEXT,            -- normalized (no <>); NULL when the header is absent
    in_reply_to TEXT,            -- normalized parent Message-ID; drives parent_id reconciliation
    thread_key  TEXT NOT NULL,   -- deterministic conversation key (References root, else In-Reply-To, else Message-ID)
    from_addr   TEXT,            -- lowercased addr-spec
    to_json     TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(to_json)),
    cc_json     TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(cc_json)),
    labels_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(labels_json))
) STRICT;

CREATE INDEX idx_emails_message_id  ON emails (message_id)  WHERE message_id IS NOT NULL;
CREATE INDEX idx_emails_in_reply_to ON emails (in_reply_to) WHERE in_reply_to IS NOT NULL;
CREATE INDEX idx_emails_thread_key  ON emails (thread_key);
CREATE INDEX idx_emails_from_addr   ON emails (from_addr)   WHERE from_addr IS NOT NULL;

-- Attachment dedup lookups (P2 #124) join files on content hash.
CREATE INDEX idx_files_sha256 ON files (sha256) WHERE sha256 IS NOT NULL;
