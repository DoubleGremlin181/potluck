-- Media satellite (#149): per-item photo/video facts, one row per photo
-- item. ON DELETE CASCADE: satellite rows die with their item.
--
-- Satellite-vs-files split, decided explicitly (the files table is NOT
-- reused here): files rows describe attachments OF an item located by a
-- per-archive member path; a photo item IS its file, and archive paths are
-- transient across re-exports (albums move, multi-part splits shift), so no
-- path is stored anywhere. The byte-derived facts live here instead —
-- sha256 is the durable locator P6 pixel ingestion needs (indexed below).
--
-- All columns are hash-covered (PhotoDraft.extra_hash_parts). width/height/
-- camera/gps_alt are NULL when the probe has nothing (videos are never
-- probed; images may lack EXIF); size_bytes/sha256 come from the streamed
-- bytes themselves and can never be absent. gps_alt complements items.lat/
-- lon (items has no altitude column); lat/lon themselves stay on items only
-- — unlike locations (013) there is no NOT NULL invariant to own, so
-- duplicating them here would be pure drift risk. No duration column:
-- nothing can populate it until a video probe dependency exists.
CREATE TABLE media (
    item_id      INTEGER PRIMARY KEY REFERENCES items(id) ON DELETE CASCADE,
    width        INTEGER,        -- pixel dimensions (NULL: video or unprobeable image)
    height       INTEGER,
    camera_make  TEXT,           -- EXIF Make, exactly as exported (NUL/space stripped)
    camera_model TEXT,           -- EXIF Model
    gps_alt      REAL,           -- altitude in meters from the winning GPS source
    mime         TEXT,           -- probed format's MIME, else extension-guessed
    size_bytes   INTEGER NOT NULL,
    sha256       TEXT NOT NULL   -- full hex digest of the media bytes
) STRICT;

CREATE INDEX idx_media_sha256 ON media (sha256);
