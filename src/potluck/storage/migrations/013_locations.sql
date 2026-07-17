-- Locations satellite (#148): coordinates + place identity, one row per
-- location item. ON DELETE CASCADE: satellite rows die with their item.
-- lat/lon duplicate items.lat/lon on purpose: items' columns are nullable
-- for every kind, so the satellite owns the "a location always has
-- coordinates" invariant, and P5 spatial linking gets one self-contained
-- table. Routes: lat/lon = start, end_lat/end_lon = end (NULL for visits
-- and raw positions). No spatial index yet — YAGNI until the P5 enrich
-- plane defines the actual spatial queries.
CREATE TABLE locations (
    item_id       INTEGER PRIMARY KEY REFERENCES items(id) ON DELETE CASCADE,
    lat           REAL NOT NULL,
    lon           REAL NOT NULL,
    end_lat       REAL,           -- route end point (visits/positions: NULL)
    end_lon       REAL,
    place_id      TEXT,           -- Google Place id exactly as exported
    semantic_type TEXT,           -- visit place type or route activity type, verbatim
    distance_m    REAL            -- route distance in meters, exactly as exported
) STRICT;

CREATE INDEX idx_locations_place_id ON locations (place_id) WHERE place_id IS NOT NULL;
