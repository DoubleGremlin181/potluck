import { CalendarRange, Check, ChevronDown, X } from 'lucide-react'
import type { ReactNode } from 'react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'

export interface FilterOption {
  value: string
  label: string
  icon?: ReactNode
}

/** Popover multi-select whose selections surface as removable chips elsewhere. */
export function MultiSelectFilter({
  label,
  icon,
  options,
  selected,
  onToggle,
  testId,
  loading = false,
  emptyText = 'Nothing available',
}: {
  label: string
  icon: ReactNode
  options: FilterOption[]
  selected: string[]
  onToggle: (value: string) => void
  testId: string
  loading?: boolean
  emptyText?: string
}) {
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="outline" size="sm" data-testid={testId} className="gap-1.5">
          {icon}
          {label}
          {selected.length > 0 && (
            <Badge variant="secondary" className="rounded-sm px-1 font-normal tabular-nums">
              {selected.length}
            </Badge>
          )}
          <ChevronDown aria-hidden className="size-3.5 text-muted-foreground" />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="max-h-80 w-56 overflow-y-auto p-1">
        {loading ? (
          <div className="space-y-1 p-1">
            <Skeleton className="h-7 w-full" />
            <Skeleton className="h-7 w-full" />
            <Skeleton className="h-7 w-full" />
          </div>
        ) : options.length === 0 ? (
          <p className="px-2 py-1.5 text-sm text-muted-foreground">{emptyText}</p>
        ) : (
          options.map((option) => {
            const checked = selected.includes(option.value)
            return (
              <button
                key={option.value}
                type="button"
                aria-pressed={checked}
                onClick={() => onToggle(option.value)}
                className="flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-sm outline-none hover:bg-muted focus-visible:bg-muted"
              >
                <span
                  aria-hidden
                  className={cn(
                    'flex size-4 shrink-0 items-center justify-center rounded-[4px] border border-input',
                    checked && 'border-primary bg-primary text-primary-foreground',
                  )}
                >
                  {checked && <Check className="size-3" />}
                </span>
                {option.icon}
                <span className="truncate">{option.label}</span>
              </button>
            )
          })
        )}
      </PopoverContent>
    </Popover>
  )
}

/** Popover with the after/before date bounds (chips render them elsewhere). */
export function DateFilter({
  after,
  before,
  onChange,
}: {
  after: string
  before: string
  onChange: (bound: 'after' | 'before', value: string) => void
}) {
  const activeCount = (after ? 1 : 0) + (before ? 1 : 0)
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="outline" size="sm" data-testid="filter-date" className="gap-1.5">
          <CalendarRange aria-hidden className="size-3.5" />
          Dates
          {activeCount > 0 && (
            <Badge variant="secondary" className="rounded-sm px-1 font-normal tabular-nums">
              {activeCount}
            </Badge>
          )}
          <ChevronDown aria-hidden className="size-3.5 text-muted-foreground" />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-64 space-y-3 p-3">
        <div className="space-y-1.5">
          <label htmlFor="filter-after" className="text-xs font-medium text-muted-foreground">
            After (inclusive)
          </label>
          <Input
            id="filter-after"
            type="date"
            value={after}
            max={before || undefined}
            onChange={(e) => onChange('after', e.target.value)}
            className="h-8"
          />
        </div>
        <div className="space-y-1.5">
          <label htmlFor="filter-before" className="text-xs font-medium text-muted-foreground">
            Before (exclusive)
          </label>
          <Input
            id="filter-before"
            type="date"
            value={before}
            min={after || undefined}
            onChange={(e) => onChange('before', e.target.value)}
            className="h-8"
          />
        </div>
      </PopoverContent>
    </Popover>
  )
}

/** One active filter, rendered as a removable chip. */
export function FilterChip({
  label,
  icon,
  onRemove,
}: {
  label: string
  icon?: ReactNode
  onRemove: () => void
}) {
  return (
    <Badge variant="secondary" className="gap-1 py-1 pr-1 font-normal">
      {icon}
      {label}
      <button
        type="button"
        aria-label={`Remove filter: ${label}`}
        onClick={onRemove}
        className="ml-0.5 rounded-sm p-0.5 outline-none hover:bg-foreground/10 focus-visible:ring-2 focus-visible:ring-ring"
      >
        <X aria-hidden className="size-3" />
      </button>
    </Badge>
  )
}
