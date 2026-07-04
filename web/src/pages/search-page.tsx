import { keepPreviousData, useInfiniteQuery, useQuery, useQueryClient } from '@tanstack/react-query'
import { useVirtualizer } from '@tanstack/react-virtual'
import {
  CircleAlert,
  Database,
  ListFilter,
  LoaderCircle,
  RotateCw,
  Search,
  SearchX,
  TriangleAlert,
  X,
} from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useSearchParams } from 'react-router'
import { KindIcon } from '@/components/kind-icon'
import { DateFilter, FilterChip, MultiSelectFilter } from '@/components/search/filters'
import { ResultRow } from '@/components/search/result-row'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { ApiError, ITEM_KINDS, fetchSources, isItemKind, searchItems } from '@/lib/api'
import { useDebouncedCallback } from '@/lib/use-debounced-callback'
import { cn } from '@/lib/utils'

const PAGE_SIZE = 30
const DEBOUNCE_MS = 220

// Results scroll offset per search key: back from an item detail remounts
// this page, react-query restores the loaded pages from cache, and this map
// restores where the user was in them. Module-level on purpose — it must
// outlive the component; bounded so an exploratory session can't grow it
// forever.
const scrollMemory = new Map<string, number>()
const SCROLL_MEMORY_MAX = 50

function rememberScroll(key: string, top: number) {
  scrollMemory.delete(key)
  scrollMemory.set(key, top)
  if (scrollMemory.size > SCROLL_MEMORY_MAX) {
    const oldest = scrollMemory.keys().next().value
    if (oldest !== undefined) scrollMemory.delete(oldest)
  }
}

const QUERY_EXAMPLES = [
  'maple syrup',
  'kind:email quarterly report',
  'from:alice@example.com',
  'after:2024-01-01 recipes',
]

/** The search page. The URL is the source of truth: `q`, repeatable
 * `kind`/`source`, `after`/`before`, and `prefix` (search-as-you-type mode)
 * all live in the search params, so back/forward and reload restore state. */
export function SearchPage() {
  const [params, setParams] = useSearchParams()
  const queryClient = useQueryClient()

  // ---- URL state --------------------------------------------------------
  const urlQ = params.get('q') ?? ''
  const kinds = useMemo(() => params.getAll('kind').filter(isItemKind).sort(), [params])
  const sources = useMemo(() => params.getAll('source').sort(), [params])
  const after = params.get('after') ?? ''
  const before = params.get('before') ?? ''
  // SAYT prefix mode only makes sense with free text present.
  const prefix = params.get('prefix') === '1' && urlQ !== ''
  const hasFilters = kinds.length > 0 || sources.length > 0 || after !== '' || before !== ''
  const enabled = urlQ.trim() !== '' || hasFilters

  function updateParams(mutate: (next: URLSearchParams) => void, opts?: { replace?: boolean }) {
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev)
        mutate(next)
        return next
      },
      { replace: opts?.replace ?? false },
    )
  }

  // ---- Input <-> URL sync -----------------------------------------------
  // The input is instant local state; the URL is committed on a debounce
  // (prefix mode) or on Enter (exact mode). `selfWriteQ` remembers the last
  // q WE wrote so external navigations (back/forward, example clicks) are
  // adopted into the input without clobbering in-flight typing.
  const [text, setText] = useState(urlQ)
  const selfWriteQ = useRef(urlQ)
  const inputRef = useRef<HTMLInputElement>(null)
  const parentRef = useRef<HTMLDivElement>(null) // the results scroll container

  function commitQuery(value: string, opts: { prefix: boolean; replace?: boolean }) {
    selfWriteQ.current = value
    // Already committed in this exact mode (e.g. Enter pressed twice on the
    // same query): skip the no-op write so history gets no duplicate entry.
    const targetPrefix = opts.prefix && value !== ''
    if (value === urlQ && targetPrefix === (params.get('prefix') === '1')) return
    updateParams(
      (next) => {
        if (value) next.set('q', value)
        else next.delete('q')
        if (opts.prefix && value) next.set('prefix', '1')
        else next.delete('prefix')
      },
      { replace: opts.replace },
    )
  }

  const debounced = useDebouncedCallback((value: string) => {
    commitQuery(value, { prefix: true, replace: true })
  }, DEBOUNCE_MS)

  useEffect(() => {
    if (urlQ !== selfWriteQ.current) {
      debounced.cancel()
      selfWriteQ.current = urlQ
      setText(urlQ)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- sync only on URL q changes
  }, [urlQ])

  // Focus on load; "/" focuses from anywhere (handled app-wide in Layout,
  // which falls back to navigating here — so also focus on mount).
  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  // ---- Search query ------------------------------------------------------
  const queryKey = useMemo(
    () => ['search', { q: urlQ, prefix, kinds, sources, after, before }] as const,
    [urlQ, prefix, kinds, sources, after, before],
  )
  const keyString = JSON.stringify(queryKey[1])

  const {
    data,
    error,
    isError,
    isLoading,
    isPlaceholderData,
    hasNextPage,
    isFetchingNextPage,
    fetchNextPage,
    refetch,
  } = useInfiniteQuery({
    queryKey,
    queryFn: ({ pageParam, signal }) =>
      searchItems(
        {
          q: urlQ,
          kinds,
          sources,
          after: after || undefined,
          before: before || undefined,
          prefix,
          cursor: pageParam,
          limit: PAGE_SIZE,
        },
        signal,
      ),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.next_cursor,
    enabled,
    placeholderData: keepPreviousData,
    retry: false,
  })

  const rows = useMemo(() => data?.pages.flatMap((page) => page.hits) ?? [], [data])
  const warnings = data?.pages[0]?.warnings ?? []
  const errorCode = error instanceof ApiError ? error.code : null

  // Cursor etiquette: a 400 invalid_cursor means the walk no longer matches
  // the query (foreign/stale cursor). Restart from the first page with a
  // transient notice — never an error state. At most ONE automatic restart
  // per search: if the server keeps rejecting the cursors it just issued
  // (e.g. restarted mid-session), the next rejection hands the restart to
  // the user instead of looping reset -> refetch -> reject.
  const [cursorReset, setCursorReset] = useState(false)
  const [cursorStuck, setCursorStuck] = useState(false)
  const autoResetDoneFor = useRef<string | null>(null)
  useEffect(() => {
    if (errorCode !== 'invalid_cursor') return
    if (autoResetDoneFor.current === keyString) {
      setCursorStuck(true)
      return
    }
    autoResetDoneFor.current = keyString
    setCursorReset(true)
    // Back to the top so the sentinel isn't instantly visible again.
    parentRef.current?.scrollTo({ top: 0 })
    void queryClient.resetQueries({ queryKey })
  }, [errorCode, keyString, queryClient, queryKey])
  useEffect(() => {
    if (!cursorReset) return
    const timer = window.setTimeout(() => setCursorReset(false), 5000)
    return () => window.clearTimeout(timer)
  }, [cursorReset])

  // Warnings (dropped inline operators) are dismissible per search.
  const [dismissedWarnKey, setDismissedWarnKey] = useState<string | null>(null)
  const showWarnings = warnings.length > 0 && dismissedWarnKey !== keyString

  // ---- Sources for the filter popover ------------------------------------
  const sourcesQuery = useQuery({
    queryKey: ['sources'],
    queryFn: ({ signal }) => fetchSources(signal),
    staleTime: 5 * 60_000,
  })

  // ---- Virtualized results -----------------------------------------------
  const virtualizer = useVirtualizer({
    count: rows.length + (hasNextPage ? 1 : 0),
    getScrollElement: () => parentRef.current,
    estimateSize: () => 92,
    overscan: 10,
    getItemKey: (index) => (index < rows.length ? rows[index].id : 'load-more'),
  })
  const virtualItems = virtualizer.getVirtualItems()

  // New search: back to the top (or to the remembered offset when returning
  // to a search we already scrolled — e.g. back from an item detail), re-arm
  // the automatic cursor restart, and drop any stuck-cursor affordance from
  // the previous search. The offset is recorded as the user scrolls — by
  // unmount-cleanup time the node is detached and reads scrollTop 0.
  useEffect(() => {
    const el = parentRef.current
    el?.scrollTo({ top: scrollMemory.get(keyString) ?? 0 })
    autoResetDoneFor.current = null
    setCursorStuck(false)
    if (!el) return
    const onScroll = () => rememberScroll(keyString, el.scrollTop)
    el.addEventListener('scroll', onScroll, { passive: true })
    return () => el.removeEventListener('scroll', onScroll)
  }, [keyString])

  // Sentinel row visible -> fetch the next page (cursor passed back
  // verbatim by react-query with the same query/filters).
  useEffect(() => {
    const last = virtualItems[virtualItems.length - 1]
    if (!last) return
    if (last.index >= rows.length && hasNextPage && !isFetchingNextPage && !isError) {
      void fetchNextPage()
    }
  }, [virtualItems, rows.length, hasNextPage, isFetchingNextPage, isError, fetchNextPage])

  // ---- Handlers -----------------------------------------------------------
  function toggleParam(name: 'kind' | 'source', value: string) {
    updateParams((next) => {
      const current = next.getAll(name)
      next.delete(name)
      const toggled = current.includes(value)
        ? current.filter((v) => v !== value)
        : [...current, value]
      for (const v of toggled) next.append(name, v)
    })
  }

  function setDate(bound: 'after' | 'before', value: string) {
    updateParams((next) => {
      if (value) next.set(bound, value)
      else next.delete(bound)
    })
  }

  function clearFilters() {
    updateParams((next) => {
      next.delete('kind')
      next.delete('source')
      next.delete('after')
      next.delete('before')
    })
  }

  function runExample(example: string) {
    debounced.cancel()
    setText(example)
    commitQuery(example, { prefix: false })
    inputRef.current?.focus()
  }

  function restartSearch() {
    // Manual restart: the user asked, so the automatic reset is re-armed.
    autoResetDoneFor.current = null
    setCursorStuck(false)
    setCursorReset(false)
    parentRef.current?.scrollTo({ top: 0 })
    void queryClient.resetQueries({ queryKey })
  }

  const isFilterOnly = urlQ.trim() === '' && hasFilters
  const nextPageFailed = isError && rows.length > 0 && errorCode !== 'invalid_cursor'

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* Search header */}
      <div className="border-b px-4 pt-5 pb-3 md:px-6">
        <div className="mx-auto w-full max-w-3xl space-y-3">
          <div className="relative">
            <Search
              aria-hidden
              className="absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground"
            />
            <Input
              ref={inputRef}
              data-testid="search-input"
              aria-label="Search"
              placeholder="Search notes, emails, and more…"
              value={text}
              onChange={(e) => {
                setText(e.target.value)
                debounced.run(e.target.value)
              }}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  debounced.cancel()
                  commitQuery(text, { prefix: false })
                }
              }}
              className="h-11 rounded-lg pr-10 pl-9 text-base"
            />
            {text ? (
              <button
                type="button"
                aria-label="Clear search"
                onClick={() => {
                  debounced.cancel()
                  setText('')
                  commitQuery('', { prefix: false })
                  inputRef.current?.focus()
                }}
                className="absolute top-1/2 right-2.5 -translate-y-1/2 rounded-sm p-1 text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
              >
                <X aria-hidden className="size-4" />
              </button>
            ) : (
              <kbd className="pointer-events-none absolute top-1/2 right-3 -translate-y-1/2 rounded border bg-muted px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground">
                /
              </kbd>
            )}
          </div>

          {/* Filter bar + active chips */}
          <div className="flex flex-wrap items-center gap-2">
            <MultiSelectFilter
              label="Kind"
              testId="filter-kind"
              icon={<ListFilter aria-hidden className="size-3.5" />}
              options={ITEM_KINDS.map((kind) => ({
                value: kind,
                label: kind,
                icon: <KindIcon kind={kind} className="size-3.5 text-muted-foreground" />,
              }))}
              selected={kinds}
              onToggle={(value) => toggleParam('kind', value)}
            />
            <MultiSelectFilter
              label="Source"
              testId="filter-source"
              icon={<Database aria-hidden className="size-3.5" />}
              options={(sourcesQuery.data ?? []).map((s) => ({ value: s.name, label: s.name }))}
              selected={sources}
              onToggle={(value) => toggleParam('source', value)}
              loading={sourcesQuery.isPending}
              emptyText={sourcesQuery.isError ? 'Could not load sources' : 'No sources available'}
            />
            <DateFilter after={after} before={before} onChange={setDate} />
            {kinds.map((kind) => (
              <FilterChip
                key={`kind-${kind}`}
                label={kind}
                icon={<KindIcon kind={kind} className="size-3" />}
                onRemove={() => toggleParam('kind', kind)}
              />
            ))}
            {sources.map((source) => (
              <FilterChip
                key={`source-${source}`}
                label={source}
                icon={<Database aria-hidden className="size-3" />}
                onRemove={() => toggleParam('source', source)}
              />
            ))}
            {after && <FilterChip label={`after ${after}`} onRemove={() => setDate('after', '')} />}
            {before && (
              <FilterChip label={`before ${before}`} onRemove={() => setDate('before', '')} />
            )}
            {hasFilters && (
              <Button variant="ghost" size="xs" onClick={clearFilters} className="text-muted-foreground">
                Clear all
              </Button>
            )}
          </div>

          {showWarnings && (
            <div
              data-testid="warning-banner"
              className="flex items-start gap-2 rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-700 dark:text-amber-300"
            >
              <TriangleAlert aria-hidden className="mt-0.5 size-4 shrink-0" />
              <div className="flex-1 space-y-0.5">
                {warnings.map((warning) => (
                  <p key={warning}>{warning}</p>
                ))}
              </div>
              <button
                type="button"
                aria-label="Dismiss warnings"
                onClick={() => setDismissedWarnKey(keyString)}
                className="rounded-sm p-0.5 outline-none hover:bg-amber-500/20 focus-visible:ring-2 focus-visible:ring-ring"
              >
                <X aria-hidden className="size-4" />
              </button>
            </div>
          )}

          {cursorStuck ? (
            <div
              data-testid="cursor-stuck-banner"
              className="flex items-center gap-3 rounded-md border bg-muted px-3 py-2 text-sm text-muted-foreground"
            >
              <RotateCw aria-hidden className="size-4 shrink-0" />
              <span className="flex-1">
                Results keep changing on the server — restart the search to continue.
              </span>
              <Button variant="outline" size="xs" onClick={restartSearch}>
                Restart search
              </Button>
            </div>
          ) : cursorReset ? (
            <div
              data-testid="cursor-reset-banner"
              className="flex items-center gap-2 rounded-md border bg-muted px-3 py-2 text-sm text-muted-foreground"
            >
              <RotateCw aria-hidden className="size-4 shrink-0" />
              Search changed — restarting from the first page.
            </div>
          ) : null}
        </div>
      </div>

      {/* Results */}
      <div
        ref={parentRef}
        data-testid="results-scroll"
        data-loaded-count={rows.length}
        className="min-h-0 flex-1 overflow-y-auto"
      >
        <div className="mx-auto w-full max-w-3xl">
          {!enabled ? (
            <IdleState onExample={runExample} />
          ) : isLoading || (rows.length === 0 && errorCode === 'invalid_cursor') ? (
            <ResultSkeletons />
          ) : isError && rows.length === 0 ? (
            <ErrorState
              message={error instanceof Error ? error.message : 'Something went wrong.'}
              onRetry={() => void refetch()}
            />
          ) : rows.length === 0 && !isPlaceholderData ? (
            <NoResultsState q={urlQ} hasFilters={hasFilters} onClearFilters={clearFilters} />
          ) : (
            <>
              <div
                className={cn(
                  'relative w-full transition-opacity',
                  isPlaceholderData && 'opacity-60',
                )}
                style={{ height: virtualizer.getTotalSize() }}
              >
                {virtualItems.map((item) => (
                  <div
                    key={item.key}
                    data-index={item.index}
                    ref={virtualizer.measureElement}
                    className="absolute top-0 left-0 w-full"
                    style={{ transform: `translateY(${item.start}px)` }}
                  >
                    {item.index < rows.length ? (
                      <ResultRow hit={rows[item.index]} />
                    ) : (
                      <div
                        data-testid="load-more-row"
                        className="flex items-center justify-center gap-2 py-5 text-sm text-muted-foreground"
                      >
                        {!isError && (
                          <>
                            <LoaderCircle aria-hidden className="size-4 animate-spin" />
                            Loading more…
                          </>
                        )}
                      </div>
                    )}
                  </div>
                ))}
              </div>
              {nextPageFailed && (
                <div className="flex items-center justify-center gap-3 py-5 text-sm text-muted-foreground">
                  <CircleAlert aria-hidden className="size-4 text-destructive" />
                  Couldn’t load more results.
                  <Button variant="outline" size="xs" onClick={() => void fetchNextPage()}>
                    Retry
                  </Button>
                </div>
              )}
              {!hasNextPage && !nextPageFailed && rows.length > 0 && (
                <p
                  data-testid="end-of-results"
                  className="py-6 text-center text-xs text-muted-foreground"
                >
                  {isFilterOnly && rows.length >= PAGE_SIZE
                    ? `Filter-only view shows the newest ${PAGE_SIZE} items — add search terms to dig deeper.`
                    : 'End of results'}
                </p>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}

function IdleState({ onExample }: { onExample: (example: string) => void }) {
  return (
    <div
      data-testid="empty-idle"
      className="mx-auto flex max-w-md flex-col items-center gap-3 px-6 py-20 text-center"
    >
      <div className="flex size-12 items-center justify-center rounded-full bg-muted">
        <Search aria-hidden className="size-5 text-muted-foreground" />
      </div>
      <h2 className="text-lg font-semibold">Search your knowledge base</h2>
      <p className="text-sm text-balance text-muted-foreground">
        Results update as you type; press Enter to match exact words. Combine free text with
        operators like <InlineCode>kind:</InlineCode> <InlineCode>from:</InlineCode>{' '}
        <InlineCode>source:</InlineCode> <InlineCode>after:</InlineCode> and{' '}
        <InlineCode>before:</InlineCode>, or use the filters above.
      </p>
      <div className="mt-2 flex flex-wrap justify-center gap-2">
        {QUERY_EXAMPLES.map((example) => (
          <Button
            key={example}
            variant="outline"
            size="sm"
            className="font-mono text-xs font-normal text-muted-foreground"
            onClick={() => onExample(example)}
          >
            {example}
          </Button>
        ))}
      </div>
    </div>
  )
}

function InlineCode({ children }: { children: ReactNode }) {
  return (
    <code className="rounded bg-muted px-1 py-0.5 font-mono text-xs text-foreground">
      {children}
    </code>
  )
}

function NoResultsState({
  q,
  hasFilters,
  onClearFilters,
}: {
  q: string
  hasFilters: boolean
  onClearFilters: () => void
}) {
  return (
    <div
      data-testid="empty-no-results"
      className="mx-auto flex max-w-md flex-col items-center gap-3 px-6 py-20 text-center"
    >
      <div className="flex size-12 items-center justify-center rounded-full bg-muted">
        <SearchX aria-hidden className="size-5 text-muted-foreground" />
      </div>
      <h2 className="text-lg font-semibold">
        {q.trim() ? <>No results for “{q}”</> : 'No items match these filters'}
      </h2>
      <p className="text-sm text-muted-foreground">
        {hasFilters ? 'Try different keywords, or remove some filters.' : 'Try different keywords.'}
      </p>
      {hasFilters && (
        <Button variant="outline" size="sm" onClick={onClearFilters}>
          Clear filters
        </Button>
      )}
    </div>
  )
}

function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div
      data-testid="error-state"
      className="mx-auto flex max-w-md flex-col items-center gap-3 px-6 py-20 text-center"
    >
      <div className="flex size-12 items-center justify-center rounded-full bg-destructive/10">
        <CircleAlert aria-hidden className="size-5 text-destructive" />
      </div>
      <h2 className="text-lg font-semibold">Search failed</h2>
      <p className="text-sm text-muted-foreground">{message}</p>
      <Button variant="outline" size="sm" onClick={onRetry}>
        Try again
      </Button>
    </div>
  )
}

function ResultSkeletons() {
  return (
    <div aria-hidden className="divide-y">
      {Array.from({ length: 6 }, (_, i) => (
        <div key={i} className="flex gap-3 px-4 py-3.5 md:px-6">
          <Skeleton className="mt-0.5 size-7 shrink-0 rounded-md" />
          <div className="flex-1 space-y-2 py-0.5">
            <Skeleton className="h-4 w-2/5" />
            <Skeleton className="h-3.5 w-full" />
            <Skeleton className="h-3.5 w-3/4" />
          </div>
        </div>
      ))}
    </div>
  )
}
