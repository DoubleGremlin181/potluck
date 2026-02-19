# Text Files & Obsidian Vault Import

## Obsidian Vault

Point Potluck at your Obsidian vault directory:

```bash
potluck ingest /path/to/my-vault --source text_files
```

Potluck will automatically detect the `.obsidian/` directory and skip config files.

## Plain text files

Import any folder containing .txt or .md files:

```bash
potluck ingest /path/to/notes --source text_files
```

## Supported formats

- `.txt` — Plain text
- `.md` / `.markdown` — Markdown
- `.rst` — reStructuredText
- `.text` — Plain text (alternative extension)
