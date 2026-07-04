-- #199: rebuild items_fts WITHOUT prefix indexes. Measured on a real 126k-email
-- corpus: prefix='2 3' doubled FTS insert cost (89% of the write stage) and
-- added 245 MB (~50% of the FTS index). Without it, >=4-char prefix queries
-- fall back to term-range scans at unchanged speed; the 1-2-char worst case
-- degrades to ~100-300 ms — acceptable for SAYT. detail= stays default:
-- 'column'/'none' would break snippet()/highlight() (search/fts.py).
-- The 003 triggers live on items and reference items_fts by name — they
-- survive the drop/recreate. 'rebuild' repopulates from the items content
-- table (one-time cost, ~1-2 min per GB of indexed text).
DROP TABLE items_fts;

CREATE VIRTUAL TABLE items_fts USING fts5(
    title, text,
    content = 'items', content_rowid = 'id',
    tokenize = 'unicode61 remove_diacritics 2'
);

INSERT INTO items_fts(items_fts) VALUES ('rebuild');
