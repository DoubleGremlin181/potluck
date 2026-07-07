import type { ReactNode } from 'react'

/** Parse FTS5 `[match]` highlight markers into `<mark>` elements.
 *
 * The API brackets matched terms with literal `[` / `]` characters
 * (highlight/snippet markers chosen in `search/fts.py`). This walks the
 * string and wraps each balanced pair in a `<mark>` — split-and-map, never
 * `dangerouslySetInnerHTML`, so hit content can't inject markup. Unbalanced
 * markers (an unmatched `[` or a stray `]` in the source text) render as
 * literal characters.
 */
export function parseHighlight(text: string): ReactNode[] {
  const nodes: ReactNode[] = []
  let i = 0
  let key = 0
  while (i < text.length) {
    const open = text.indexOf('[', i)
    if (open === -1) {
      nodes.push(text.slice(i))
      break
    }
    const close = text.indexOf(']', open + 1)
    if (close === -1) {
      // Unbalanced opener: everything from here is literal text.
      nodes.push(text.slice(i))
      break
    }
    if (open > i) nodes.push(text.slice(i, open))
    nodes.push(<mark key={key++}>{text.slice(open + 1, close)}</mark>)
    i = close + 1
  }
  return nodes
}
