import { useEffect, useState } from 'react'
import { ThemeProviderContext, resolveTheme, type Theme } from '@/lib/theme-context'

interface ThemeProviderProps {
  children: React.ReactNode
  storageKey?: string
}

export function ThemeProvider({ children, storageKey = 'potluck-ui-theme' }: ThemeProviderProps) {
  const [theme, setTheme] = useState<Theme>(
    () => (localStorage.getItem(storageKey) as Theme | null) ?? 'system',
  )

  useEffect(() => {
    const root = window.document.documentElement
    root.classList.remove('light', 'dark')
    root.classList.add(resolveTheme(theme))
  }, [theme])

  const value = {
    theme,
    setTheme: (next: Theme) => {
      localStorage.setItem(storageKey, next)
      setTheme(next)
    },
  }

  return <ThemeProviderContext.Provider value={value}>{children}</ThemeProviderContext.Provider>
}
