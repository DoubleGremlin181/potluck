CREATE VIRTUAL TABLE items_fts USING fts5(
    title, text,
    content = 'items', content_rowid = 'id',
    tokenize = 'unicode61 remove_diacritics 2',
    prefix = '2 3'
);

CREATE TRIGGER items_fts_ai AFTER INSERT ON items BEGIN
    INSERT INTO items_fts(rowid, title, text) VALUES (new.id, new.title, new.text);
END;

CREATE TRIGGER items_fts_ad AFTER DELETE ON items BEGIN
    INSERT INTO items_fts(items_fts, rowid, title, text) VALUES ('delete', old.id, old.title, old.text);
END;

CREATE TRIGGER items_fts_au AFTER UPDATE OF title, text ON items BEGIN
    INSERT INTO items_fts(items_fts, rowid, title, text) VALUES ('delete', old.id, old.title, old.text);
    INSERT INTO items_fts(rowid, title, text) VALUES (new.id, new.title, new.text);
END;
