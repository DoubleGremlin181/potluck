# YNAB (You Need A Budget) Export

## How to export your data

1. Open YNAB in your browser at https://app.ynab.com
2. Go to your budget
3. Click the account name in the sidebar
4. Click "Export" in the toolbar
5. Select "Export Budget" to get both Register and Plan CSVs

## What's included

- `*Register.csv` — All transactions across all accounts
- `*Plan.csv` — Monthly budget allocations by category

## Import command

```bash
potluck ingest "/path/to/YNAB Export - My Budget as of 2026-01-01 20-15.zip"
```
