"""Lossy HTML -> plaintext extraction for indexing email bodies.

Stdlib only (html.parser): v1 needs readable, searchable text, not rendering
fidelity. Tag soup never raises — HTMLParser is tolerant by design.

CPython gh-135462 (CVE-2025-6069 hardening, backported into 3.13.x
maintenance releases) rewrote html.parser tokenization: <title>/<textarea>
became RCDATA and xmp/iframe/noembed/noframes became rawtext, so an UNCLOSED
<title> now swallows the document tail as character data — no tag events
fire — where older parsers kept tokenizing markup. The extractor buffers
RCDATA text and re-feeds a swallowed tail from the raw source (see
html_to_text) so both parser generations produce identical output.
"""

import re
from html.parser import HTMLParser

# script/style/template require explicit end tags; head/title close
# implicitly. iframe/noembed/noframes/xmp are rawtext on newer parsers —
# skipping them keeps output identical across parser generations (an unclosed
# one would otherwise leak raw markup) and their fallback content out of the
# index.
_SKIP_TAGS = frozenset({
    "script", "style", "head", "title", "template",
    "iframe", "noembed", "noframes", "xmp",
})  # fmt: skip
# Head-level skips whose end tag is optional in HTML: <body> or any block tag
# implies they closed.
_IMPLICIT_CLOSE_TAGS = frozenset({"head", "title", "style"})
# RCDATA elements: newer parsers deliver their content purely as character
# data (markup included) until the exact end tag. Buffered so an
# unclosed-at-EOF element's swallowed markup can be recovered; on older
# parsers any tag event flushes the buffer and extraction proceeds as before.
_RCDATA_TAGS = frozenset({"title", "textarea"})
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
        super().__init__(convert_charrefs=True)  # calls reset(), creating _raw
        self.chunks: list[str] = []
        self._skip_stack: list[str] = []
        self._rcdata_tag: str | None = None
        self._rcdata_buf: list[str] = []
        self._rcdata_pos: tuple[int, int] = (1, 0)
        self._rcdata_taglen = 0

    def feed(self, data: str) -> None:
        # Keep the raw source of the current reset() epoch: take_rawtext_tail
        # maps getpos() back into it. Accumulating per feed() keeps offsets
        # chunk-safe (getpos is cumulative); the whole-document single feed
        # from html_to_text just aliases the string ('' + s copies nothing).
        self._raw += data
        super().feed(data)

    def reset(self) -> None:
        super().reset()
        self._raw = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._rcdata_tag is not None:
            self._flush_rcdata()
        if tag in _RCDATA_TAGS:
            self._rcdata_tag = tag
            self._rcdata_pos = self.getpos()  # (1-based line, col) of the '<'
            self._rcdata_taglen = len(self.get_starttag_text() or "")
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
        if self._rcdata_tag is not None:
            self._flush_rcdata()
        if tag in _SKIP_TAGS:
            if tag in self._skip_stack:
                # Closing an outer skip closes everything nested inside it;
                # stray end tags without an open element are ignored.
                idx = len(self._skip_stack) - 1 - self._skip_stack[::-1].index(tag)
                del self._skip_stack[idx:]
        elif tag in _BLOCK_TAGS:
            self.chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self._rcdata_tag is not None:
            self._rcdata_buf.append(data)
            return
        self._emit_data(data)

    def _emit_data(self, data: str) -> None:
        if not self._skip_stack and data:
            # Source newlines carry no structure (only block tags produce line
            # breaks); runs of whitespace are collapsed once, per line, in
            # html_to_text — no need to regex every chunk here.
            self.chunks.append(data.replace("\n", " "))

    def _flush_rcdata(self) -> None:
        """A tag event arrived while an RCDATA element was open (older-parser
        tokenization, or the element's own end tag): its buffered text is
        plain character data — emit under the unchanged skip semantics."""
        self._rcdata_tag = None
        data = "".join(self._rcdata_buf)
        self._rcdata_buf.clear()
        self._emit_data(data)

    def take_rawtext_tail(self) -> str | None:
        """Call after close(): recover from EOF inside an unclosed RCDATA
        element (newer parsers swallowed the rest of the document as one
        character-data blob; older ones only get here when nothing after the
        start tag parsed as a tag). Discards the decoded buffer and returns
        the RAW source suffix from just past the element's start tag for the
        caller to re-feed — pass 2 then tokenizes exactly what pre-RCDATA
        parsers saw. The decoded buffer must NOT be re-fed: charrefs would
        decode twice and a decoded '&lt;' would turn into markup. None when
        parsing ended normally or nothing follows the start tag."""
        if self._rcdata_tag is None:
            return None
        self._rcdata_tag = None
        self._rcdata_buf.clear()
        lineno, col = self._rcdata_pos
        start = 0
        for _ in range(lineno - 1):  # getpos() lines are 1-based, '\n'-split
            start = self._raw.index("\n", start) + 1
        start += col + self._rcdata_taglen
        return self._raw[start:] or None


def html_to_text(html: str) -> str:
    """Extract readable plaintext: tags stripped, blocks become line breaks,
    entities decoded, whitespace collapsed, no blank lines."""
    extractor = _TextExtractor()
    extractor.feed(html)
    extractor.close()
    # Unclosed <title>/<textarea>: re-tokenize the swallowed raw tail,
    # matching what pre-RCDATA parsers did by continuing to emit tag events.
    # reset() (documented API) clears the parser's rawtext mode but leaves our
    # chunks and skip stack intact, so head-level skip semantics carry across
    # passes. Terminates: each pass consumes at least the next RCDATA start
    # tag before another swallow can begin. No cost on the happy path.
    while (tail := extractor.take_rawtext_tail()) is not None:
        extractor.reset()
        extractor.feed(tail)
        extractor.close()
    joined = "".join(extractor.chunks)
    lines = (_WS.sub(" ", line).strip() for line in joined.split("\n"))
    return "\n".join(line for line in lines if line)
