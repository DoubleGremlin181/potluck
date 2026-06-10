-- Bookkeeping key/value store; presence of this table means "initialized".
CREATE TABLE meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) STRICT;
