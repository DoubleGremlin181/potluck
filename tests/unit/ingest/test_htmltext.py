"""HTML -> readable plaintext extraction (#122). Stdlib-only, lossy by design."""

from potluck.ingest.htmltext import html_to_text


def test_strips_tags() -> None:
    assert html_to_text("<p>Hello <b>world</b></p>") == "Hello world"


def test_drops_script_and_style_content() -> None:
    out = html_to_text(
        "<head><style>p{color:red}</style></head>"
        "<body><script>alert('x')</script><p>visible</p></body>"
    )
    assert "visible" in out
    assert "alert" not in out
    assert "color" not in out


def test_block_tags_become_newlines() -> None:
    out = html_to_text("<div>one</div><div>two</div><p>three</p>")
    lines = out.splitlines()
    assert "one" in lines
    assert "two" in lines
    assert "three" in lines


def test_br_becomes_newline() -> None:
    out = html_to_text("first<br>second")
    assert out.splitlines() == ["first", "second"]


def test_entities_decoded() -> None:
    assert html_to_text("fish &amp; chips &lt;3") == "fish & chips <3"


def test_whitespace_collapsed() -> None:
    out = html_to_text("<p>  lots   of\n\n   space  </p>")
    assert out == "lots of space"


def test_no_blank_line_runs() -> None:
    out = html_to_text("<div>a</div><div></div><div></div><div>b</div>")
    assert "\n\n\n" not in out


def test_empty_and_text_only_input() -> None:
    assert html_to_text("") == ""
    assert html_to_text("just text") == "just text"


# ---------------------------------------------------------------------------
# implicitly closed head-level elements (#198 review 7) + chunk collapse pin
# ---------------------------------------------------------------------------


def test_implicit_head_close_via_body() -> None:
    """</head> is optional in HTML (implied by <body>); the body text must
    not be swallowed by the still-open head skip."""
    assert html_to_text("<head><title>t</title><body><p>hello</p>") == "hello"


def test_implicit_head_close_via_block_tag() -> None:
    assert html_to_text("<head><style>p{color:red}</style><p>hello</p>") == "hello"


def test_unclosed_title_then_body() -> None:
    out = html_to_text("<head><title>page title<body><p>content</p>")
    assert "content" in out


def test_stray_end_tags_do_not_unbalance() -> None:
    assert html_to_text("</head></style><p>a</p></head><p>b</p>").splitlines() == ["a", "b"]


def test_source_newlines_collapse_identically() -> None:
    """Pin for the chunk-pass simplification: \\r\\n and \\t inside text nodes
    still collapse to single spaces."""
    assert html_to_text("<p>a\r\n\tb</p>") == "a b"
    assert html_to_text("a\nb") == "a b"


# ---------------------------------------------------------------------------
# RCDATA/CDATA tokenization variants. CPython gh-135462 (CVE-2025-6069
# hardening, backported into 3.13.x maintenance releases) rewrote
# html.parser tokenization: <title>/<textarea> became RCDATA and
# xmp/iframe/noembed/noframes became rawtext (CDATA). An UNCLOSED rawtext
# element now swallows the whole document tail as one character-data blob —
# no tag events fire — where older 3.13.x kept tokenizing markup after it.
# These tests pin ONE output contract that must hold on both parser
# generations (the CI/local split that motivated them: 3.13.14 vs 3.13.2).
# ---------------------------------------------------------------------------


def test_unclosed_title_recovers_document_tail() -> None:
    """The swallowed markup after an unclosed <title> is re-parsed; only the
    title's own text (up to the first '<') is dropped."""
    assert html_to_text("<head><title>page title<body><p>content</p>") == "content"


def test_unclosed_title_without_markup_extracts_nothing() -> None:
    assert html_to_text("<head><title>just a title") == ""


def test_unclosed_textarea_keeps_text_and_recovers_tail() -> None:
    """<textarea> text is legitimate content (not a skip tag); the swallowed
    markup after it must be re-parsed, never emitted raw."""
    assert html_to_text("<textarea>abc<p>content</p>") == "abc\ncontent"


def test_chained_unclosed_rcdata_elements() -> None:
    """Each recovery pass may end inside another unclosed RCDATA element."""
    assert html_to_text("<head><title>t<body><p>a</p><textarea>x<p>b</p>") == "a\nx\nb"


def test_unclosed_style_swallows_to_eof() -> None:
    """Rawtext without any end tag consumes everything to EOF on BOTH parser
    generations (old: silently; new: as skipped character data) — pin the
    swallow so the contract stays cross-version."""
    assert html_to_text("<head><style>p{color:red}<p>hello</p>") == ""


def test_new_rawtext_elements_are_skipped() -> None:
    """iframe/xmp/noembed/noframes fallback content is never indexed — on new
    parsers an unclosed one would otherwise leak raw markup into the text."""
    assert html_to_text("<iframe>fallback</iframe><p>real</p>") == "real"
    assert html_to_text("<p>a</p><iframe>tail<p>x</p>") == "a"
