# WhatsApp Backup Import

## Prerequisites

This ingester works with **decrypted WhatsApp backup databases**, not text chat exports.

You need [whatsapp-backup-downloader-decryptor](https://github.com/giacomoferretti/whatsapp-backup-downloader-decryptor) to extract your backup.

## Setup

1. Follow the whatsapp-backup-downloader-decryptor instructions to download and decrypt your WhatsApp backup
2. You should end up with a directory like `16506835325_20251222-decrypted/` containing:
   - `Databases/msgstore.db` — The message database
   - `Media/` — Media files (photos, videos, audio, documents)

## Usage

```bash
# Point to the decrypted backup folder
potluck ingest /path/to/16506835325_20251222-decrypted --source whatsapp

# Or point directly to the database file
potluck ingest /path/to/msgstore.db --source whatsapp

# Filter by date range
potluck ingest /path/to/backup --source whatsapp --since 2024-01-01 --until 2025-01-01
```

## What gets imported

- **Chat threads**: DM and group conversations
- **Messages**: Text messages with sender info, timestamps, starred status
- **Media**: Photos, videos, audio, documents linked to messages

## Notes

- The database is opened in read-only mode — your backup is never modified
- Phone numbers are used as sender identifiers (contact name resolution is not available from the backup)
- Newsletter channels and broadcast lists are automatically skipped
- Large databases (100K+ messages) are processed in batches for memory efficiency
