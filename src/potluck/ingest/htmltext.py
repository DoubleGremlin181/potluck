"""Lossy HTML -> plaintext extraction for indexing email bodies.

Stdlib only (html.parser): v1 needs readable, searchable text, not rendering
fidelity. Tag soup never raises — HTMLParser is tolerant by design.
"""

import re
from html.parser import HTMLParser

_SKIP_TAGS = frozenset({"script", "style", "head", "title", "template"})
_BLOCK_TAGS = frozenset({
    "address", "article", "aside", "blockquote", "br", "div", "dl", "dt", "dd",
    "fieldset", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6",
    "header", "hr", "li", "main", "nav", "ol", "p", "pre", "section", "table",
    "td", "th", "tr", "ul",
})  # fmt: skip

_WS = re.compile(r"\s+")


class _TextExtractor(HTMLParser):
    """Collects text chunks; block tags become newline markers."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS:
            self.chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in _BLOCK_TAGS:
            self.chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth and data:
            # Whitespace inside text nodes (incl. source newlines) carries no
            # structure; only block tags produce line breaks.
            self.chunks.append(_WS.sub(" ", data))


def html_to_text(html: str) -> str:
    """Extract readable plaintext: tags stripped, blocks become line breaks,
    entities decoded, whitespace collapsed, no blank lines."""
    extractor = _TextExtractor()
    extractor.feed(html)
    extractor.close()
    joined = "".join(extractor.chunks)
    lines = (_WS.sub(" ", line).strip() for line in joined.split("\n"))
    return "\n".join(line for line in lines if line)
