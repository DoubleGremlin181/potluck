# Reddit Data Export

## How to request your data

1. Go to https://www.reddit.com/settings/data-request
2. Select "GDPR" as the request type
3. Click "Submit Request"
4. Wait for Reddit to process your request (usually 24-48 hours)
5. Download the ZIP file from the link in the email

## What's included

- `posts.csv` — Your posts
- `comments.csv` — Your comments
- `subscribed_subreddits.csv` — Subreddits you're subscribed to
- `saved_posts.csv` — Posts you've saved
- `saved_comments.csv` — Comments you've saved

## Import command

```bash
potluck ingest /path/to/export_username_20240101.zip
```
