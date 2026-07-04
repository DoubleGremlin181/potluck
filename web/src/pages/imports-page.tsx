import { Inbox } from 'lucide-react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

/** Placeholder — import management UI lands later in Phase 3. */
export function ImportsPage() {
  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-3xl space-y-6 p-4 md:p-6">
        <h2 className="text-xl font-semibold tracking-tight">Imports</h2>
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Inbox aria-hidden className="size-4 text-muted-foreground" />
              Import management is coming soon
            </CardTitle>
            <CardDescription>
              Uploading and tracking archive imports from the browser lands later in this phase.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-2 text-sm text-muted-foreground">
            <p>Until then, import archives from the command line:</p>
            <pre className="overflow-x-auto rounded-md bg-muted px-3 py-2 font-mono text-xs text-foreground">
              potluck import path/to/takeout.zip
            </pre>
            <p>Imported items are searchable right away.</p>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
