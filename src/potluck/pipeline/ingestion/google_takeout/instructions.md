# Google Takeout Export Instructions

This guide explains how to export your data from Google and import it into Potluck.

## Step 1: Request Your Data Export

1. Go to [Google Takeout](https://takeout.google.com/)
2. Sign in with your Google account if prompted
3. Click **Deselect all** to start fresh

## Step 2: Select Data to Export

Select the data types you want to import into Potluck:

### Google Photos
- Check **Google Photos**
- Click "All photo albums included" to select specific albums if desired
- Photos include metadata like location, date taken, and descriptions

### Gmail
- Check **Mail**
- By default, all mail is included
- Click "All Mail data included" to select specific labels if desired
- Format: MBOX

### Google Chat / Hangouts
- Check **Google Chat**
- Includes all conversations, DMs, and spaces
- Messages include sender info, timestamps, and attachments

### Google Calendar
- Check **Calendar**
- Format: iCalendar (.ics)
- Includes all calendars you have access to

### Chrome
- Check **Chrome**
- Make sure "Bookmarks" and "History" are selected
- History format: JSON
- Bookmarks format: HTML

### Location History (Timeline)
- Check **Location History** or **Timeline**
- Includes visited places, activity, and travel history
- Note: Google changed location storage in 2024; recent data may be sparse

## Step 3: Configure Export Settings

1. Click **Next step**
2. Choose export settings:
   - **Delivery method**: Download link via email
   - **Frequency**: Export once
   - **File type**: .zip or .tgz
   - **File size**: 2 GB or larger (to avoid splitting into multiple files)

## Step 4: Create and Download Export

1. Click **Create export**
2. Wait for Google to prepare your archive (this can take hours to days depending on data size)
3. You'll receive an email when your export is ready
4. Download the archive file(s)

## Step 5: Import into Potluck

1. Place the downloaded archive (e.g., `takeout-20240115T123456Z-001.zip`) in a location accessible to Potluck
2. If you have multiple archive files, you can either:
   - Import them one at a time
   - Extract them all to a single folder first
3. Use the Potluck import command or UI to process the archive

## Tips

- **Large exports**: For accounts with lots of data, request exports in smaller chunks (by service or date range)
- **Privacy**: Keep your takeout archives secure - they contain personal data
- **Partial imports**: You can select only specific entity types to import
- **Re-importing**: Potluck uses content hashing to avoid duplicates, so re-importing is safe

## Supported Data Types

| Google Service | Potluck Entity Type | Notes |
|----------------|---------------------|-------|
| Google Photos | Media | Photos, videos, metadata |
| Gmail | Email | Messages, threads, labels |
| Google Chat | Chat Message | Conversations, DMs, spaces |
| Google Calendar | Calendar Event | Events, reminders, recurring |
| Chrome History | Browsing History | URLs, titles, visit times |
| Chrome Bookmarks | Bookmark | URLs, folders, organization |
| Location History | Location Visit | Places, activities, travel |

## Troubleshooting

### Export is taking too long
Large accounts (especially with many photos) can take 24-48 hours. Check your email for status updates.

### Multiple archive files
Google splits large exports into multiple files. Import each file separately or extract them all to one folder.

### Missing data
Some data types may be empty if you haven't used that Google service. Check which services are active in your Google account.

### Location History is sparse
Google changed to on-device location storage in 2024. For richer location data, export your Android Timeline directly from your phone's Settings > Location > Timeline > Export timeline.
