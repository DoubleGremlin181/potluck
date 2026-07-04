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

async function request<T>(path: string): Promise<T> {
  const res = await fetch(path)
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

export function searchItems(query: SearchQuery): Promise<SearchResponse> {
  const params = new URLSearchParams({ q: query.q })
  for (const kind of query.kinds ?? []) params.append('kind', kind)
  for (const source of query.sources ?? []) params.append('source', source)
  if (query.after) params.set('after', query.after)
  if (query.before) params.set('before', query.before)
  if (query.prefix) params.set('prefix', 'true')
  if (query.cursor) params.set('cursor', query.cursor)
  if (query.limit !== undefined) params.set('limit', String(query.limit))
  return request<SearchResponse>(`/api/search?${params.toString()}`)
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

export function fetchItem(id: number): Promise<Item> {
  return request<Item>(`/api/items/${id}`)
}

// ---------------------------------------------------------------------------
// GET /api/sources
// ---------------------------------------------------------------------------

export interface SourceInfo {
  name: string
  kinds: ItemKind[]
}

export function fetchSources(): Promise<SourceInfo[]> {
  return request<SourceInfo[]>('/api/sources')
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
  sources: number
  imports: number
}

export function fetchStats(): Promise<Stats> {
  return request<Stats>('/api/stats')
}
