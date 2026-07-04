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
