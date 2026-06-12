-- #199 rider: persist email fields that were parsed-and-dropped (display
-- names) or never parsed (Bcc). Name arrays are positional parallels of
-- to_json/cc_json ("" = mailbox had no display name). Existing rows keep the
-- defaults until the gmail parser_version bump re-ingests them in place.
ALTER TABLE emails ADD COLUMN from_name TEXT;
ALTER TABLE emails ADD COLUMN to_names_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(to_names_json));
ALTER TABLE emails ADD COLUMN cc_names_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(cc_names_json));
ALTER TABLE emails ADD COLUMN bcc_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(bcc_json));
