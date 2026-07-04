import { useQuery } from '@tanstack/react-query'
import { ArrowLeft, CircleAlert } from 'lucide-react'
import { useNavigate, useParams } from 'react-router'
import { KindIcon } from '@/components/kind-icon'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { fetchItem, type EmailDetail } from '@/lib/api'
import { formatDate } from '@/lib/format'

function mailbox(addr: string | null, name: string | null): string | null {
  if (!addr) return null
  return name ? `${name} <${addr}>` : addr
}

function EmailMeta({ email }: { email: EmailDetail }) {
  const from = mailbox(email.from_addr, email.from_name)
  const to = email.to_addrs
    .map((addr, i) => mailbox(addr, email.to_names[i] || null))
    .filter(Boolean)
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
      {from && (
        <>
          <dt className="text-muted-foreground">From</dt>
          <dd className="break-all">{from}</dd>
        </>
      )}
      {to.length > 0 && (
        <>
          <dt className="text-muted-foreground">To</dt>
          <dd className="break-all">{to.join(', ')}</dd>
        </>
      )}
      {email.labels.length > 0 && (
        <>
          <dt className="text-muted-foreground">Labels</dt>
          <dd className="flex flex-wrap gap-1">
            {email.labels.map((label) => (
              <Badge key={label} variant="outline" className="font-normal">
                {label}
              </Badge>
            ))}
          </dd>
        </>
      )}
    </dl>
  )
}

/** Placeholder item detail page — the full view lands with #135. */
export function ItemPage() {
  const { itemId } = useParams()
  const navigate = useNavigate()
  const id = Number(itemId)
  const validId = Number.isInteger(id) && id > 0

  const { data: item, error, isPending } = useQuery({
    queryKey: ['item', id],
    queryFn: ({ signal }) => fetchItem(id, signal),
    enabled: validId,
  })

  function goBack() {
    if (window.history.length > 1) void navigate(-1)
    else void navigate('/')
  }

  const date = item ? formatDate(item.ts) : null

  return (
    <div className="min-h-0 flex-1 overflow-y-auto" data-testid="item-page">
      <div className="mx-auto w-full max-w-3xl space-y-4 p-4 md:p-6">
        <Button variant="ghost" size="sm" onClick={goBack} className="-ml-2 text-muted-foreground">
          <ArrowLeft aria-hidden className="size-4" />
          Back to search
        </Button>

        {!validId || error ? (
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <CircleAlert aria-hidden className="size-4 text-destructive" />
                Couldn’t load this item
              </CardTitle>
            </CardHeader>
            <CardContent className="text-sm text-muted-foreground">
              {error instanceof Error ? error.message : 'The item id in the URL is not valid.'}
            </CardContent>
          </Card>
        ) : isPending ? (
          <div className="space-y-3">
            <Skeleton className="h-7 w-2/3" />
            <Skeleton className="h-4 w-1/3" />
            <Skeleton className="h-40 w-full" />
          </div>
        ) : (
          <article className="space-y-4">
            <header className="space-y-2">
              <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
                <Badge variant="secondary" className="gap-1 font-normal">
                  <KindIcon kind={item.kind} className="size-3" />
                  {item.kind}
                </Badge>
                <Badge variant="outline" className="font-normal">
                  {item.source}
                </Badge>
                {date && <span data-testid="item-date">{date}</span>}
              </div>
              <h2 data-testid="item-title" className="text-xl font-semibold tracking-tight">
                {item.title ?? 'Untitled'}
              </h2>
            </header>

            {item.email && <EmailMeta email={item.email} />}

            <Card size="sm">
              <CardContent>
                {item.text ? (
                  <p className="text-sm leading-relaxed whitespace-pre-wrap">{item.text}</p>
                ) : (
                  <p className="text-sm text-muted-foreground italic">This item has no text.</p>
                )}
              </CardContent>
            </Card>
          </article>
        )}
      </div>
    </div>
  )
}
