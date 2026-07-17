-- App-settings KV (#151): runtime overrides for config values that must be
-- togglable from the UI without editing config.toml or restarting.
--
-- Deliberately minimal — one table, JSON-encoded TEXT values, get/set — and
-- deliberately NOT a settings framework: config.toml (via pydantic-settings)
-- stays the source of truth for everything that has no runtime toggle. A key
-- here overrides the corresponding Settings field when present; absence of
-- the row means "use the config value" (values are never SQL NULL nor JSON
-- null — absence IS the null state, hence NOT NULL below).
--
-- First key: 'watch_enabled' (the settings-page watch-folder toggle).
CREATE TABLE app_settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL   -- JSON-encoded scalar (true/false/number/string)
) STRICT;
