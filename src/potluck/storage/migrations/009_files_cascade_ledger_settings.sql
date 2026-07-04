-- #198 review: (1) files.item_id gains ON DELETE CASCADE — 005 gave emails
-- the same; without it, deleting an item that has attachment rows raises
-- IntegrityError. SQLite cannot add a constraint in place, so recreate the
-- table (legal under foreign_keys=ON inside the migration transaction:
-- files is a leaf — nothing references it).
CREATE TABLE files_new (
    id          INTEGER PRIMARY KEY,
    item_id     INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    member_path TEXT NOT NULL,
    mime        TEXT,
    size_bytes  INTEGER,
    sha256      TEXT
) STRICT;
INSERT INTO files_new (id, item_id, member_path, mime, size_bytes, sha256)
    SELECT id, item_id, member_path, mime, size_bytes, sha256 FROM files;
DROP TABLE files;
ALTER TABLE files_new RENAME TO files;
-- Recreate BOTH indexes: idx_files_item (002) and idx_files_sha256 (005).
CREATE INDEX idx_files_item   ON files (item_id);
CREATE INDEX idx_files_sha256 ON files (sha256) WHERE sha256 IS NOT NULL;

-- (2) Parse-affecting settings on the ledger row: the completed-run
-- short-circuit must distinguish runs that extracted attachment blobs from
-- runs that did not. Historical rows default to 0 ("did not extract") —
-- conservative: enabling extraction later re-parses them.
ALTER TABLE imports ADD COLUMN extract_attachments INTEGER NOT NULL DEFAULT 0
    CHECK (extract_attachments IN (0, 1));
