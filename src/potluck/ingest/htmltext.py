"""Lossy HTML -> plaintext extraction for indexing email bodies.

Stdlib only (html.parser): v1 needs readable, searchable text, not rendering
fidelity. Tag soup never raises — HTMLParser is tolerant by design.
"""

import re
from html.parser import HTMLParser

_SKIP_TAGS = frozenset({"script", "style", "head", "title", "template"})
# Head-level skips whose end tag is optional in HTML: <body> or any block tag
# implies they closed (script/template require explicit end tags; html.parser
# treats script/style content as CDATA, so only well-formed ones get here).
_IMPLICIT_CLOSE_TAGS = frozenset({"head", "title", "style"})
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
        self._skip_stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_stack.append(tag)
            return
        if tag == "body" or tag in _BLOCK_TAGS:
            # Flow content implicitly closes open head-level elements — an
            # HTML-only email without </head> must not be swallowed whole.
            while self._skip_stack and self._skip_stack[-1] in _IMPLICIT_CLOSE_TAGS:
                self._skip_stack.pop()
        if tag in _BLOCK_TAGS:
            self.chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            if tag in self._skip_stack:
                # Closing an outer skip closes everything nested inside it;
                # stray end tags without an open element are ignored.
                idx = len(self._skip_stack) - 1 - self._skip_stack[::-1].index(tag)
                del self._skip_stack[idx:]
        elif tag in _BLOCK_TAGS:
            self.chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_stack and data:
            # Source newlines carry no structure (only block tags produce line
            # breaks); runs of whitespace are collapsed once, per line, in
            # html_to_text — no need to regex every chunk here.
            self.chunks.append(data.replace("\n", " "))


def html_to_text(html: str) -> str:
    """Extract readable plaintext: tags stripped, blocks become line breaks,
    entities decoded, whitespace collapsed, no blank lines."""
    extractor = _TextExtractor()
    extractor.feed(html)
    extractor.close()
    joined = "".join(extractor.chunks)
    lines = (_WS.sub(" ", line).strip() for line in joined.split("\n"))
    return "\n".join(line for line in lines if line)
