# Google Drive Takeout auto-pull — setup guide

Potluck can automatically pull your scheduled [Google Takeout](https://takeout.google.com)
exports out of Google Drive and import them while `potluck serve` runs: schedule an export
every 2 months once, and your knowledge base keeps itself current.

One-time setup, four stages:

1. [Create your own Google OAuth client](#1-create-your-own-google-oauth-client) (~10 minutes,
   Google Cloud console)
2. [Configure Potluck](#2-configure-potluck) (two lines of config)
3. [Authorize](#3-authorize) (`potluck gdrive auth`, one browser approval)
4. [Schedule the Takeout export](#4-schedule-the-takeout-export) (takeout.google.com)

> Google's console UI shifts layout now and then; the steps below are anchored on the labels
> and page names, which move far less than the pixels. Written against the console as of
> mid-2026.

## Why you must supply your own OAuth client

Reading your Drive requires a Google OAuth "client". Potluck cannot ship one: the Drive
scopes it needs are **restricted** scopes, and a publicly distributed client using them
would require Google's app verification plus a recurring third-party security assessment —
and an open-source app's client secret would be public in the repo anyway, making the
verification meaningless.

So you create a client that belongs to *you*, in your own (free) Google Cloud project, used
only by your own account against your own data. You will see a "Google hasn't verified this
app" warning exactly once, during authorization — that is Google telling you that *you*
haven't submitted *your own* client for verification, which you have no reason to do.
Widely-used tools that talk to Drive/Gmail for personal use (rclone and friends) work the
same way.

## 1. Create your own Google OAuth client

### 1a. Create a project

1. Open <https://console.cloud.google.com/> and sign in with the Google account whose
   Takeout you export.
2. Click the **project picker** in the top bar → **New project**.
3. Name it anything (e.g. `potluck`), leave organization empty → **Create**, then make sure
   the picker now shows this project.

### 1b. Enable the Drive API

1. In the left menu (or the top search bar) open **APIs & Services → Library**.
2. Search for **Google Drive API** → open it → **Enable**.

### 1c. Configure the OAuth consent screen — and PUBLISH it

1. **APIs & Services → OAuth consent screen** (Google also calls this "Google Auth
   Platform" / "Branding" in newer consoles).
2. User type: **External** (the only choice on a personal account) → **Create**.
3. Fill only the required fields: app name (`potluck`), your email as user-support email
   and developer contact. No logo, no domains needed. Save through the remaining steps —
   you do **not** need to add scopes here (the auth request carries them), and you do not
   need test users.
4. **Critical**: on the consent-screen overview, set **Publishing status** to
   **In production** (button: **Publish app**; confirm the dialog and ignore the list of
   verification requirements — you are not submitting for verification).

   > **Why this matters**: while an app is in **Testing** status, Google expires its
   > refresh tokens after **7 days**. Your exports arrive every 2 months — a testing-status
   > token *will* silently die between pulls and Potluck will report
   > `reauth_required` forever. "In production" (even unverified) issues long-lived
   > refresh tokens.

### 1d. Create the client credentials

1. **APIs & Services → Credentials → + Create credentials → OAuth client ID**.
2. Application type: **Desktop app** (this is what permits the localhost redirect the auth
   command uses). Name it anything.
3. **Create** → a dialog shows the **Client ID** (ends in `.apps.googleusercontent.com`)
   and **Client secret** (starts with `GOCSPX-`). Copy both — they go into Potluck's
   config next. (You can re-view them anytime under Credentials.)

## 2. Configure Potluck

Add the two values to Potluck's `config.toml` — flat top-level `gdrive_*` keys (there is
no `[gdrive]` section; the config file has no sections at all):

```toml
# <config dir>/potluck/config.toml
# Linux: ~/.config/potluck/config.toml   macOS: ~/Library/Application Support/potluck/
gdrive_client_id = "1234567890-abcdef.apps.googleusercontent.com"
gdrive_client_secret = "GOCSPX-your-secret-here"

# Optional (defaults shown):
# gdrive_folder_name = "Takeout"     # Drive folder the exports land in
# gdrive_interval_s = 86400          # poll daily (exports arrive every 2 months)
# gdrive_enabled = true              # runtime toggle also available via API/UI
# gdrive_prune = false               # see "Remote pruning" below before enabling
```

Environment variables work too (`POTLUCK_GDRIVE_CLIENT_ID`, `POTLUCK_GDRIVE_CLIENT_SECRET`)
and win over the file. A desktop-app client secret is not a strong secret (Google's own
docs say installed-app secrets can't be kept confidential), but tightening the file to
`chmod 600 config.toml` is still good hygiene.

## 3. Authorize

On the machine that runs `potluck serve`:

```console
$ potluck gdrive auth
```

1. Your browser opens Google's consent page (the URL is also printed, in case it doesn't).
2. Pick your account. You'll hit **"Google hasn't verified this app"** — expected, it's
   *your* client (see above). Click **Advanced → Go to potluck (unsafe)**.
3. Approve the requested access ("See and download all your Google Drive files").
4. The browser lands on a local "Authorization received" page; the terminal confirms:

   ```text
   Authorized. Token saved (0600) to ~/.config/potluck/gdrive_token.json
   Takeout archives will be pulled while `potluck serve` runs.
   ```

The token file is created with `0600` permissions and is the only place the credential
lives — never the database. Re-run `potluck gdrive auth` anytime to re-authorize (e.g.
after revoking access at <https://myaccount.google.com/permissions>).

**Headless server?** Two options:

- `potluck gdrive auth --no-browser` — prints the consent URL; open it in any browser
  anywhere. After you approve, the browser tries to load a `http://127.0.0.1:8085/...`
  URL and fails (expected — nothing is listening there). Copy that entire URL from the
  address bar and paste it back into the terminal prompt.
- Or run `potluck gdrive auth` on a desktop machine with the same config, then copy
  `gdrive_token.json` into the server's Potluck config dir (`chmod 600` it).

## 4. Schedule the Takeout export

1. Open <https://takeout.google.com> → **Create a new export**.
2. Select the data to include (Keep, Chrome, Chat, Calendar, Photos, Mail, Timeline… —
   everything Potluck ingests is fair game; it skips what it doesn't recognize).
3. **Next step** → destination: **Add to Drive**. Frequency: **Export every 2 months for
   1 year** (Google's maximum automation — it re-prompts yearly). File type: `.zip`;
   size: any (Potluck handles multi-part exports; 10 GB parts mean fewer files).
4. **Create export**. Exports appear in a Drive folder named **Takeout** (if you rename or
   move it, set `gdrive_folder_name` accordingly — Potluck matches by name).

## Verify

```console
$ potluck gdrive status
gdrive: configured
gdrive auth: ok
gdrive enabled: True (config)
...
```

Then start (or restart) `potluck serve`. The puller checks Drive immediately at startup
and daily thereafter; new archives download into the managed downloads dir (default:
`<data dir>/potluck/gdrive/`), where the watch-folder importer picks them up within
seconds. Watch progress on the web app's imports page, or:

- `GET /api/gdrive` — auth state, last check/pull, errors (`potluck gdrive status` shows
  the same durable state from the CLI; runtime fields are live only inside the serve
  process).
- Re-imports of bytes you already imported are automatically skipped (content-hash
  ledger), so overlap between pulled exports and manual imports is harmless.

If status ever shows `reauth_required`, run `potluck gdrive auth` again — the refresh
token was revoked or expired (did the consent screen stay in Testing status? See 1c).
`offline` is not an error: the puller just couldn't reach Drive (laptop off the network)
and will retry next cycle.

## Remote pruning (optional, destructive, default off)

Takeout archives are large and Drive quota is finite. With pruning enabled, Potluck
**permanently deletes** a pulled archive set from Drive (`files.delete` — not the trash,
which would keep counting against quota for 30 days) once every part of the set was
downloaded, verified, and its import **completed** successfully. It deletes only file ids
it recorded pulling — never anything else in the folder.

Two explicit switches are required:

1. Authorize with delete rights: `potluck gdrive auth --prune` (requests the full Drive
   scope; the consent screen will say "See, edit, create and delete all of your Google
   Drive files").
2. Set `gdrive_prune = true` in `config.toml`.

Enabling the flag without the scope is safe: status shows a re-auth instruction and
nothing is deleted. Your local copies (downloads dir) and imported items are never
touched by pruning.
