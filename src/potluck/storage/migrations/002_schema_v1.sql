CREATE TABLE sources (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;

CREATE TABLE imports (
    id              INTEGER PRIMARY KEY,
    source_id       INTEGER NOT NULL REFERENCES sources(id),
    path            TEXT NOT NULL,
    file_hash       TEXT,            -- sha256 of the passed archive file (per part for multi-part sets); NULL for dirs
    parser_version  INTEGER NOT NULL,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    status          TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running','completed','failed')),
    items_new       INTEGER NOT NULL DEFAULT 0,
    items_duplicate INTEGER NOT NULL DEFAULT 0,
    items_skipped   INTEGER NOT NULL DEFAULT 0,
    error           TEXT
) STRICT;

CREATE TABLE items (
    id           INTEGER PRIMARY KEY,   -- rowid alias; used as content_rowid by items_fts (migration 003)
    source_id    INTEGER NOT NULL REFERENCES sources(id),
    import_id    INTEGER NOT NULL REFERENCES imports(id),
    kind         TEXT NOT NULL CHECK (kind IN ('note','email','message','photo','file','event',
                     'contact','location','transaction','bookmark','post','activity')),
    external_id  TEXT,
    content_hash TEXT NOT NULL,
    ts           TEXT,
    title        TEXT,
    text         TEXT,
    lat          REAL,
    lon          REAL,
    parent_id    INTEGER REFERENCES items(id),
    meta         TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(meta))
) STRICT;

-- Dedup identity is per source: the same content under two sources is two
-- logical items (migration 004's (source_id, external_id) identity agrees).
CREATE UNIQUE INDEX idx_items_source_hash ON items (source_id, content_hash);

CREATE INDEX idx_items_kind_ts ON items (kind, ts);
-- Early-exit scan for the default unfiltered listing (ts DESC NULLS LAST,
-- id DESC); NULL ts sorts smallest, so a DESC index is naturally NULLS LAST.
CREATE INDEX idx_items_ts ON items (ts DESC, id DESC);
CREATE INDEX idx_items_import  ON items (import_id);
CREATE INDEX idx_items_parent  ON items (parent_id) WHERE parent_id IS NOT NULL;

CREATE TABLE files (
    id          INTEGER PRIMARY KEY,
    item_id     INTEGER NOT NULL REFERENCES items(id),
    member_path TEXT NOT NULL,
    mime        TEXT,
    size_bytes  INTEGER,
    sha256      TEXT
) STRICT;
CREATE INDEX idx_files_item ON files (item_id);
