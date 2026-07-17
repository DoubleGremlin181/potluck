import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Check,
  Copy,
  ExternalLink,
  FolderSearch,
  Monitor,
  Moon,
  Sparkles,
  Sun,
} from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router'
import { KindIcon } from '@/components/kind-icon'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import {
  fetchSources,
  fetchStats,
  fetchWatchStatus,
  ITEM_KINDS,
  setWatchEnabled,
  type ItemKind,
  type Stats,
  type WatchStatus,
} from '@/lib/api'
import { formatBytes, formatDateTime } from '@/lib/format'
import { useTheme, type Theme } from '@/lib/theme-context'

const REPO_URL = 'https://github.com/DoubleGremlin181/potluck'

const THEME_CHOICES: { value: Theme; label: string; icon: typeof Sun }[] = [
  { value: 'light', label: 'Light', icon: Sun },
  { value: 'dark', label: 'Dark', icon: Moon },
  { value: 'system', label: 'System', icon: Monitor },
]

function Section({
  title,
  description,
  children,
}: {
  title: string
  description: string
  children: React.ReactNode
}) {
  return (
    <section className="space-y-3">
      <div>
        <h3 className="text-sm font-medium">{title}</h3>
        <p className="text-sm text-muted-foreground">{description}</p>
      </div>
      {children}
    </section>
  )
}

function StatCard({ label, value }: { label: string; value: string }) {
  return (
    <Card size="sm">
      <CardHeader>
        <CardDescription>{label}</CardDescription>
        <CardTitle className="text-2xl">{value}</CardTitle>
      </CardHeader>
    </Card>
  )
}

/** Copies *text* to the clipboard, flipping to a check mark briefly. */
function CopyButton({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined)
  useEffect(() => () => clearTimeout(timer.current), [])

  async function copy() {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      clearTimeout(timer.current)
      timer.current = setTimeout(() => setCopied(false), 1500)
    } catch {
      // Clipboard unavailable (non-secure context): the path is selectable text.
    }
  }

  return (
    <Button
      variant="ghost"
      size="icon-xs"
      aria-label={label}
      className="shrink-0 text-muted-foreground"
      onClick={() => void copy()}
    >
      {copied ? <Check aria-hidden className="text-primary" /> : <Copy aria-hidden />}
    </Button>
  )
}

function ApiErrorCard({ error }: { error: unknown }) {
  return (
    <Card size="sm" className="ring-destructive/40">
      <CardHeader>
        <CardTitle>Cannot reach the Potluck API</CardTitle>
        <CardDescription>
          {error instanceof Error ? error.message : String(error)}
        </CardDescription>
      </CardHeader>
    </Card>
  )
}

function DatabaseSection({
  stats,
  error,
  isPending,
}: {
  stats: Stats | undefined
  error: unknown
  isPending: boolean
}) {
  return (
    <Section title="Database" description="Everything lives in one local SQLite file.">
      {error ? (
        <ApiErrorCard error={error} />
      ) : isPending || !stats ? (
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {Array.from({ length: 4 }, (_, i) => (
              <Skeleton key={i} className="h-[5.5rem] w-full rounded-xl" />
            ))}
          </div>
          <Skeleton className="h-16 w-full rounded-xl" />
        </div>
      ) : (
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <StatCard label="Items" value={stats.items.toLocaleString()} />
            <StatCard label="Size" value={formatBytes(stats.db_size_bytes)} />
            <StatCard label="Sources" value={stats.sources.toLocaleString()} />
            <StatCard label="Imports" value={stats.imports.toLocaleString()} />
          </div>
          <Card size="sm">
            <CardContent className="flex items-center justify-between gap-3">
              <div className="min-w-0 space-y-1">
                <p className="text-xs text-muted-foreground">Location</p>
                <code data-testid="db-path" className="block text-[13px] break-all">
                  {stats.db_path}
                </code>
              </div>
              <CopyButton text={stats.db_path} label="Copy database path" />
            </CardContent>
          </Card>
          <p className="text-xs text-muted-foreground">Schema version {stats.schema_version}</p>
        </div>
      )}
    </Section>
  )
}

function KindBar({ kind, count, max }: { kind: ItemKind; count: number; max: number }) {
  // Floor tiny shares at 1.5% so every nonzero kind shows a visible sliver.
  const pct = Math.max((count / max) * 100, 1.5)
  return (
    <div
      data-testid="kind-count"
      data-kind={kind}
      data-count={count}
      className="flex items-center gap-3"
    >
      <span className="flex w-28 shrink-0 items-center gap-2 text-sm capitalize">
        <KindIcon kind={kind} className="size-4 shrink-0 text-muted-foreground" />
        {kind}
      </span>
      <span aria-hidden className="h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-muted">
        <span className="block h-full rounded-full bg-primary" style={{ width: `${pct}%` }} />
      </span>
      <span className="w-16 shrink-0 text-right text-sm tabular-nums">
        {count.toLocaleString()}
      </span>
    </div>
  )
}

function ItemsByKindSection({ stats, isPending }: { stats: Stats | undefined; isPending: boolean }) {
  return (
    <Section title="Items by kind" description="What the database holds right now.">
      {isPending ? (
        <Skeleton className="h-24 w-full rounded-xl" />
      ) : !stats ? (
        <p className="text-sm text-muted-foreground">Item counts are unavailable.</p>
      ) : stats.items === 0 ? (
        <Card size="sm">
          <CardContent className="text-sm text-muted-foreground">
            No items yet — bring a dish!{' '}
            <Link to="/imports" className="font-medium text-foreground underline underline-offset-4">
              Import an archive
            </Link>{' '}
            to get started.
          </CardContent>
        </Card>
      ) : (
        <KindBreakdown byKind={stats.items_by_kind} />
      )}
    </Section>
  )
}

function KindBreakdown({ byKind }: { byKind: Stats['items_by_kind'] }) {
  // Server order: largest count first.
  const present = Object.entries(byKind) as [ItemKind, number][]
  const zeroKinds = ITEM_KINDS.filter((kind) => !(kind in byKind))
  const max = present[0]?.[1] ?? 1
  return (
    <div className="space-y-3">
      <Card size="sm">
        <CardContent className="space-y-2.5">
          {present.map(([kind, count]) => (
            <KindBar key={kind} kind={kind} count={count} max={max} />
          ))}
        </CardContent>
      </Card>
      {zeroKinds.length > 0 && (
        <p data-testid="zero-kinds" className="text-xs text-muted-foreground">
          Nothing yet under {zeroKinds.join(', ')}.
        </p>
      )}
    </div>
  )
}

function SourcesSection() {
  const { data: sources, error, isPending } = useQuery({
    queryKey: ['sources'],
    queryFn: ({ signal }) => fetchSources(signal),
  })
  return (
    <Section title="Sources" description="Installed source plugins and the item kinds they produce.">
      {error ? (
        <ApiErrorCard error={error} />
      ) : isPending || !sources ? (
        <Skeleton className="h-24 w-full rounded-xl" />
      ) : sources.length === 0 ? (
        <p className="text-sm text-muted-foreground">No source plugins are registered.</p>
      ) : (
        <Card size="sm">
          <CardContent className="divide-y">
            {sources.map((source) => (
              <div
                key={source.name}
                data-testid="source-row"
                data-source={source.name}
                className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1.5 py-2.5 first:pt-0 last:pb-0"
              >
                <code className="text-[13px] font-medium">{source.name}</code>
                <span className="flex flex-wrap gap-1.5">
                  {source.kinds.map((kind) => (
                    <Badge key={kind} variant="secondary" className="capitalize">
                      <KindIcon kind={kind} className="size-3" />
                      {kind}
                    </Badge>
                  ))}
                </span>
              </div>
            ))}
          </CardContent>
        </Card>
      )}
    </Section>
  )
}

/** Refresh cadence for the watch status while the page is open: runtime
 * fields (last scan, pending drops) move on their own server-side. */
const WATCH_POLL_MS = 5000

function WatchFolderRow({ path, exists }: { path: string; exists: boolean }) {
  return (
    <div
      data-testid="watch-folder"
      data-path={path}
      data-exists={exists}
      className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1.5 py-2 first:pt-0 last:pb-0"
    >
      <code className="min-w-0 text-[13px] break-all">{path}</code>
      {exists ? (
        <Badge variant="outline" className="gap-1">
          <FolderSearch aria-hidden className="size-3 text-emerald-600 dark:text-emerald-400" />
          watching
        </Badge>
      ) : (
        <Badge variant="destructive">missing</Badge>
      )}
    </div>
  )
}

function WatchPendingList({ status }: { status: WatchStatus }) {
  if (status.pending.length === 0) return null
  return (
    <div data-testid="watch-pending" className="space-y-1 border-t pt-3">
      <p className="text-xs font-medium">Waiting to import</p>
      {status.pending.map((set) => (
        <p key={set.path} className="text-xs text-muted-foreground">
          <code className="break-all">{set.path}</code>{' '}
          {set.state === 'backoff'
            ? `— failed, retrying in ${set.retry_in_cycles ?? '?'} scan${set.retry_in_cycles === 1 ? '' : 's'}`
            : '— waiting for the file to finish copying'}
        </p>
      ))}
    </div>
  )
}

function WatchCard({ status }: { status: WatchStatus }) {
  const queryClient = useQueryClient()
  const mutation = useMutation({
    mutationFn: setWatchEnabled,
    // Optimistic flip: the switch answers immediately; the server response
    // (or an error rollback) reconciles right after.
    onMutate: async (enabled: boolean) => {
      await queryClient.cancelQueries({ queryKey: ['watch'] })
      const previous = queryClient.getQueryData<WatchStatus>(['watch'])
      if (previous) {
        queryClient.setQueryData<WatchStatus>(['watch'], {
          ...previous,
          enabled,
          effective_enabled_source: 'runtime',
        })
      }
      return { previous }
    },
    onError: (_error, _enabled, context) => {
      if (context?.previous) queryClient.setQueryData(['watch'], context.previous)
    },
    onSuccess: (fresh) => queryClient.setQueryData(['watch'], fresh),
    onSettled: () => void queryClient.invalidateQueries({ queryKey: ['watch'] }),
  })

  return (
    <Card size="sm" data-testid="watch-card">
      <CardContent className="space-y-3">
        <div className="flex items-center justify-between gap-3">
          <div className="space-y-0.5">
            <p className="text-sm font-medium">Auto-import new archives</p>
            <p className="text-xs text-muted-foreground">
              Archives dropped into the folders below import on their own while Potluck runs.
            </p>
          </div>
          <Switch
            data-testid="watch-toggle"
            aria-label="Auto-import new archives"
            checked={status.enabled}
            disabled={mutation.isPending}
            onCheckedChange={(enabled) => mutation.mutate(enabled)}
          />
        </div>
        <div className="divide-y border-t pt-3">
          {status.folders.map((folder) => (
            <WatchFolderRow key={folder.path} path={folder.path} exists={folder.exists} />
          ))}
        </div>
        <p className="text-xs text-muted-foreground">
          Folders and the scan interval (every {status.interval_s}s) are set in{' '}
          <code>config.toml</code> — edit it to change what is watched.
          {status.last_scan_at && ` Last scan ${formatDateTime(status.last_scan_at)}.`}
        </p>
        <WatchPendingList status={status} />
        {status.last_error && (
          <p
            data-testid="watch-error"
            className="rounded-md bg-destructive/10 px-3 py-2 font-mono text-xs break-all text-destructive"
          >
            {status.last_error}
          </p>
        )}
        <p className="text-xs text-muted-foreground">
          Auto-imports show up on the{' '}
          <Link
            to="/imports"
            className="font-medium text-foreground underline underline-offset-4"
          >
            Imports
          </Link>{' '}
          page like any other import.
        </p>
      </CardContent>
    </Card>
  )
}

function WatchSection() {
  const { data: status, error, isPending } = useQuery({
    queryKey: ['watch'],
    queryFn: ({ signal }) => fetchWatchStatus(signal),
    refetchInterval: WATCH_POLL_MS,
  })
  return (
    <Section
      title="Watch folders"
      description="Drop export archives into a watched folder and Potluck imports them automatically."
    >
      {error ? (
        <ApiErrorCard error={error} />
      ) : isPending || !status ? (
        <Skeleton className="h-24 w-full rounded-xl" />
      ) : status.folders.length === 0 ? (
        <Card size="sm" data-testid="watch-empty" className="bg-muted/40 shadow-none">
          <CardContent className="text-sm text-muted-foreground">
            No watch folders configured. Add <code>watch_folders</code> to{' '}
            <code>config.toml</code> and restart to enable auto-import.
          </CardContent>
        </Card>
      ) : (
        <WatchCard status={status} />
      )}
    </Section>
  )
}

function AboutSection({ version }: { version: string | undefined }) {
  return (
    <Section title="About" description="This Potluck installation.">
      <Card size="sm">
        <CardContent className="grid grid-cols-[6rem_1fr] gap-x-4 gap-y-2.5 text-sm">
          <span className="text-muted-foreground">Version</span>
          <span data-testid="app-version" className="font-medium">
            {version ?? '—'}
          </span>
          <span className="text-muted-foreground">Privacy</span>
          <span>
            Serves on localhost only — your data never leaves this machine.
          </span>
          <span className="text-muted-foreground">Source</span>
          <a
            href={REPO_URL}
            target="_blank"
            rel="noreferrer"
            className="flex w-fit items-center gap-1 font-medium underline underline-offset-4 hover:text-muted-foreground"
          >
            DoubleGremlin181/potluck
            <ExternalLink aria-hidden className="size-3.5" />
          </a>
        </CardContent>
      </Card>
    </Section>
  )
}

function EnrichmentSection() {
  return (
    <Section title="Enrichment" description="Derived data computed on top of your items.">
      <Card size="sm" data-testid="enrichment-placeholder" className="bg-muted/40 shadow-none">
        <CardContent className="flex items-start gap-3">
          <Sparkles aria-hidden className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
          <div className="space-y-1 text-sm">
            <p className="font-medium">Semantic search is on the menu</p>
            <p className="text-muted-foreground">
              Not enabled yet — once enrichment lands, the embedding model id and revision
              used for your vector index will appear here.
            </p>
          </div>
        </CardContent>
      </Card>
    </Section>
  )
}

/** Settings: appearance, database overview (path, size, counts by kind),
 * watch folders (the one editable control: the auto-import toggle),
 * registered sources, and version info. Everything else is display-only. */
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

        <Section title="Appearance" description="How Potluck looks on this device.">
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
        </Section>

        <DatabaseSection stats={stats} error={error} isPending={isPending} />
        <ItemsByKindSection stats={stats} isPending={isPending && !error} />
        <WatchSection />
        <SourcesSection />
        <AboutSection version={stats?.version} />
        <EnrichmentSection />
      </div>
    </div>
  )
}
