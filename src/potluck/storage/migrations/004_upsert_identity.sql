-- One logical item per (source, external_id): reimports update the existing
-- row in place instead of accumulating versions.
--
-- Pre-004 databases may hold several rows per (source_id, external_id) from
-- the insert-only era; keep the newest (highest id). The DELETE fires the
-- items_fts AFTER DELETE trigger (003), keeping the FTS index in sync.
-- Safe under PRAGMA foreign_keys=ON: nothing writes items.parent_id or
-- files.item_id yet, so no child rows can reference the deleted ids.
DELETE FROM items
WHERE external_id IS NOT NULL
  AND id NOT IN (
      SELECT MAX(id)
      FROM items
      WHERE external_id IS NOT NULL
      GROUP BY source_id, external_id
  );

CREATE UNIQUE INDEX idx_items_source_external
    ON items (source_id, external_id)
    WHERE external_id IS NOT NULL;

ALTER TABLE imports ADD COLUMN items_updated INTEGER NOT NULL DEFAULT 0;
