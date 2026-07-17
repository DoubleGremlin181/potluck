-- Transactions satellite (#144): exact integer money + the aggregation-worthy
-- register columns, one row per transaction item. ON DELETE CASCADE:
-- satellite rows die with their item. amount_milliunits is the signed amount
-- in 1/1000 currency units (outflow negative) — INTEGER + STRICT means a
-- float can never be stored. Memo lives in items.text (FTS), cleared/flag in
-- items.meta (display enums, not aggregation targets).
CREATE TABLE transactions (
    item_id           INTEGER PRIMARY KEY REFERENCES items(id) ON DELETE CASCADE,
    amount_milliunits INTEGER NOT NULL,
    account           TEXT,  -- register account name exactly as exported
    payee             TEXT,  -- exactly as exported ("Transfer : X" kept verbatim)
    category          TEXT,  -- leaf category (NULL: transfers, uncategorized)
    category_group    TEXT   -- parent group (spend-by-group aggregation)
) STRICT;

CREATE INDEX idx_transactions_account  ON transactions (account)  WHERE account  IS NOT NULL;
CREATE INDEX idx_transactions_payee    ON transactions (payee)    WHERE payee    IS NOT NULL;
CREATE INDEX idx_transactions_category ON transactions (category) WHERE category IS NOT NULL;
