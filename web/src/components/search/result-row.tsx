import { Link } from 'react-router'
import { KindIcon } from '@/components/kind-icon'
import type { SearchHit } from '@/lib/api'
import { formatDate } from '@/lib/format'
import { parseHighlight } from '@/lib/highlight'

/** One search hit: kind icon, highlighted title, snippet, date. */
export function ResultRow({ hit }: { hit: SearchHit }) {
  const title = hit.title_highlight
    ? parseHighlight(hit.title_highlight)
    : (hit.title ?? 'Untitled')
  const date = formatDate(hit.ts)
  return (
    <Link
      to={`/items/${hit.id}`}
      data-testid="result-row"
      data-item-id={hit.id}
      data-kind={hit.kind}
      className="flex gap-3 border-b px-4 py-3.5 outline-none transition-colors hover:bg-muted/50 focus-visible:bg-muted/50 md:px-6"
    >
      <span
        title={hit.kind}
        className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground"
      >
        <KindIcon kind={hit.kind} className="size-3.5" />
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex items-baseline justify-between gap-3">
          <span className="truncate text-sm font-medium">{title}</span>
          {date && (
            <span className="shrink-0 text-xs whitespace-nowrap text-muted-foreground">
              {date}
            </span>
          )}
        </span>
        <span className="mt-0.5 line-clamp-2 text-sm text-muted-foreground">
          {parseHighlight(hit.snippet)}
        </span>
      </span>
    </Link>
  )
}
