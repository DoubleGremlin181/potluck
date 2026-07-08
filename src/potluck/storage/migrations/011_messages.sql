-- Messages satellite (#142): chat threading + sender, one row per message
-- item. ON DELETE CASCADE: satellite rows die with their item. Media
-- references live in files (metadata-only until P6 pixel ingestion).
CREATE TABLE messages (
    item_id   INTEGER PRIMARY KEY REFERENCES items(id) ON DELETE CASCADE,
    chat_key  TEXT NOT NULL,   -- conversation key (chat file anchor); every message in one chat shares it
    chat_name TEXT,            -- human-readable chat title (contact or group name)
    sender    TEXT,            -- display name exactly as exported (contact name or phone string)
    is_media  INTEGER NOT NULL DEFAULT 0 CHECK (is_media IN (0, 1))
) STRICT;

CREATE INDEX idx_messages_chat_key ON messages (chat_key);
CREATE INDEX idx_messages_sender   ON messages (sender) WHERE sender IS NOT NULL;
