import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryResult,
} from '@tanstack/react-query'
import {
  CircleAlert,
  CircleCheck,
  FileArchive,
  FolderInput,
  Inbox,
  LoaderCircle,
  RotateCw,
  TriangleAlert,
  Upload,
  X,
} from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import {
  ApiError,
  fetchImports,
  fetchImportStatus,
  startPathImport,
  startUploadImport,
  type ImportListResponse,
  type ImportRun,
  type ImportStatus,
  type ImportTask,
} from '@/lib/api'
import { basename, formatDateTime, formatDuration } from '@/lib/format'
import { cn } from '@/lib/utils'

const STATUS_POLL_MS = 1000
const HISTORY_POLL_MS = 1500
const HISTORY_PAGE_SIZE = 20
// Errors shorter than this render whole; longer ones clamp behind "Show more".
const ERROR_CLAMP_CHARS = 160

/** The imports page: start an import (upload or server path), watch the
 * current one live, and browse the history ledger.
 *
 * Polling design: `/api/imports/status` is the drumbeat — it polls only while
 * the background operation reports `running` (react-query refetchInterval
 * returns false otherwise, so an idle page makes zero requests). While
 * running, two more pollers wake up: a small newest-first slice for the
 * per-source progress card, and the history page the user is looking at.
 * When the run settles, everything refetches once more and goes quiet. */
export function ImportsPage() {
  const queryClient = useQueryClient()
  const [path, setPath] = useState('')
  const [page, setPage] = useState(0)
  // Failed-task banners are dismissable per operation: keyed by the task's
  // identity so the banner for a NEW failure always reappears.
  const [dismissedTaskKey, setDismissedTaskKey] = useState<string | null>(null)
  const pathInputRef = useRef<HTMLInputElement>(null)

  // ---- Polling ------------------------------------------------------------
  const statusQuery = useQuery({
    queryKey: ['import-status'],
    queryFn: ({ signal }) => fetchImportStatus(signal),
    refetchInterval: (query) => (query.state.data?.status === 'running' ? STATUS_POLL_MS : false),
    staleTime: 0,
  })
  const task = statusQuery.data ?? null
  const running = task?.status === 'running'

  const historyQuery = useQuery({
    queryKey: ['imports', 'history', page],
    queryFn: ({ signal }) =>
      fetchImports({ limit: HISTORY_PAGE_SIZE, offset: page * HISTORY_PAGE_SIZE }, signal),
    refetchInterval: running ? HISTORY_POLL_MS : false,
    placeholderData: keepPreviousData,
    staleTime: 0,
  })

  // Newest-first slice feeding the current-import card — independent of the
  // history page the user browsed to, and only alive while a run is active.
  const activeQuery = useQuery({
    queryKey: ['imports', 'active'],
    queryFn: ({ signal }) => fetchImports({ limit: 10, offset: 0 }, signal),
    enabled: running,
    refetchInterval: STATUS_POLL_MS,
    staleTime: 0,
  })
  const activeRuns = useMemo(() => {
    if (!running || !task || !activeQuery.data) return []
    // This operation's ledger rows: same archive path, started after the task
    // did (2s slack — the row's timestamp has coarser precision than the
    // task's). Rows appear here as each detected source starts.
    const since = new Date(task.started_at).getTime() - 2000
    return activeQuery.data.runs
      .filter((run) => run.path === task.path && new Date(run.started_at).getTime() >= since)
      .sort((a, b) => a.id - b.id)
  }, [running, task, activeQuery.data])

  // Run settled (running -> completed/failed): one final refresh of the
  // ledger and stats, then the pollers above stop on their own.
  const prevRunning = useRef(running)
  useEffect(() => {
    if (prevRunning.current && !running) {
      void queryClient.invalidateQueries({ queryKey: ['imports'] })
      void queryClient.invalidateQueries({ queryKey: ['stats'] })
    }
    prevRunning.current = running
  }, [running, queryClient])

  // ---- Start-an-import mutations -------------------------------------------
  const [startError, setStartError] = useState<{ busy: boolean; message: string } | null>(null)

  function onStarted(started: ImportTask) {
    setStartError(null)
    // Seed the cache with the 202 snapshot: the current-import card appears
    // immediately, and the status poller takes over from there.
    queryClient.setQueryData(['import-status'], started)
    void queryClient.invalidateQueries({ queryKey: ['imports'] })
  }

  function onStartFailed(error: unknown) {
    if (error instanceof ApiError) {
      setStartError({ busy: error.status === 409, message: error.message })
    } else {
      setStartError({
        busy: false,
        message: error instanceof Error ? error.message : 'The import could not be started.',
      })
    }
  }

  const pathMutation = useMutation({
    mutationFn: startPathImport,
    onSuccess: onStarted,
    onError: onStartFailed,
  })
  const uploadMutation = useMutation({
    mutationFn: startUploadImport,
    onSuccess: onStarted,
    onError: onStartFailed,
  })

  function submitPath() {
    const trimmed = path.trim()
    if (!trimmed || pathMutation.isPending) return
    setStartError(null)
    pathMutation.mutate(trimmed)
  }

  function uploadFile(file: File) {
    if (uploadMutation.isPending) return
    setStartError(null)
    uploadMutation.mutate(file)
  }

  /** The fix-and-retry affordance: refill the path input with a failed path. */
  function retryPath(failedPath: string) {
    setPath(failedPath)
    setStartError(null)
    pathInputRef.current?.focus()
    pathInputRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }

  const taskKey = task ? `${task.path}@${task.started_at}` : null
  const showTaskFailed = task?.status === 'failed' && taskKey !== dismissedTaskKey

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-4xl space-y-6 p-4 md:p-6">
        <div>
          <h2 className="text-xl font-semibold tracking-tight">Imports</h2>
          <p className="text-sm text-muted-foreground">
            Bring your exports into Potluck — everything stays on this machine.
          </p>
        </div>

        <StartImportCard
          path={path}
          onPathChange={setPath}
          onSubmitPath={submitPath}
          onUpload={uploadFile}
          pathPending={pathMutation.isPending}
          uploading={uploadMutation.isPending}
          uploadingName={uploadMutation.variables?.name}
          pathInputRef={pathInputRef}
          startError={startError}
          onDismissError={() => setStartError(null)}
        />

        {running && task && <CurrentImportCard task={task} runs={activeRuns} />}

        {showTaskFailed && task && (
          <TaskFailedCard
            task={task}
            onRetry={() => retryPath(task.path)}
            onDismiss={() => setDismissedTaskKey(taskKey)}
          />
        )}

        <HistoryCard query={historyQuery} page={page} onPageChange={setPage} onRetry={retryPath} />
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Start an import
// ---------------------------------------------------------------------------

function StartImportCard({
  path,
  onPathChange,
  onSubmitPath,
  onUpload,
  pathPending,
  uploading,
  uploadingName,
  pathInputRef,
  startError,
  onDismissError,
}: {
  path: string
  onPathChange: (value: string) => void
  onSubmitPath: () => void
  onUpload: (file: File) => void
  pathPending: boolean
  uploading: boolean
  uploadingName: string | undefined
  pathInputRef: React.RefObject<HTMLInputElement | null>
  startError: { busy: boolean; message: string } | null
  onDismissError: () => void
}) {
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [dragOver, setDragOver] = useState(false)

  return (
    <Card>
      <CardHeader>
        <CardTitle>Import an archive</CardTitle>
        <CardDescription>
          Google Takeout zip and tgz exports — sources inside are detected automatically.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-4 md:grid-cols-2">
          {/* Upload zone */}
          <div
            data-testid="upload-dropzone"
            onDragOver={(e) => {
              e.preventDefault()
              setDragOver(true)
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={(e) => {
              e.preventDefault()
              setDragOver(false)
              const file = e.dataTransfer.files?.[0]
              if (file) onUpload(file)
            }}
            className={cn(
              'flex flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed px-4 py-8 text-center transition-colors',
              dragOver ? 'border-primary bg-primary/5' : 'border-border',
            )}
          >
            <input
              ref={fileInputRef}
              data-testid="upload-input"
              type="file"
              accept=".zip,.tgz,.gz,.tar"
              aria-label="Upload an archive"
              className="sr-only"
              onChange={(e) => {
                const file = e.target.files?.[0]
                if (file) onUpload(file)
                e.target.value = '' // allow re-choosing the same file
              }}
            />
            {uploading ? (
              <>
                <LoaderCircle aria-hidden className="size-6 animate-spin text-muted-foreground" />
                <p data-testid="uploading" className="text-sm font-medium">
                  Uploading {uploadingName}…
                </p>
                <p className="text-xs text-muted-foreground">
                  Large archives can take a while to copy.
                </p>
              </>
            ) : (
              <>
                <div className="flex size-10 items-center justify-center rounded-full bg-muted">
                  <Upload aria-hidden className="size-4.5 text-muted-foreground" />
                </div>
                <p className="text-sm font-medium">Drop an archive here</p>
                <Button variant="outline" size="sm" onClick={() => fileInputRef.current?.click()}>
                  <FileArchive aria-hidden className="size-3.5" />
                  Browse files
                </Button>
              </>
            )}
          </div>

          {/* Server-path zone */}
          <div className="flex flex-col justify-center gap-2">
            <label htmlFor="import-path" className="text-sm font-medium">
              Or import from a path on this machine
            </label>
            <div className="flex gap-2">
              <Input
                id="import-path"
                ref={pathInputRef}
                data-testid="path-input"
                value={path}
                placeholder="~/exports/takeout-20260101-001.zip"
                onChange={(e) => onPathChange(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') onSubmitPath()
                }}
                className="font-mono text-xs"
              />
              <Button
                data-testid="path-import-button"
                onClick={onSubmitPath}
                disabled={!path.trim() || pathPending}
              >
                {pathPending ? (
                  <LoaderCircle aria-hidden className="size-4 animate-spin" />
                ) : (
                  <FolderInput aria-hidden className="size-4" />
                )}
                Import
              </Button>
            </div>
            <p className="text-xs text-muted-foreground">
              Archives or extracted folders. For a multi-part Takeout set, point at any one part —
              the whole set is picked up automatically.
            </p>
          </div>
        </div>

        {startError &&
          (startError.busy ? (
            <div
              data-testid="start-busy"
              className="flex items-start gap-2 rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-700 dark:text-amber-300"
            >
              <TriangleAlert aria-hidden className="mt-0.5 size-4 shrink-0" />
              <p className="flex-1">{startError.message}</p>
              <DismissButton onClick={onDismissError} className="hover:bg-amber-500/20" />
            </div>
          ) : (
            <div
              data-testid="start-error"
              className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive"
            >
              <CircleAlert aria-hidden className="mt-0.5 size-4 shrink-0" />
              <p className="flex-1 break-all">{startError.message}</p>
              <DismissButton onClick={onDismissError} className="hover:bg-destructive/20" />
            </div>
          ))}
      </CardContent>
    </Card>
  )
}

function DismissButton({ onClick, className }: { onClick: () => void; className?: string }) {
  return (
    <button
      type="button"
      aria-label="Dismiss"
      onClick={onClick}
      className={cn(
        'rounded-sm p-0.5 outline-none focus-visible:ring-2 focus-visible:ring-ring',
        className,
      )}
    >
      <X aria-hidden className="size-4" />
    </button>
  )
}

// ---------------------------------------------------------------------------
// Current import (visible while running)
// ---------------------------------------------------------------------------

function CurrentImportCard({ task, runs }: { task: ImportTask; runs: ImportRun[] }) {
  return (
    <Card data-testid="current-import">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <LoaderCircle aria-hidden className="size-4 animate-spin text-muted-foreground" />
          Importing {basename(task.path)}
        </CardTitle>
        <CardDescription>
          Started {formatDateTime(task.started_at)} · runs in the background — leaving this page
          won’t stop it.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {runs.length === 0 ? (
          <div data-testid="detect-phase" className="space-y-1.5">
            <p className="text-sm text-muted-foreground">Detecting sources in the archive…</p>
            <ProgressBar value={null} />
          </div>
        ) : (
          runs.map((run) => <SourceProgress key={run.id} run={run} />)
        )}
      </CardContent>
    </Card>
  )
}

function SourceProgress({ run }: { run: ImportRun }) {
  const pct =
    run.status === 'completed'
      ? 100
      : run.items_total !== null && run.items_total > 0
        ? Math.min(100, (run.items_done / run.items_total) * 100)
        : null // unknown total -> indeterminate bar + live counters
  return (
    <div data-testid="source-progress" data-source={run.source} className="space-y-1.5">
      <div className="flex items-baseline justify-between gap-3">
        <span className="flex items-center gap-1.5 text-sm font-medium">
          {run.status === 'completed' && (
            <CircleCheck aria-hidden className="size-3.5 text-emerald-600 dark:text-emerald-400" />
          )}
          {run.source}
        </span>
        <span className="text-xs tabular-nums text-muted-foreground">
          {run.items_done.toLocaleString()}
          {run.items_total !== null && ` / ${run.items_total.toLocaleString()}`} items
          {run.status === 'running' && ' so far'}
        </span>
      </div>
      <ProgressBar value={pct} />
      <p className="text-xs text-muted-foreground">{countsBreakdown(run).join(' · ')}</p>
    </div>
  )
}

function ProgressBar({ value }: { value: number | null }) {
  return (
    <div
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={value ?? undefined}
      className="h-1.5 w-full overflow-hidden rounded-full bg-muted"
    >
      {value === null ? (
        <div className="animate-progress-slide h-full w-2/5 rounded-full bg-primary" />
      ) : (
        <div
          className="h-full rounded-full bg-primary transition-[width] duration-500"
          style={{ width: `${value}%` }}
        />
      )}
    </div>
  )
}

/** "128 new", "3 updated", … — only the non-zero counters. */
function countsBreakdown(run: ImportRun): string[] {
  const parts = [
    [run.items_new, 'new'],
    [run.items_updated, 'updated'],
    [run.items_duplicate, 'duplicate'],
    [run.items_skipped, 'skipped'],
  ] as const
  const shown = parts.filter(([count]) => count > 0)
  if (shown.length === 0) return [run.status === 'running' ? 'starting…' : 'no items']
  return shown.map(([count, label]) => `${count.toLocaleString()} ${label}`)
}

/** The breakdown with line breaks allowed between pairs but never inside one. */
function CountsBreakdown({ run }: { run: ImportRun }) {
  const parts = countsBreakdown(run)
  return (
    <>
      {parts.map((part, i) => (
        <span key={part} className="whitespace-nowrap">
          {part}
          {i < parts.length - 1 && ' · '}
        </span>
      ))}
    </>
  )
}

// ---------------------------------------------------------------------------
// Failed operation (detection failures have no ledger row — this is their UI)
// ---------------------------------------------------------------------------

function TaskFailedCard({
  task,
  onRetry,
  onDismiss,
}: {
  task: ImportTask
  onRetry: () => void
  onDismiss: () => void
}) {
  return (
    <Card data-testid="task-failed" className="ring-destructive/30">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-destructive">
          <CircleAlert aria-hidden className="size-4" />
          Import failed
        </CardTitle>
        <CardDescription className="font-mono text-xs break-all">{task.path}</CardDescription>
        <CardAction>
          <DismissButton onClick={onDismiss} className="text-muted-foreground hover:bg-muted" />
        </CardAction>
      </CardHeader>
      <CardContent className="space-y-3">
        <p
          data-testid="task-error"
          className="rounded-md bg-destructive/10 px-3 py-2 font-mono text-xs break-all whitespace-pre-wrap text-destructive"
        >
          {task.error ?? 'Unknown error.'}
        </p>
        <Button data-testid="task-retry" variant="outline" size="sm" onClick={onRetry}>
          <RotateCw aria-hidden className="size-3.5" />
          Fix path and retry
        </Button>
      </CardContent>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// History
// ---------------------------------------------------------------------------

function HistoryCard({
  query,
  page,
  onPageChange,
  onRetry,
}: {
  query: UseQueryResult<ImportListResponse>
  page: number
  onPageChange: (page: number) => void
  onRetry: (path: string) => void
}) {
  const data = query.data
  const start = page * HISTORY_PAGE_SIZE
  const end = data ? Math.min(start + data.runs.length, data.total) : 0

  return (
    <Card>
      <CardHeader>
        <CardTitle>History</CardTitle>
        <CardDescription>
          {data
            ? `${data.total.toLocaleString()} import ${data.total === 1 ? 'run' : 'runs'}, newest first.`
            : 'Every import run, newest first.'}
        </CardDescription>
      </CardHeader>
      <CardContent>
        {query.isPending ? (
          <div aria-hidden className="space-y-2">
            {Array.from({ length: 3 }, (_, i) => (
              <Skeleton key={i} className="h-10 w-full" />
            ))}
          </div>
        ) : query.isError ? (
          <div className="flex flex-col items-center gap-3 py-8 text-center">
            <CircleAlert aria-hidden className="size-5 text-destructive" />
            <p className="text-sm text-muted-foreground">
              {query.error instanceof Error ? query.error.message : 'Could not load the history.'}
            </p>
            <Button variant="outline" size="sm" onClick={() => void query.refetch()}>
              Try again
            </Button>
          </div>
        ) : !data || data.runs.length === 0 ? (
          <div
            data-testid="history-empty"
            className="flex flex-col items-center gap-2 py-8 text-center"
          >
            <div className="flex size-10 items-center justify-center rounded-full bg-muted">
              <Inbox aria-hidden className="size-4.5 text-muted-foreground" />
            </div>
            <p className="text-sm font-medium">No imports yet</p>
            <p className="text-sm text-muted-foreground">
              Your first archive will appear here with its item counts.
            </p>
          </div>
        ) : (
          <>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-left text-xs text-muted-foreground">
                    <th className="py-2 pr-3 font-medium">Status</th>
                    <th className="py-2 pr-3 font-medium">Source</th>
                    <th className="py-2 pr-3 font-medium">Archive</th>
                    <th className="py-2 pr-3 text-right font-medium">Items</th>
                    <th className="py-2 pr-3 font-medium">Started</th>
                    <th className="py-2 font-medium">Duration</th>
                  </tr>
                </thead>
                <tbody className={cn(query.isPlaceholderData && 'opacity-60')}>
                  {data.runs.map((run) => (
                    <HistoryRow key={run.id} run={run} onRetry={onRetry} />
                  ))}
                </tbody>
              </table>
            </div>
            {data.total > HISTORY_PAGE_SIZE && (
              <div className="mt-3 flex items-center justify-between border-t pt-3 text-xs text-muted-foreground">
                <span className="tabular-nums">
                  {(start + 1).toLocaleString()}–{end.toLocaleString()} of{' '}
                  {data.total.toLocaleString()}
                </span>
                <div className="flex gap-2">
                  <Button
                    variant="outline"
                    size="xs"
                    disabled={page === 0}
                    onClick={() => onPageChange(page - 1)}
                  >
                    Previous
                  </Button>
                  <Button
                    variant="outline"
                    size="xs"
                    disabled={end >= data.total}
                    onClick={() => onPageChange(page + 1)}
                  >
                    Next
                  </Button>
                </div>
              </div>
            )}
          </>
        )}
      </CardContent>
    </Card>
  )
}

function HistoryRow({ run, onRetry }: { run: ImportRun; onRetry: (path: string) => void }) {
  const failed = run.status === 'failed'
  return (
    <>
      <tr
        data-testid="history-row"
        data-status={run.status}
        data-source={run.source}
        data-import-id={run.id}
        className={cn('align-top', failed ? 'border-0' : 'border-b last:border-0')}
      >
        <td className="py-2.5 pr-3">
          <StatusBadge status={run.status} />
        </td>
        <td className="py-2.5 pr-3 font-medium whitespace-nowrap">{run.source}</td>
        <td className="max-w-52 truncate py-2.5 pr-3 text-muted-foreground" title={run.path}>
          {basename(run.path)}
        </td>
        <td data-testid="history-items" className="py-2.5 pr-3 text-right tabular-nums">
          <div className="whitespace-nowrap">{run.items_done.toLocaleString()}</div>
          <div className="ml-auto max-w-44 text-xs text-muted-foreground">
            <CountsBreakdown run={run} />
          </div>
        </td>
        <td className="py-2.5 pr-3 whitespace-nowrap text-muted-foreground">
          {formatDateTime(run.started_at) ?? '—'}
        </td>
        <td className="py-2.5 whitespace-nowrap text-muted-foreground">
          {formatDuration(run.started_at, run.finished_at) ?? '—'}
        </td>
      </tr>
      {failed && (
        <tr className="border-b last:border-0">
          <td colSpan={6} className="pb-3">
            <FailedRowError run={run} onRetry={onRetry} />
          </td>
        </tr>
      )}
    </>
  )
}

/** A failed run's error, clamped with an expand toggle, plus fix-and-retry. */
function FailedRowError({ run, onRetry }: { run: ImportRun; onRetry: (path: string) => void }) {
  const [expanded, setExpanded] = useState(false)
  const error = run.error ?? 'Unknown error.'
  const clampable = error.length > ERROR_CLAMP_CHARS
  return (
    <div
      data-testid="history-error"
      data-expanded={expanded}
      className="space-y-2 rounded-md bg-destructive/10 px-3 py-2"
    >
      <p
        className={cn(
          'font-mono text-xs break-all whitespace-pre-wrap text-destructive',
          clampable && !expanded && 'line-clamp-2',
        )}
      >
        {error}
      </p>
      <div className="flex gap-2">
        {clampable && (
          <Button
            variant="ghost"
            size="xs"
            className="text-destructive hover:bg-destructive/20 hover:text-destructive"
            onClick={() => setExpanded((prev) => !prev)}
          >
            {expanded ? 'Show less' : 'Show more'}
          </Button>
        )}
        <Button
          data-testid="history-retry"
          variant="ghost"
          size="xs"
          className="text-destructive hover:bg-destructive/20 hover:text-destructive"
          onClick={() => onRetry(run.path)}
        >
          <RotateCw aria-hidden className="size-3" />
          Fix path and retry
        </Button>
      </div>
    </div>
  )
}

function StatusBadge({ status }: { status: ImportStatus }) {
  if (status === 'completed') {
    return (
      <Badge variant="outline" className="gap-1">
        <CircleCheck aria-hidden className="text-emerald-600 dark:text-emerald-400" />
        completed
      </Badge>
    )
  }
  if (status === 'failed') {
    return (
      <Badge variant="destructive" className="gap-1">
        <CircleAlert aria-hidden />
        failed
      </Badge>
    )
  }
  return (
    <Badge variant="outline" className="gap-1">
      <LoaderCircle aria-hidden className="animate-spin" />
      running
    </Badge>
  )
}
