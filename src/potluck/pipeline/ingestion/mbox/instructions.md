# MBOX Email Import

## Thunderbird

Point Potluck at your Thunderbird profile's mail directory:

```bash
# Linux
potluck ingest ~/.thunderbird/<profile>/Mail/Local\ Folders --source mbox

# macOS
potluck ingest ~/Library/Thunderbird/Profiles/<profile>/Mail/Local\ Folders --source mbox
```

## Single MBOX file

```bash
potluck ingest /path/to/mailbox.mbox
```

## Apple Mail

Export mailboxes as MBOX files from Mail.app, then:

```bash
potluck ingest /path/to/exported.mbox
```
