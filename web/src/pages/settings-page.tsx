import { useQuery } from '@tanstack/react-query'
import { Monitor, Moon, Sun } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { fetchStats } from '@/lib/api'
import { formatBytes } from '@/lib/format'
import { useTheme, type Theme } from '@/lib/theme-context'

const THEME_CHOICES: { value: Theme; label: string; icon: typeof Sun }[] = [
  { value: 'light', label: 'Light', icon: Sun },
  { value: 'dark', label: 'Dark', icon: Moon },
  { value: 'system', label: 'System', icon: Monitor },
]

function StatCard({ label, value }: { label: string; value: string }) {
  return (
    <Card size="sm">
      <CardHeader>
        <CardDescription>{label}</CardDescription>
        <CardTitle className="text-2xl tabular-nums">{value}</CardTitle>
      </CardHeader>
    </Card>
  )
}

/** Settings: appearance plus the database overview (the P0 stats page). */
export function SettingsPage() {
  const { theme, setTheme } = useTheme()
  const { data: stats, error, isPending } = useQuery({
    queryKey: ['stats'],
    queryFn: ({ signal }) => fetchStats(signal),
  })

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-3xl space-y-8 p-4 md:p-6">
        <h2 className="text-xl font-semibold tracking-tight">Settings</h2>

        <section className="space-y-3">
          <div>
            <h3 className="text-sm font-medium">Appearance</h3>
            <p className="text-sm text-muted-foreground">How Potluck looks on this device.</p>
          </div>
          <div className="flex gap-2">
            {THEME_CHOICES.map(({ value, label, icon: Icon }) => (
              <Button
                key={value}
                variant={theme === value ? 'default' : 'outline'}
                size="sm"
                aria-pressed={theme === value}
                onClick={() => setTheme(value)}
              >
                <Icon aria-hidden className="size-4" />
                {label}
              </Button>
            ))}
          </div>
        </section>

        <section className="space-y-3">
          <div>
            <h3 className="text-sm font-medium">Database</h3>
            <p className="text-sm text-muted-foreground">
              Everything lives in one local SQLite file.
            </p>
          </div>
          {error ? (
            <Card className="border-destructive">
              <CardHeader>
                <CardTitle>Cannot reach the Potluck API</CardTitle>
                <CardDescription>
                  {error instanceof Error ? error.message : String(error)}
                </CardDescription>
              </CardHeader>
            </Card>
          ) : isPending || !stats ? (
            <div className="grid grid-cols-2 gap-4 md:grid-cols-3">
              {Array.from({ length: 6 }, (_, i) => (
                <Skeleton key={i} className="h-24 w-full rounded-xl" />
              ))}
            </div>
          ) : (
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
        </section>
      </div>
    </div>
  )
}
