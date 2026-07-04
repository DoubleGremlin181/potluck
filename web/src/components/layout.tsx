import { CookingPot, Import, Search, Settings } from 'lucide-react'
import { useEffect } from 'react'
import { NavLink, Outlet, useNavigate } from 'react-router'
import { ThemeToggle } from '@/components/theme-toggle'
import { cn } from '@/lib/utils'

const NAV_ITEMS = [
  { to: '/', label: 'Search', icon: Search },
  { to: '/imports', label: 'Imports', icon: Import },
  { to: '/settings', label: 'Settings', icon: Settings },
] as const

function navClass({ isActive }: { isActive: boolean }): string {
  return cn(
    'flex items-center gap-2.5 rounded-md px-3 py-2 text-sm text-muted-foreground transition-colors hover:bg-sidebar-accent hover:text-foreground',
    isActive && 'bg-sidebar-accent font-medium text-foreground',
  )
}

function Brand() {
  return (
    <span className="flex items-center gap-2">
      <span className="flex size-7 items-center justify-center rounded-md bg-primary text-primary-foreground">
        <CookingPot aria-hidden className="size-4" />
      </span>
      <h1 className="text-base font-semibold tracking-tight">Potluck</h1>
    </span>
  )
}

/** App shell: sidebar navigation on desktop, top bar on small screens. */
export function Layout() {
  const navigate = useNavigate()

  // "/" focuses the search input from anywhere in the app (navigating to the
  // search page first when needed — it focuses its input on mount).
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key !== '/' || e.ctrlKey || e.metaKey || e.altKey) return
      const target = e.target as HTMLElement | null
      if (
        target &&
        (target.tagName === 'INPUT' ||
          target.tagName === 'TEXTAREA' ||
          target.tagName === 'SELECT' ||
          target.isContentEditable)
      ) {
        return
      }
      e.preventDefault()
      const input = document.querySelector<HTMLInputElement>('[data-testid="search-input"]')
      if (input) {
        input.focus()
        input.select()
      } else {
        void navigate('/')
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [navigate])

  return (
    <div className="flex h-dvh flex-col bg-background text-foreground md:flex-row">
      {/* Mobile top bar */}
      <header className="flex h-14 shrink-0 items-center justify-between border-b px-4 md:hidden">
        <Brand />
        <div className="flex items-center gap-1">
          {NAV_ITEMS.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              aria-label={label}
              className={({ isActive }) =>
                cn(
                  'flex size-9 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground',
                  isActive && 'bg-muted text-foreground',
                )
              }
            >
              <Icon aria-hidden className="size-4" />
            </NavLink>
          ))}
          <ThemeToggle />
        </div>
      </header>

      {/* Desktop sidebar */}
      <aside className="hidden w-56 shrink-0 flex-col border-r bg-sidebar md:flex">
        <div className="flex h-14 shrink-0 items-center border-b px-4">
          <Brand />
        </div>
        <nav aria-label="Primary" className="flex flex-col gap-1 p-3">
          {NAV_ITEMS.map(({ to, label, icon: Icon }) => (
            <NavLink key={to} to={to} className={navClass}>
              <Icon aria-hidden className="size-4" />
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="mt-auto flex items-center justify-between border-t p-3">
          <span className="px-1 text-xs text-muted-foreground">Local-first</span>
          <ThemeToggle />
        </div>
      </aside>

      <main className="flex min-h-0 min-w-0 flex-1 flex-col">
        <Outlet />
      </main>
    </div>
  )
}
