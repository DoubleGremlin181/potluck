/** Shared display formatters. */

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`
  let value = n
  let unit = 'B'
  for (const next of ['KiB', 'MiB', 'GiB', 'TiB']) {
    value /= 1024
    unit = next
    if (value < 1024) break
  }
  return `${value.toFixed(1)} ${unit}`
}

export function formatDate(iso: string | null): string | null {
  if (iso === null) return null
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return null
  return date.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
}

/** Last path segment, for display ("/exports/takeout-001.zip" → "takeout-001.zip"). */
export function basename(path: string): string {
  const trimmed = path.replace(/[/\\]+$/, '')
  const idx = Math.max(trimmed.lastIndexOf('/'), trimmed.lastIndexOf('\\'))
  const name = idx === -1 ? trimmed : trimmed.slice(idx + 1)
  return name || path
}

/** Elapsed time between two instants; end = null means "still running" (now). */
export function formatDuration(startIso: string, endIso: string | null): string | null {
  const start = new Date(startIso).getTime()
  const end = endIso === null ? Date.now() : new Date(endIso).getTime()
  if (Number.isNaN(start) || Number.isNaN(end)) return null
  const totalSeconds = Math.round(Math.max(0, end - start) / 1000)
  if (totalSeconds < 1) return '<1s'
  if (totalSeconds < 60) return `${totalSeconds}s`
  const minutes = Math.floor(totalSeconds / 60)
  if (minutes < 60) return `${minutes}m ${totalSeconds % 60}s`
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`
}

/** Date plus time of day — thread messages need intra-day ordering context. */
export function formatDateTime(iso: string | null): string | null {
  if (iso === null) return null
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return null
  return date.toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}
