export interface Stats {
  version: string
  schema_version: number
  db_path: string
  db_size_bytes: number
  items: number
  sources: number
  imports: number
}

export async function fetchStats(): Promise<Stats> {
  const res = await fetch('/api/stats')
  if (!res.ok) {
    throw new Error(`GET /api/stats failed with status ${res.status}`)
  }
  return res.json() as Promise<Stats>
}
