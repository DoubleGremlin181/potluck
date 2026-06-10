import { useEffect, useState } from 'react'
import { Card, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { ThemeToggle } from '@/components/theme-toggle'
import { fetchStats, type Stats } from '@/lib/api'

function formatBytes(n: number): string {
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

function StatCard({ label, value }: { label: string; value: string }) {
  return (
    <Card>
      <CardHeader>
        <CardDescription>{label}</CardDescription>
        <CardTitle className="text-3xl tabular-nums">{value}</CardTitle>
      </CardHeader>
    </Card>
  )
}

export function StatsPage() {
  const [stats, setStats] = useState<Stats | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetchStats()
      .then(setStats)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)))
  }, [])

  return (
    <div className="min-h-screen bg-background text-foreground">
      <header className="flex items-center justify-between border-b px-6 py-4">
        <h1 className="text-xl font-semibold tracking-tight">Potluck</h1>
        <div className="flex items-center gap-3">
          {stats && <span className="text-sm text-muted-foreground">v{stats.version}</span>}
          <ThemeToggle />
        </div>
      </header>
      <main className="mx-auto max-w-4xl space-y-6 p-6">
        {error && (
          <Card className="border-destructive">
            <CardHeader>
              <CardTitle>Cannot reach the Potluck API</CardTitle>
              <CardDescription>{error}</CardDescription>
            </CardHeader>
          </Card>
        )}
        {!error && !stats && <p className="text-muted-foreground">Loading…</p>}
        {stats && (
          <>
            <div className="grid grid-cols-2 gap-4 md:grid-cols-3">
              <StatCard label="Items" value={stats.items.toLocaleString()} />
              <StatCard label="Sources" value={stats.sources.toLocaleString()} />
              <StatCard label="Imports" value={stats.imports.toLocaleString()} />
              <StatCard label="Database size" value={formatBytes(stats.db_size_bytes)} />
              <StatCard label="Schema version" value={String(stats.schema_version)} />
              <StatCard label="App version" value={stats.version} />
            </div>
            <p className="text-sm text-muted-foreground">
              Database: <code className="break-all">{stats.db_path}</code>
            </p>
          </>
        )}
      </main>
    </div>
  )
}
