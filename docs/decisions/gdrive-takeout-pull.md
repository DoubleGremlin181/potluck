# Decision: Google Drive Takeout auto-pull (#152)

- **Status**: accepted (Phase A of the #152 spike; implementation follows in Phase B)
- **Date**: 2026-07-16
- **Issue**: [#152](https://github.com/DoubleGremlin181/potluck/issues/152) — scheduled Takeout
  exports delivered to Google Drive get pulled automatically while `potluck serve` runs,
  land in a watched folder, and import incrementally; optional remote pruning.

No network calls or real OAuth flows were performed for this spike (none are possible in the
dev environment); endpoint/flow facts below come from the Google OAuth 2.0 and Drive v3 REST
documentation, cross-checked against widely deployed user-supplied-client tools (e.g. rclone).
Phase B validates everything mockable; the setup guide is the validation path for the rest.

## Decision summary

| # | Area | Decision |
|---|------|----------|
| 1 | Library | Raw REST over `httpx` (already core). **No** google-api-python-client. |
| 2 | Auth flow | Installed-app **loopback** flow + PKCE via `potluck gdrive auth`. **Not** device flow (its scope allowlist excludes `drive.readonly`). |
| 3 | Scopes | `drive.readonly` default; `drive` (full) only when `--prune` requested at auth time. |
| 4 | Client creds | `gdrive_client_id` / `gdrive_client_secret` in `config.toml` (env-overridable). |
| 5 | Token storage | JSON file `config_dir()/gdrive_token.json`, 0600, atomic rewrite; never in DB. |
| 6 | Architecture | Second lifespan-owned poller thread (`DrivePuller`, mirrors `FolderWatcher`); downloads into a managed dir that the #151 watcher imports. Puller never touches `import_manager`. |
| 7 | Pull tracking | New tiny table `gdrive_pulls` (migration 017), not app_settings KV. |
| 8 | Pruning | Ships in v1 behind `gdrive_prune = false`; `files.delete` on exact pulled ids only, after the whole set's import is verified `done`. |
| 9 | Testing | `httpx.MockTransport` fixture; integration test list→download→ingest→record; 0600 asserted; zero network in tests. |

## Deviations from the issue text (authorized: standing requirement-adjustment grant)

1. **`google-api-python-client` → raw REST over `httpx`** (§1). The issue's parenthetical
   predates absolute rule 2 pressure-testing; the SDK stack is a heavy permanent core
   dependency for four HTTPS calls.
2. **"device/installed flow" → installed-app loopback only** (§2). Google's device flow
   ("OAuth 2.0 for TV and Limited-Input Devices") only permits an allowlisted scope set —
   `drive.file`, `drive.appdata`, profile/email, YouTube scopes. It does **not** allow
   `drive.readonly` or `drive`, and `drive.file` cannot see Takeout-created files (they are
   not created by our client). Device flow is therefore structurally unusable for this
   feature, not merely inferior.

## 1. Library: raw REST over httpx

**Decision**: implement a small `DriveClient` (auth-URL construction, code→token exchange,
token refresh, `files.list`, `files.get?alt=media` streaming download, `files.delete`)
directly over `httpx`, which has been a core dependency since P3 (`httpx>=0.28` in
`pyproject.toml`; `potluck.testing.server` already imports it).

The entire API surface is four plain HTTPS endpoints:

- `POST https://oauth2.googleapis.com/token` — `grant_type=authorization_code` and
  `grant_type=refresh_token` (form-encoded, JSON response).
- `GET https://www.googleapis.com/drive/v3/files` — `q` filter, `fields=nextPageToken,`
  `files(id,name,size,md5Checksum,mimeType,createdTime)`, `pageSize`, `pageToken`.
- `GET https://www.googleapis.com/drive/v3/files/{id}?alt=media` — bytes; supports `Range`.
- `DELETE https://www.googleapis.com/drive/v3/files/{id}` — pruning only.

Plus one URL we never request ourselves: `https://accounts.google.com/o/oauth2/v2/auth`,
which the auth command opens in the user's browser.

**Why not google-api-python-client**: absolute rule 2 (no optional deps, ever) makes it a
permanent core addition for *every* install — and it is a stack, not a package:
`google-api-python-client` pulls `httplib2`, `google-auth`, `google-auth-httplib2`,
`uritemplate`, and `google-api-core` (which drags `protobuf` + `googleapis-common-protos`);
the auth flow additionally wants `google-auth-oauthlib` → `requests-oauthlib` → `requests`,
i.e. a second, redundant HTTP client stack beside httpx. Tens of MB and ~10 transitive deps
to wrap four calls we can write and mock more precisely ourselves (`httpx.MockTransport` is
first-class; mocking httplib2 inside the SDK is miserable).

**Honest trade-offs**: we own token-refresh plumbing, pagination, and backoff (~150 lines,
all unit-testable); we track two stable, versioned endpoints ourselves (Drive v3 and the
OAuth token endpoint have been unchanged for ~a decade — low risk); no automatic transport
retries (we need cycle-level backoff anyway, §8). If Potluck ever grows deep Google API
integrations, this decision can be revisited — for list/download/delete it cannot pay for
itself.

## 2. Auth UX: user-supplied client, loopback flow, CLI command

**User-supplied OAuth client (non-negotiable)**: `drive.readonly` and `drive` are Google
**restricted** scopes. A shared client shipped with an open-source local app would require
Google verification plus an annual CASA security assessment, and the client secret would be
public in the repo anyway. Every comparable tool (rclone et al.) has users create their own
OAuth client; the user is then the app's owner and its only user. The setup guide (§9) owns
the walkthrough, including the two console gotchas that actually bite:

- **Publishing status must be "In production"** (unverified is fine). A consent screen left
  in "Testing" issues refresh tokens that **expire after 7 days** — fatal for an
  every-2-month cadence. Production-unverified shows a one-time "Google hasn't verified this
  app" interstitial with an *Advanced → continue* path; acceptable because the user owns the
  client.
- Client type **"Desktop app"**, which permits loopback (`http://127.0.0.1:<port>`) redirect
  URIs without pre-registration.

**Flow — installed-app loopback with PKCE** (device flow ruled out; see Deviations):
`potluck gdrive auth` binds an ephemeral port on 127.0.0.1, builds the consent URL
(`response_type=code`, `access_type=offline`, `prompt=consent` — guarantees a refresh token
on re-auth — plus S256 PKCE), opens the browser (`webbrowser.open`, URL also printed),
catches the redirect with a one-shot stdlib `http.server` handler, exchanges the code, and
writes the token file (§3). `--no-browser` prints the URL and prompts for a paste of the
full redirect URL (the localhost redirect fails to load on a headless box, but the code is
in the address bar) — the standard headless workaround; copying the token file from another
machine also works and is documented.

**Where the flow lives — CLI, not settings UI**: authorization is one-time, wants a browser
plus a loopback listener on the *server's* host, and produces a secret file. A CLI command
is honest about all three; a settings-UI flow would need the SPA to relay codes to the
backend and would still break on remote/containerized serves. The settings/watch UI gets a
read-only auth-state surface (§8); an in-UI flow can be a follow-up if demand appears.

**Client credentials in `config.toml`** (`gdrive_client_id`, `gdrive_client_secret` — flat
`Settings` fields like the `watch_*` family, so `POTLUCK_GDRIVE_CLIENT_SECRET` env override
works for free). Google's own docs state that installed-app client secrets are "not treated
as a secret" — the binary can't protect them — so a plain user-owned config file is
proportionate; the guide still suggests `chmod 600 config.toml`. Rejected alternatives:
parsing Google's downloaded `client_secret.json` (a second config surface and a foreign
schema for two strings — the guide shows exactly where to copy the values from); OS keyring
(new dependency, headless pain — violates the spirit of rule 2).

**Scopes — least privilege that works**: `https://www.googleapis.com/auth/drive.readonly`
by default. `drive.file` *would* be the polite non-sensitive scope but only exposes files
created/opened by this client — Takeout's files are invisible to it. `drive.metadata.readonly`
cannot download content. Pruning needs delete rights, i.e. full
`https://www.googleapis.com/auth/drive`: requested **only** when the user runs
`potluck gdrive auth --prune`. Granted scopes are recorded in the token file; enabling
`gdrive_prune` later without the scope surfaces a "re-auth with --prune required" status
error (§6) — we never silently escalate.

## 3. Token storage

**Decision**: `config_dir()/gdrive_token.json` (a `gdrive_token_path()` helper joins the
existing family in `core/paths.py`), containing refresh token, granted scopes, client_id
(to detect a changed client → force re-auth), and obtained-at timestamp. Written via
`os.open(..., O_WRONLY|O_CREAT|O_EXCL, 0o600)` on a temp name in the same directory, then
`os.rename` — atomic, and 0600 from birth (no chmod window). The acceptance criterion
"secrets never in DB plaintext" is met by never writing any token material through
`Database.write()` at all; `gdrive_pulls` (§4) holds only file ids/checksums/names.

**Access tokens stay in memory** (~1 h lifetime; the puller refreshes lazily on 401 or
known expiry). Persisting them would rewrite the file hourly for no benefit. **Rotation**:
Google normally returns the same refresh token for installed apps, but *if* a token
response includes a new `refresh_token`, the file is atomically rewritten — cheap insurance.

**Refresh failure** (`invalid_grant` = revoked/expired/password-reset): the puller flips to
`auth_state="reauth_required"`, stops calling Drive (cheap no-op cycles, like the watcher's
disabled state), and surfaces the state + re-auth instructions through gdrive status
(§8) — the scheduler never crashes and never spins on a dead token. `potluck gdrive auth`
re-runs the flow and clears the state. The token file is never auto-deleted on failure
(transient 5xx from the token endpoint must not destroy a working credential; those retry
with backoff instead).

## 4. Architecture: compose with the #151 watcher

**Decision — the brief's composition, affirmed**: a `DrivePuller` in
`services/gdrive_manager.py` mirroring `FolderWatcher` exactly — `configure()` /
`start()` / `run_cycle()` / `stop()` / `join()` / `snapshot()`, one lock, a named daemon
thread (`potluck-gdrive`) started only by the serve lifespan next to `start_watcher()`
(`api/app.py` already stops/joins the watcher on shutdown; the puller joins the same
block). Tests drive `run_cycle()` synchronously, no thread — the proven #151 pattern.

The puller **only downloads**: new Takeout archives land in a managed
`gdrive_downloads_dir` (default `data_dir()/gdrive`, a new `Settings` field +
`default_gdrive_downloads_dir()` in `core/paths.py`). That directory is appended to the
watcher's effective folder tuple when the puller is configured (`start_watcher` gains
`effective folders = settings.watch_folders + [gdrive_downloads_dir if gdrive configured]`,
and starts even when `watch_folders` is empty in that case). The #151 watcher then does
what it already does: two-scan debounce, multi-part set grouping via `parse_part_name`,
atomic claim of the import manager, backoff, `last_error` recovery. **The puller never
touches `import_manager`** — one component owns import submission, and every #151
behavior (dedup short-circuit, claim-busy retry, status surfacing) is inherited instead
of reimplemented. A pure `DriveClient` (HTTP only, injectable `httpx` transport) lives in
`ingest/gdrive.py` — acquisition is ETL-plane work, `services/` stays thin, and
import-linter contracts hold (cli/api reach it only through services).

**Pulled-file tracking — tiny table, not KV**: migration `017_gdrive_pulls.sql`:

```sql
CREATE TABLE gdrive_pulls (
    file_id    TEXT PRIMARY KEY,  -- Drive file id
    name       TEXT NOT NULL,     -- takeout-....zip (part file name)
    md5        TEXT,              -- Drive md5Checksum (verification, §8)
    set_stem   TEXT NOT NULL,     -- parse_part_name grouping key
    local_path TEXT NOT NULL,     -- where it landed
    pulled_at  TEXT NOT NULL,
    pruned_at  TEXT               -- set by the prune step (§6)
) STRICT;
```

The `app_settings` KV is documented as JSON **scalars** with absence-as-null semantics —
a growing JSON blob under one key would abuse it, and pruning (§6) needs per-file durable
state (`pruned_at`) plus a queryable join against the `imports` table. Rows are ~6 sets/yr
× a handful of parts: trivially small. As the brief notes, this tracking is
bandwidth-saving, not correctness — the content-hash ledger already short-circuits
re-imported bytes, so a lost row merely costs one re-download. The KV still gets one key:
a `gdrive_enabled` runtime toggle mirroring `watch_enabled` (pause pulling from the UI
without editing config).

## 5. Pull selection

**How Takeout appears in Drive**: scheduled exports ("Export every 2 months" → deliver to
Drive) create files in a folder named **Takeout** in My Drive root; archives are named
`takeout-YYYYMMDDTHHMMSSZ-NNN.zip` (old style) or `takeout-YYYYMMDDTHHMMSSZ-F-NNN.tgz`
(new style, file number + part) — exactly the shapes `ingest.readers.parse_part_name`
already groups, and split at the user-chosen 1/2/4/10/50 GB chunk size.

**Selection algorithm** (per cycle): resolve folder(s) matching
`name = '<gdrive_folder_name>' and mimeType = 'application/vnd.google-apps.folder' and
trashed = false` (config `gdrive_folder_name`, default `"Takeout"`); list children
(`'<id>' in parents and trashed = false`, paginated) and keep names ending in
`.zip/.tgz/.tar.gz`; group into sets with `parse_part_name`; skip every `file_id` already
in `gdrive_pulls`; download the rest, oldest export first. Name-based, id-tracked — a
re-run export gets fresh ids and is pulled again (ledger dedups the bytes downstream).

**Poll interval**: `gdrive_interval_s`, default **86400 s (daily)** — a 2-month cadence
makes hourly polling pointless, and daily bounds the worst-case pickup delay at a level
nobody notices on a 2-month loop. One cycle runs immediately at startup (same shape as the
watcher's loop: cycle, then `stop.wait(interval)`), so setup verification and
laptop-usually-asleep cases don't wait a day. Each cycle is 2–3 list calls when idle.

**Partial sets / atomicity — set-atomic publish**: each part streams to
`<name>.part` in `gdrive_downloads_dir` (invisible to the watcher: its suffix filter only
matches the three archive extensions), and **rename happens only after every part of the
set has downloaded and md5-verified**. The watcher's two-scan debounce would tolerate
staggered finishes anyway, but on a slow link a multi-GB gap between parts exceeds any
debounce; renaming the completed set together means the watcher sees only whole sets
(the per-part re-import-then-dedup path stays as the safety net it already is for manual
drops, rather than the normal path). Downloads settle fully before the set is published,
per the brief.

## 6. Remote pruning

**Decision — ship in v1, hard-disabled by default**: `gdrive_prune: bool = False` in
config. The eligibility gate, evaluated on a later puller cycle (never inline with the
download): every part of a set has a `gdrive_pulls` row, **and** the set's import is
verified successful by joining against the persisted `imports` runs (rows carry `path`,
`file_hash`, `status` — match on the set's local paths with `status = 'completed'`, the
schema's actual CHECK value), **and**
the `drive` scope was granted (recorded in the token file, §3). Only then are the exact
recorded `file_id`s deleted, one `files.delete` each, `pruned_at` stamped per file;
anything else in the Takeout folder is never touched. `gdrive_prune = true` without the
scope is a status error ("re-auth with `potluck gdrive auth --prune`"), never an implicit
escalation.

**`files.delete` (permanent), not trash**: trashed files still count against Drive quota
for 30 days, and quota pressure from multi-GB bimonthly archives is the *only* reason
pruning exists — trashing would defeat the point while looking safer than it is. The
destructive nature is documented loudly in config comments, setup guide, and the auth
command's `--prune` help text; the local archive and the imported items both still exist,
so the blast radius of a worst-case bug is bounded to "the Drive copy of an
already-imported export".

**Why ship it at all**: the marginal surface is one endpoint, one eligibility query, and
one config flag — all fully covered by the mock-Drive tier — and without it the feature
quietly fills the user's Drive quota until Takeout exports start failing. Weighed against
deferring: cutting it would save little and leave the acceptance list incomplete.

## 7. Mock-Drive testing

**Decision**: an `httpx.MockTransport` handler fixture (in `tests/conftest.py` or a
`tests/gdrive/` conftest — test-only code, not `potluck.testing`, which ships synthetic
*generators*) implementing a tiny in-memory Drive: folder + file listings with pagination,
`alt=media` bodies, `files.delete`, and the token endpoint (refresh success,
`invalid_grant`, 429/403-with-Retry-After, 5xx). `DriveClient` takes an injectable
`transport` (the same seam `potluck.testing.server`-style code uses), so no
monkeypatching. Archive bytes served by the mock come from the existing
`potluck.testing` generators, so downloads are *real importable archives*.

**The acceptance integration test** — list → download → ingest → record: seed the mock
with a two-part synthetic Takeout set; `puller.run_cycle()` (synchronous, no thread) →
assert `.part`-then-rename landing in `gdrive_downloads_dir`; two `watcher.run_cycle()`
calls (debounce) → import claims and completes; assert items queryable, `gdrive_pulls`
rows recorded; then flip `gdrive_prune` + granted scope → next cycle issues exactly the
recorded ids' deletes. Companion tests: token file perms `(st_mode & 0o777) == 0o600`,
refresh-failure → `reauth_required` without thread death, thread start/stop/join leak
hygiene under `-n auto` (the #151 lifecycle tests are the template). **No network, ever**:
nothing in the suite constructs a real transport; CI would catch a regression because no
Google host resolves in the sandbox anyway.

## 8. Failure modes

| Failure | Behavior |
|---|---|
| 429 / 403 `rateLimitExceeded` | Honor `Retry-After` if present; park the cycle with the watcher's exponential skip-cycles idiom (1, 2, 4 … capped). Never per-request retry loops inside a cycle. |
| Access token expired (401) | One lazy refresh, replay the request once; persistent 401 → error state, backoff. |
| Refresh token dead (`invalid_grant`) | `auth_state="reauth_required"`, Drive calls stop, status carries re-auth instructions (§3). Token file kept. |
| Drive unreachable (offline laptop) | Quiet skip: `connectivity="offline"` status field + debug log, retry next cycle. **Not** `last_error` — offline is a normal state for a local-first app, and alarming red text daily would train users to ignore the field that matters. |
| Token-endpoint / Drive 5xx | Transient: cycle backoff, `last_error` only after repeated consecutive failures. |
| Huge archives | Always streamed to disk in chunks (`client.stream` → `.part` file); nothing archive-sized in memory, matching the 10 GiB upload-path posture. |
| Interrupted download | **Resume via `Range: bytes=<part-size>-`** into the existing `.part` (Drive's `alt=media` supports Range), then full-file md5 verify against the listed `md5Checksum`; verify-fail or 416 → delete `.part`, restart from zero, count a failure toward backoff. Resume costs ~10 lines and saves multi-GB re-pulls on flaky links — worth owning. |
| Disk full (`ENOSPC`) | Remove the `.part`, surface `last_error`, backoff — never leave the partial file to confuse later Range resumes. |
| Cycle-level surprise | Same contract as `FolderWatcher._run`: catch-all → log + `last_error`; the thread never dies mid-serve. |

Status surface: a `gdrive` section beside the watch status (extend `GET /api/watch` or a
parallel `GET /api/gdrive` + `potluck gdrive status` — Phase B picks based on DTO fit),
reporting enabled/auth_state/connectivity/last_check_at/last_pull_at/last_error/pending
downloads. Mirrors `WatchStatus` so the SPA card pattern is reusable if trivial.

## 9. Setup guide outline (`docs/gdrive-setup.md`, Phase B)

1. **Why you supply your own OAuth client** — restricted-scope verification economics; you
   own the client, you are its only user; what Google's "unverified app" screen means here.
2. **Create a Google Cloud project** (console.cloud.google.com → New Project).
3. **Enable the Drive API** (APIs & Services → Library → "Google Drive API" → Enable).
4. **OAuth consent screen**: External; app name/support email; **Publish app → In
   production** — with the explicit warning that "Testing" status expires refresh tokens
   after 7 days and *will* silently break a 2-month pull cadence.
5. **Create the client**: Credentials → Create credentials → OAuth client ID → **Desktop
   app**; copy client ID + secret.
6. **Configure Potluck**: flat top-level `gdrive_*` keys in `config.toml` (exact snippet —
   the `watch_*` family shape; a TOML `[gdrive]` section would not map to the flat
   pydantic-settings fields), or `POTLUCK_GDRIVE_*` env vars; optional `gdrive_prune`,
   folder name, interval.
7. **Authorize**: `potluck gdrive auth` (add `--prune` for delete rights, destructive-use
   warning); the unverified-app interstitial walkthrough (Advanced → continue); token file
   location + 0600 note; `--no-browser` and copy-the-token-file paths for headless serves.
8. **Schedule the export**: takeout.google.com → select data → *Add to Drive* → *Export
   every 2 months*; where the Takeout folder appears.
9. **Verify**: `potluck gdrive status` / watch page; what a first successful pull looks
   like; pointer to re-auth instructions when status says so.

Screenshots-level step detail per the acceptance; each console step written to be
followable without screenshots too (Google's console layout drifts; text anchored on
labels survives that better than pixels — see the honest-validation note at the top).

## Non-goals (v1, documented not forgotten)

Multiple Google accounts; Shared Drives (`supportsAllDrives`); non-Takeout Drive pulls;
a settings-UI OAuth flow (§2); inotify-style push (Drive changes/watch channels need a
public HTTPS endpoint — a polling local app has none); keyring/keychain token storage.
