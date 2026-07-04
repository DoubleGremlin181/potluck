-- #132: progress lives directly on the imports row — no jobs table. The row
-- already carries status / error / finished_at (002) and the per-batch
-- counters (002/004), updated once per committed batch; items_done derives
-- from those counters (items_new + items_duplicate + items_updated +
-- items_skipped). The one missing piece is the denominator: the expected
-- item count, when a source can know it cheaply. NULL = unknown — streaming
-- sources (an mbox is one giant member) cannot know the total without a
-- pre-scan, and the engine never pre-scans just to count.
ALTER TABLE imports ADD COLUMN items_total INTEGER
    CHECK (items_total IS NULL OR items_total >= 0);
