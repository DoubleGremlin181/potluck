/** Typed client for the Potluck REST API.
 *
 * Interfaces mirror the Pydantic DTOs in `src/potluck/models/` by hand (no
 * codegen for MVP). Every non-2xx `/api/*` response carries the uniform
 * `{"error": {code, message}}` envelope, surfaced here as `ApiError`.
 */

export const ITEM_KINDS = [
  'note',
  'email',
  'message',
  'photo',
  'file',
  'event',
  'contact',
  'location',
  'transaction',
  'bookmark',
  'post',
  'activity',
] as const

export type ItemKind = (typeof ITEM_KINDS)[number]

export function isItemKind(value: string): value is ItemKind {
  return (ITEM_KINDS as readonly string[]).includes(value)
}

interface ErrorEnvelope {
  error: {
    code: string
    message: string
    detail?: unknown[]
  }
}

export class ApiError extends Error {
  readonly status: number
  readonly code: string

  constructor(status: number, code: string, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  // The signal (threaded via init) comes from react-query: superseded queries
  // (rapid typing) abort their in-flight requests instead of racing them to
  // completion.
  const res = await fetch(path, init)
  if (!res.ok) {
    let code = `http_${res.status}`
    let message = `Request failed with status ${res.status}`
    try {
      const body = (await res.json()) as ErrorEnvelope
      code = body.error.code
      message = body.error.message
    } catch {
      // Non-JSON body (proxy error page, …): keep the generic message.
    }
    throw new ApiError(res.status, code, message)
  }
  return res.json() as Promise<T>
}

// ---------------------------------------------------------------------------
// GET /api/search
// ---------------------------------------------------------------------------

export interface SearchHit {
  id: number
  kind: ItemKind
  /** Name of the source the item was ingested from (e.g. "gmail"). */
  source: string
  title: string | null
  /** Title with `[match]` brackets around matched terms; null for filter-only searches. */
  title_highlight: string | null
  /** Snippet with `[match]` brackets; render via parseHighlight, never as HTML. */
  snippet: string
  score: number
  ts: string | null
}

export interface SearchResponse {
  query: string
  hits: SearchHit[]
  /** Pass back verbatim as `cursor` — with every other parameter unchanged. */
  next_cursor: string | null
  /** Inline operator values that were ignored (typos etc.); search ran without them. */
  warnings: string[]
}

export interface SearchQuery {
  q: string
  kinds?: ItemKind[]
  sources?: string[]
  /** ISO date (YYYY-MM-DD), inclusive lower bound on ts. */
  after?: string
  /** ISO date (YYYY-MM-DD), exclusive upper bound on ts. */
  before?: string
  /** Search-as-you-type: the last query token matches as a prefix. */
  prefix?: boolean
  cursor?: string | null
  limit?: number
}

export function searchItems(query: SearchQuery, signal?: AbortSignal): Promise<SearchResponse> {
  const params = new URLSearchParams({ q: query.q })
  for (const kind of query.kinds ?? []) params.append('kind', kind)
  for (const source of query.sources ?? []) params.append('source', source)
  if (query.after) params.set('after', query.after)
  if (query.before) params.set('before', query.before)
  if (query.prefix) params.set('prefix', 'true')
  if (query.cursor) params.set('cursor', query.cursor)
  if (query.limit !== undefined) params.set('limit', String(query.limit))
  return request<SearchResponse>(`/api/search?${params.toString()}`, { signal })
}

// ---------------------------------------------------------------------------
// GET /api/items/{id}
// ---------------------------------------------------------------------------

export interface AttachmentDetail {
  filename: string
  mime: string | null
  size_bytes: number | null
  sha256: string | null
}

export interface EmailDetail {
  message_id: string | null
  in_reply_to: string | null
  thread_key: string
  from_addr: string | null
  from_name: string | null
  to_addrs: string[]
  to_names: string[]
  cc_addrs: string[]
  cc_names: string[]
  bcc_addrs: string[]
  labels: string[]
  attachments: AttachmentDetail[]
}

export interface Item {
  id: number
  source: string
  import_id: number
  kind: ItemKind
  external_id: string | null
  content_hash: string
  ts: string | null
  title: string | null
  text: string | null
  lat: number | null
  lon: number | null
  parent_id: number | null
  meta: Record<string, unknown>
  email: EmailDetail | null
}

export function fetchItem(id: number, signal?: AbortSignal): Promise<Item> {
  return request<Item>(`/api/items/${id}`, { signal })
}

// ---------------------------------------------------------------------------
// GET /api/items/{id}/thread
// ---------------------------------------------------------------------------

export interface ThreadEntry {
  id: number
  /** Reply-tree link to another entry's id; null for the thread root. */
  parent_id: number | null
  ts: string | null
  title: string | null
  from_addr: string | null
  /** First 200 chars of the message text. */
  text_preview: string | null
}

export interface ThreadResponse {
  item_id: number
  /** Null when the item is not part of any email thread (entries then hold just the item). */
  thread_key: string | null
  /** Oldest message first; undated members last. */
  entries: ThreadEntry[]
}

export function fetchThread(id: number, signal?: AbortSignal): Promise<ThreadResponse> {
  return request<ThreadResponse>(`/api/items/${id}/thread`, { signal })
}

// ---------------------------------------------------------------------------
// GET /api/sources
// ---------------------------------------------------------------------------

export interface SourceInfo {
  name: string
  kinds: ItemKind[]
}

export function fetchSources(signal?: AbortSignal): Promise<SourceInfo[]> {
  return request<SourceInfo[]>('/api/sources', { signal })
}

// ---------------------------------------------------------------------------
// GET /api/stats
// ---------------------------------------------------------------------------

export interface Stats {
  version: string
  schema_version: number
  db_path: string
  db_size_bytes: number
  items: number
  /** Nonzero kinds only, largest count first (server-ordered). */
  items_by_kind: Partial<Record<ItemKind, number>>
  sources: number
  imports: number
}

export function fetchStats(signal?: AbortSignal): Promise<Stats> {
  return request<Stats>('/api/stats', { signal })
}

// ---------------------------------------------------------------------------
// Imports: POST /api/imports (+ /upload), GET /api/imports (+ /status)
// ---------------------------------------------------------------------------

export type ImportStatus = 'running' | 'completed' | 'failed'

/** One import-ledger row (mirrors `models/imports.py::ImportRun`). */
export interface ImportRun {
  id: number
  source: string
  /** Server-local archive path (for uploads: inside the managed uploads dir). */
  path: string
  file_hash: string | null
  parser_version: number
  started_at: string
  finished_at: string | null
  status: ImportStatus
  items_new: number
  items_duplicate: number
  items_updated: number
  items_skipped: number
  /** Drafts dropped because their content was forgotten (suppressed_hashes). */
  items_suppressed: number
  /** Progress denominator; null = unknown (streaming sources never pre-count). */
  items_total: number | null
  error: string | null
  extract_attachments: boolean
  /** Progress numerator, advanced once per committed batch (server-computed). */
  items_done: number
}

/** Snapshot of the background import operation (mirrors `ImportTask`).
 * Covers the whole operation, including archive detection BEFORE any ledger
 * row exists — detection failures only ever surface here. */
export interface ImportTask {
  path: string
  status: ImportStatus
  error: string | null
  /** Per-source ledger row ids; filled in on completion. */
  import_ids: number[]
  started_at: string
  finished_at: string | null
}

export interface ImportListResponse {
  runs: ImportRun[]
  /** Unpaginated run count — pages exist while offset < total. */
  total: number
}

/** Current (or most recent) background import; null before any start. */
export function fetchImportStatus(signal?: AbortSignal): Promise<ImportTask | null> {
  return request<ImportTask | null>('/api/imports/status', { signal })
}

export function fetchImports(
  params: { limit?: number; offset?: number },
  signal?: AbortSignal,
): Promise<ImportListResponse> {
  const search = new URLSearchParams()
  if (params.limit !== undefined) search.set('limit', String(params.limit))
  if (params.offset !== undefined) search.set('offset', String(params.offset))
  const qs = search.toString()
  return request<ImportListResponse>(`/api/imports${qs ? `?${qs}` : ''}`, { signal })
}

/** Start a background import of an archive already on the server's machine.
 * 202 returns the initial task snapshot; 400 = bad path, 409 = one already runs. */
export function startPathImport(path: string): Promise<ImportTask> {
  return request<ImportTask>('/api/imports', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path }),
  })
}

/** Upload an archive into the managed uploads dir, then import it exactly
 * like the path variant. 409 = one already runs, 413 = over max_upload_bytes. */
export function startUploadImport(file: File): Promise<ImportTask> {
  const form = new FormData()
  form.append('file', file)
  return request<ImportTask>('/api/imports/upload', { method: 'POST', body: form })
}

/** Counts from one delete call (mirrors `models/lifecycle.py::RemoveResult`). */
export interface RemoveResult {
  items_deleted: number
  imports_deleted: number
  hashes_suppressed: number
}

/** Delete an import run and every item it ingested. Plain delete lets a
 * re-import of the same archive restore the content; `forget` also blocks
 * the deleted content from ever re-importing. 404 = unknown id, 409 = the
 * run is still running. */
export function deleteImport(id: number, forget: boolean): Promise<RemoveResult> {
  return request<RemoveResult>(`/api/imports/${id}?forget=${forget}`, { method: 'DELETE' })
}

// ---------------------------------------------------------------------------
// Watch folders: GET /api/watch, PATCH /api/watch
// ---------------------------------------------------------------------------

export interface WatchFolder {
  path: string
  /** False = configured but missing on disk (warned by the server, skipped). */
  exists: boolean
}

export interface WatchPendingSet {
  /** Representative file of the archive set (first part of a multi-part drop). */
  path: string
  /** 'stabilizing' = waiting out the copy-in-progress debounce; 'backoff' = a
   * failed import cooling down before its retry. */
  state: 'stabilizing' | 'backoff'
  /** Backoff only: polling cycles left before the retry. */
  retry_in_cycles: number | null
}

/** Mirrors `models/watch.py::WatchStatus`. Folders and interval are
 * config-file-owned (edit config.toml); only `enabled` is togglable here. */
export interface WatchStatus {
  enabled: boolean
  /** 'runtime' when a persisted toggle overrides the config default. */
  effective_enabled_source: 'config' | 'runtime'
  interval_s: number
  folders: WatchFolder[]
  /** Null before the first scan — and always null unless `potluck serve` runs. */
  last_scan_at: string | null
  pending: WatchPendingSet[]
  last_error: string | null
}

export function fetchWatchStatus(signal?: AbortSignal): Promise<WatchStatus> {
  return request<WatchStatus>('/api/watch', { signal })
}

/** Persist the runtime enable/disable toggle; returns the new status. */
export function setWatchEnabled(enabled: boolean): Promise<WatchStatus> {
  return request<WatchStatus>('/api/watch', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  })
}
