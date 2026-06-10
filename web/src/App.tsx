import { StatsPage } from '@/components/stats-page'
import { ThemeProvider } from '@/components/theme-provider'

function App() {
  return (
    <ThemeProvider>
      <StatsPage />
    </ThemeProvider>
  )
}

export default App
