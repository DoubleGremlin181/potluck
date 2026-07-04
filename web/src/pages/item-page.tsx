import { useQuery } from '@tanstack/react-query'
import {
  ArrowLeft,
  Check,
  ChevronRight,
  CircleAlert,
  Copy,
  FileQuestion,
  Paperclip,
} from 'lucide-react'
import { Fragment, useEffect, useMemo, useRef, useState } from 'react'
import Markdown, { type Components } from 'react-markdown'
import { Link, useNavigate, useParams } from 'react-router'
import remarkGfm from 'remark-gfm'
import { KindIcon } from '@/components/kind-icon'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import {
  ApiError,
  fetchItem,
  fetchThread,
  type EmailDetail,
  type Item,
  type ThreadEntry,
} from '@/lib/api'
import { formatBytes, formatDate, formatDateTime } from '@/lib/format'
import { cn } from '@/lib/utils'

// Long threads collapse to the first message + the latest N behind an
// "older messages" expander (the API always returns the full thread).
const THREAD_TAIL = 5

// A 404 is a definite answer — retrying it only delays the not-found state.
function noRetryOn404(failureCount: number, error: Error): boolean {
  if (error instanceof ApiError && error.status === 404) return false
  return failureCount < 1
}

/** Item detail (#135): email view renders the whole conversation, note view
 * renders markdown/GFM checklists, and every kind gets the raw-meta
 * inspector. The URL id is the source of truth; back preserves the search
 * page state because that state lives in ITS url. */
export function ItemPage() {
  const { itemId } = useParams()
  const navigate = useNavigate()
  const id = Number(itemId)
  const validId = Number.isInteger(id) && id > 0

  // Destructured so TS narrows `item` through the error/isPending checks
  // below (the query result is a discriminated union).
  const {
    data: item,
    error,
    isPending,
    refetch,
  } = useQuery({
    queryKey: ['item', id],
    queryFn: ({ signal }) => fetchItem(id, signal),
    enabled: validId,
    retry: noRetryOn404,
  })
  const threadQuery = useQuery({
    queryKey: ['thread', id],
    queryFn: ({ signal }) => fetchThread(id, signal),
    enabled: validId && item?.kind === 'email',
    retry: noRetryOn404,
  })

  function goBack() {
    if (window.history.length > 1) void navigate(-1)
    else void navigate('/')
  }

  const notFound = !validId || (error instanceof ApiError && error.status === 404)

  return (
    <div className="min-h-0 flex-1 overflow-y-auto" data-testid="item-page">
      <div className="mx-auto w-full max-w-3xl space-y-4 p-4 md:p-6">
        <Button variant="ghost" size="sm" onClick={goBack} className="-ml-2 text-muted-foreground">
          <ArrowLeft aria-hidden className="size-4" />
          Back to search
        </Button>

        {notFound ? (
          <NotFoundState />
        ) : error ? (
          <ErrorState message={error.message} onRetry={() => void refetch()} />
        ) : isPending ? (
          <DetailSkeleton />
        ) : (
          <article className="space-y-5">
            <header className="space-y-2">
              <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
                <Badge variant="secondary" className="gap-1 font-normal">
                  <KindIcon kind={item.kind} className="size-3" />
                  {item.kind}
                </Badge>
                <Badge variant="outline" className="font-normal">
                  {item.source}
                </Badge>
                {item.ts && <span data-testid="item-date">{formatDate(item.ts)}</span>}
              </div>
              <h2 data-testid="item-title" className="text-xl font-semibold tracking-tight">
                {item.title ?? 'Untitled'}
              </h2>
            </header>

            {item.kind === 'email' ? (
              <EmailView item={item} threadQuery={threadQuery} />
            ) : item.kind === 'note' ? (
              <NoteBody text={item.text} />
            ) : (
              <PlainBody text={item.text} />
            )}

            <MetaInspector meta={item.meta} />
          </article>
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Email view: the conversation containing the item
// ---------------------------------------------------------------------------

function EmailView({
  item,
  threadQuery,
}: {
  item: Item
  threadQuery: {
    data?: { entries: ThreadEntry[] }
    isPending: boolean
    isError: boolean
    refetch: () => unknown
  }
}) {
  if (threadQuery.isPending) return <ThreadSkeleton />

  // The thread failing must not sink the page — we already hold the focused
  // item, so render it as a single message under a retry notice.
  const entries = threadQuery.data?.entries ?? [
    {
      id: item.id,
      parent_id: item.parent_id,
      ts: item.ts,
      title: item.title,
      from_addr: item.email?.from_addr ?? null,
      text_preview: item.text?.slice(0, 200) ?? null,
    },
  ]

  return (
    <section aria-label="Conversation" className="space-y-3">
      {threadQuery.isError && (
        <div
          data-testid="thread-error"
          className="flex items-center gap-3 rounded-md border bg-muted px-3 py-2 text-sm text-muted-foreground"
        >
          <CircleAlert aria-hidden className="size-4 shrink-0 text-destructive" />
          <span className="flex-1">Couldn’t load the rest of this conversation.</span>
          <Button variant="outline" size="xs" onClick={() => void threadQuery.refetch()}>
            Retry
          </Button>
        </div>
      )}
      <EmailThread entries={entries} focusedId={item.id} />
    </section>
  )
}

function EmailThread({ entries, focusedId }: { entries: ThreadEntry[]; focusedId: number }) {
  // Collapsible middle: everything between the first message and the latest
  // THREAD_TAIL. Starts expanded when the opened item lives in that middle —
  // the focused message must never be hidden.
  const collapsible = useMemo(
    () => new Set(entries.slice(1, Math.max(1, entries.length - THREAD_TAIL)).map((e) => e.id)),
    [entries],
  )
  const [showOlder, setShowOlder] = useState(() => collapsible.has(focusedId))
  const collapsed = collapsible.size > 0 && !showOlder

  return (
    <div data-testid="email-thread" data-thread-size={entries.length} className="space-y-2">
      {entries.map((entry, i) => (
        <Fragment key={entry.id}>
          {i === 1 && collapsed && (
            <button
              type="button"
              data-testid="thread-older-expander"
              onClick={() => setShowOlder(true)}
              className="flex w-full items-center gap-3 py-1 text-xs text-muted-foreground outline-none hover:text-foreground focus-visible:rounded-sm focus-visible:ring-2 focus-visible:ring-ring"
            >
              <span aria-hidden className="h-px flex-1 bg-border" />
              {collapsible.size} older {collapsible.size === 1 ? 'message' : 'messages'}
              <span aria-hidden className="h-px flex-1 bg-border" />
            </button>
          )}
          <ThreadMessage
            entry={entry}
            focused={entry.id === focusedId}
            defaultExpanded={entry.id === focusedId}
            hidden={collapsed && collapsible.has(entry.id)}
          />
        </Fragment>
      ))}
    </div>
  )
}

function ThreadMessage({
  entry,
  focused,
  defaultExpanded,
  hidden,
}: {
  entry: ThreadEntry
  focused: boolean
  defaultExpanded: boolean
  /** display:none while the thread middle is collapsed — the component stays
   * mounted so expanding older messages never resets per-message state. */
  hidden: boolean
}) {
  const [expanded, setExpanded] = useState(defaultExpanded)
  const ref = useRef<HTMLDivElement>(null)

  // Full body/addresses/labels/attachments come from the item endpoint,
  // fetched lazily on expand (the focused message is already in cache — the
  // page query shares this key).
  const detail = useQuery({
    queryKey: ['item', entry.id],
    queryFn: ({ signal }) => fetchItem(entry.id, signal),
    enabled: expanded,
  })

  // The focused message is scrolled into view once, on mount.
  useEffect(() => {
    if (focused) ref.current?.scrollIntoView({ block: 'center' })
  }, [focused])

  const email = detail.data?.email ?? null
  const from = email
    ? (mailbox(email.from_addr, email.from_name) ?? 'Unknown sender')
    : (entry.from_addr ?? 'Unknown sender')
  const dateTime = formatDateTime(entry.ts)

  return (
    <div
      ref={ref}
      data-testid="thread-message"
      data-item-id={entry.id}
      data-focused={focused || undefined}
      data-expanded={expanded}
      className={cn(
        'scroll-mt-4 rounded-lg bg-card ring-1 ring-foreground/10',
        focused && 'ring-2 ring-primary/45',
        hidden && 'hidden',
      )}
    >
      <button
        type="button"
        aria-expanded={expanded}
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-baseline gap-3 rounded-lg px-4 py-2.5 text-left outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <span className={cn('shrink-0 truncate text-sm font-medium', expanded && 'break-all')}>
          {from}
        </span>
        {!expanded && (
          <span className="min-w-0 flex-1 truncate text-sm text-muted-foreground">
            {entry.text_preview}
          </span>
        )}
        {expanded && <span className="flex-1" />}
        <span className="flex shrink-0 items-baseline gap-2">
          {focused && (
            <span className="rounded-sm bg-primary/10 px-1.5 py-px text-[11px] leading-4 font-medium text-primary">
              this result
            </span>
          )}
          {dateTime && (
            <span className="text-xs whitespace-nowrap text-muted-foreground">{dateTime}</span>
          )}
        </span>
      </button>

      {expanded && (
        <div className="space-y-3 px-4 pb-4">
          {detail.isPending ? (
            <div className="space-y-2 pt-1">
              <Skeleton className="h-3.5 w-1/3" />
              <Skeleton className="h-3.5 w-full" />
              <Skeleton className="h-3.5 w-4/5" />
            </div>
          ) : detail.isError ? (
            <div className="flex items-center gap-3 text-sm text-muted-foreground">
              <CircleAlert aria-hidden className="size-4 text-destructive" />
              Couldn’t load this message.
              <Button variant="outline" size="xs" onClick={() => void detail.refetch()}>
                Retry
              </Button>
            </div>
          ) : (
            <MessageBody item={detail.data} />
          )}
        </div>
      )}
    </div>
  )
}

function MessageBody({ item }: { item: Item }) {
  const email = item.email
  return (
    <>
      {email && <RecipientDetails email={email} />}
      {email && email.labels.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {email.labels.map((label) => (
            <Badge key={label} variant="outline" className="font-normal text-muted-foreground">
              {label}
            </Badge>
          ))}
        </div>
      )}
      {/* Email bodies are plain text with preserved whitespace — never HTML. */}
      {item.text ? (
        <p
          data-testid="message-text"
          className="text-sm leading-relaxed break-words whitespace-pre-wrap"
        >
          {item.text}
        </p>
      ) : (
        <p className="text-sm text-muted-foreground italic">This message has no text.</p>
      )}
      {email && email.attachments.length > 0 && (
        <ul data-testid="message-attachments" className="space-y-1 border-t pt-3">
          {email.attachments.map((attachment, i) => (
            <li key={`${attachment.filename}-${i}`} className="flex items-center gap-2 text-sm">
              <Paperclip aria-hidden className="size-3.5 shrink-0 text-muted-foreground" />
              <span className="truncate">{attachment.filename}</span>
              <span className="shrink-0 text-xs text-muted-foreground">
                {[attachment.mime, attachment.size_bytes !== null && formatBytes(attachment.size_bytes)]
                  .filter(Boolean)
                  .join(' · ')}
              </span>
            </li>
          ))}
        </ul>
      )}
    </>
  )
}

function mailbox(addr: string | null, name: string | null): string | null {
  if (!addr) return name || null
  return name ? `${name} <${addr}>` : addr
}

function mailboxes(addrs: string[], names: string[]): string[] {
  return addrs
    .map((addr, i) => mailbox(addr, names[i] || null))
    .filter((value): value is string => value !== null)
}

function RecipientDetails({ email }: { email: EmailDetail }) {
  const to = mailboxes(email.to_addrs, email.to_names)
  const cc = mailboxes(email.cc_addrs, email.cc_names)
  const bcc = mailboxes(email.bcc_addrs, [])
  if (to.length === 0 && cc.length === 0 && bcc.length === 0) return null
  return (
    <details data-testid="message-recipients" className="group text-sm">
      <summary className="flex w-fit cursor-pointer list-none items-center gap-1 text-xs text-muted-foreground outline-none select-none hover:text-foreground focus-visible:rounded-sm focus-visible:ring-2 focus-visible:ring-ring [&::-webkit-details-marker]:hidden">
        <ChevronRight
          aria-hidden
          className="size-3 transition-transform group-open:rotate-90"
        />
        {to.length > 0 ? `to ${summarize(to)}` : 'recipients'}
      </summary>
      <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 border-l-2 pl-3">
        {to.length > 0 && <RecipientRow label="To" values={to} />}
        {cc.length > 0 && <RecipientRow label="Cc" values={cc} />}
        {bcc.length > 0 && <RecipientRow label="Bcc" values={bcc} />}
      </dl>
    </details>
  )
}

function RecipientRow({ label, values }: { label: string; values: string[] }) {
  return (
    <>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="break-all">{values.join(', ')}</dd>
    </>
  )
}

/** "alice@…" or "alice@…, bob@… and 3 more" for the collapsed summary line. */
function summarize(values: string[]): string {
  const shown = values.slice(0, 2)
  const rest = values.length - shown.length
  return rest > 0 ? `${shown.join(', ')} and ${rest} more` : shown.join(', ')
}

// ---------------------------------------------------------------------------
// Note view: markdown + GFM checklists (raw HTML stays disabled)
// ---------------------------------------------------------------------------

function stripNode<P extends { node?: unknown }>(props: P): Omit<P, 'node'> {
  const { node, ...rest } = props
  void node
  return rest
}

// Tailwind has no typography plugin here; each element gets its scoped
// classes. remark-gfm marks task lists via className (`contains-task-list`,
// `task-list-item`) — merged so checklists lose their bullets.
const markdownComponents: Components = {
  p: (props) => <p className="text-sm leading-relaxed break-words" {...stripNode(props)} />,
  a: (props) => (
    <a
      className="text-primary underline underline-offset-2"
      target="_blank"
      rel="noreferrer"
      {...stripNode(props)}
    />
  ),
  h1: (props) => <h3 className="text-lg font-semibold" {...stripNode(props)} />,
  h2: (props) => <h4 className="text-base font-semibold" {...stripNode(props)} />,
  h3: (props) => <h5 className="text-sm font-semibold" {...stripNode(props)} />,
  h4: (props) => <h6 className="text-sm font-semibold" {...stripNode(props)} />,
  h5: (props) => <h6 className="text-sm font-semibold" {...stripNode(props)} />,
  h6: (props) => <h6 className="text-sm font-semibold" {...stripNode(props)} />,
  ul: (props) => {
    const { className, ...rest } = stripNode(props)
    return (
      <ul
        className={cn(
          'list-disc space-y-1.5 pl-5 text-sm',
          className?.includes('contains-task-list') && 'list-none pl-0.5',
          className,
        )}
        {...rest}
      />
    )
  },
  ol: (props) => <ol className="list-decimal space-y-1.5 pl-5 text-sm" {...stripNode(props)} />,
  li: (props) => {
    const { className, ...rest } = stripNode(props)
    return <li className={cn('leading-relaxed', className)} {...rest} />
  },
  input: (props) => {
    const { className, ...rest } = stripNode(props)
    // GFM checklist boxes arrive disabled+checked/unchecked from the parser.
    return <input className={cn('mr-1.5 size-3.5 align-[-2px] accent-primary', className)} {...rest} />
  },
  blockquote: (props) => (
    <blockquote className="border-l-2 pl-3 text-muted-foreground" {...stripNode(props)} />
  ),
  code: (props) => {
    const { className, ...rest } = stripNode(props)
    return (
      <code
        className={cn('rounded bg-muted px-1 py-0.5 font-mono text-xs', className)}
        {...rest}
      />
    )
  },
  pre: (props) => (
    <pre
      className="overflow-x-auto rounded-md bg-muted p-3 [&_code]:bg-transparent [&_code]:p-0"
      {...stripNode(props)}
    />
  ),
  table: (props) => (
    <div className="overflow-x-auto">
      <table className="text-sm [&_td]:border [&_td]:px-2 [&_td]:py-1 [&_th]:border [&_th]:px-2 [&_th]:py-1 [&_th]:font-medium" {...stripNode(props)} />
    </div>
  ),
  hr: (props) => <hr className="border-border" {...stripNode(props)} />,
}

function NoteBody({ text }: { text: string | null }) {
  if (!text) return <EmptyBody />
  return (
    <div
      data-testid="note-markdown"
      className="space-y-3 rounded-lg bg-card px-4 py-4 ring-1 ring-foreground/10"
    >
      <Markdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
        {text}
      </Markdown>
    </div>
  )
}

function PlainBody({ text }: { text: string | null }) {
  if (!text) return <EmptyBody />
  return (
    <div className="rounded-lg bg-card px-4 py-4 ring-1 ring-foreground/10">
      <p className="text-sm leading-relaxed break-words whitespace-pre-wrap">{text}</p>
    </div>
  )
}

function EmptyBody() {
  return <p className="text-sm text-muted-foreground italic">This item has no text.</p>
}

// ---------------------------------------------------------------------------
// Meta inspector
// ---------------------------------------------------------------------------

function MetaInspector({ meta }: { meta: Record<string, unknown> }) {
  const [open, setOpen] = useState(false)
  const [copied, setCopied] = useState(false)
  const json = useMemo(() => JSON.stringify(meta, null, 2), [meta])

  useEffect(() => {
    if (!copied) return
    const timer = window.setTimeout(() => setCopied(false), 1500)
    return () => window.clearTimeout(timer)
  }, [copied])

  return (
    <section data-testid="meta-inspector" className="border-t pt-3">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1 text-xs font-medium text-muted-foreground outline-none hover:text-foreground focus-visible:rounded-sm focus-visible:ring-2 focus-visible:ring-ring"
      >
        <ChevronRight
          aria-hidden
          className={cn('size-3 transition-transform', open && 'rotate-90')}
        />
        Meta
      </button>
      {open && (
        <div className="relative mt-2">
          <pre
            data-testid="meta-json"
            className="max-h-96 overflow-auto rounded-md bg-muted p-3 pr-10 font-mono text-xs leading-relaxed"
          >
            {json}
          </pre>
          <Button
            variant="ghost"
            size="icon-xs"
            aria-label="Copy meta JSON"
            className="absolute top-1.5 right-1.5 text-muted-foreground"
            onClick={() => {
              navigator.clipboard.writeText(json).then(
                () => setCopied(true),
                () => undefined,
              )
            }}
          >
            {copied ? <Check aria-hidden /> : <Copy aria-hidden />}
          </Button>
        </div>
      )}
    </section>
  )
}

// ---------------------------------------------------------------------------
// Empty / error / loading states
// ---------------------------------------------------------------------------

function NotFoundState() {
  return (
    <div
      data-testid="item-not-found"
      className="mx-auto flex max-w-md flex-col items-center gap-3 px-6 py-20 text-center"
    >
      <div className="flex size-12 items-center justify-center rounded-full bg-muted">
        <FileQuestion aria-hidden className="size-5 text-muted-foreground" />
      </div>
      <h2 className="text-lg font-semibold">Item not found</h2>
      <p className="text-sm text-muted-foreground">
        There’s no item at this address — it may have been removed, or the link is stale.
      </p>
      <Button variant="outline" size="sm" asChild>
        <Link to="/">Back to search</Link>
      </Button>
    </div>
  )
}

function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div
      data-testid="item-error"
      className="mx-auto flex max-w-md flex-col items-center gap-3 px-6 py-20 text-center"
    >
      <div className="flex size-12 items-center justify-center rounded-full bg-destructive/10">
        <CircleAlert aria-hidden className="size-5 text-destructive" />
      </div>
      <h2 className="text-lg font-semibold">Couldn’t load this item</h2>
      <p className="text-sm text-muted-foreground">{message}</p>
      <Button variant="outline" size="sm" onClick={onRetry}>
        Try again
      </Button>
    </div>
  )
}

function DetailSkeleton() {
  return (
    <div aria-hidden className="space-y-4">
      <div className="flex gap-2">
        <Skeleton className="h-5 w-16" />
        <Skeleton className="h-5 w-20" />
      </div>
      <Skeleton className="h-7 w-2/3" />
      <ThreadSkeleton />
    </div>
  )
}

function ThreadSkeleton() {
  return (
    <div aria-hidden className="space-y-2" data-testid="thread-skeleton">
      {Array.from({ length: 3 }, (_, i) => (
        <div key={i} className="space-y-2 rounded-lg bg-card p-4 ring-1 ring-foreground/10">
          <div className="flex justify-between gap-3">
            <Skeleton className="h-4 w-1/3" />
            <Skeleton className="h-4 w-24" />
          </div>
          {i === 1 && (
            <>
              <Skeleton className="h-3.5 w-full" />
              <Skeleton className="h-3.5 w-4/5" />
            </>
          )}
        </div>
      ))}
    </div>
  )
}
