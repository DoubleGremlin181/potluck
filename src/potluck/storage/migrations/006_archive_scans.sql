-- Detection cache (#196): detect_sources is a pure function of the archive
-- bytes and the registered plugins' globs. For tgz archives the scan is a
-- full decompression pass (73 s on a real 3.8 GB Takeout), so its outcome is
-- cached by (file_hash, registry fingerprint). Rows are never invalidated —
-- a registry change produces a new fingerprint and simply misses.
CREATE TABLE archive_scans (
    file_hash    TEXT NOT NULL,  -- sha256 of the archive file
    registry_fp  TEXT NOT NULL,  -- sha256 over sorted "name:glob" of registered plugins
    matched_json TEXT NOT NULL CHECK (json_valid(matched_json)),  -- matched plugin names ([] = no match)
    scanned_at   TEXT NOT NULL,
    PRIMARY KEY (file_hash, registry_fp)
) STRICT;
