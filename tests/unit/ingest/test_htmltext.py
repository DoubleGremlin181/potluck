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
