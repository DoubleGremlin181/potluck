import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createBrowserRouter, Link, RouterProvider } from 'react-router'
import { Layout } from '@/components/layout'
import { ThemeProvider } from '@/components/theme-provider'
import { Button } from '@/components/ui/button'
import { ImportsPage } from '@/pages/imports-page'
import { ItemPage } from '@/pages/item-page'
import { SearchPage } from '@/pages/search-page'
import { SettingsPage } from '@/pages/settings-page'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
      staleTime: 30_000,
    },
  },
})

function NotFoundPage() {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-3 p-8 text-center">
      <h2 className="text-lg font-semibold">Page not found</h2>
      <p className="text-sm text-muted-foreground">There’s nothing at this address.</p>
      <Button variant="outline" size="sm" asChild>
        <Link to="/">Back to search</Link>
      </Button>
    </div>
  )
}

const router = createBrowserRouter([
  {
    path: '/',
    Component: Layout,
    children: [
      { index: true, Component: SearchPage },
      { path: 'items/:itemId', Component: ItemPage },
      { path: 'imports', Component: ImportsPage },
      { path: 'settings', Component: SettingsPage },
      { path: '*', Component: NotFoundPage },
    ],
  },
])

function App() {
  return (
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </ThemeProvider>
  )
}

export default App
