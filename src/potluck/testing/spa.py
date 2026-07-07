"""Synthetic SPA build generator (Vite-shaped ``dist/``).

The SPA cold-load bench (#141) needs a web build that exists on every machine
CI included — nightly bench runners never run ``npm``.  This generator writes
a ``dist/`` directory with the exact shape Vite emits (``index.html``
referencing one hashed JS module and one stylesheet under ``assets/``), with
asset sizes mirroring the real beta.1 bundle (~650 KB JS + ~52 KB CSS).
Revisit the defaults if the real bundle grows materially.

Same arguments → identical output on every machine, forever.
"""

import re
from pathlib import Path

# Real web/dist weight at beta.1: index-*.js 648 KB, index-*.css 52 KB.
_JS_BYTES = 650_000
_CSS_BYTES = 52_000

_INDEX_HTML = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Potluck (synthetic SPA)</title>
    <script type="module" crossorigin src="/assets/index-synth.js"></script>
    <link rel="stylesheet" crossorigin href="/assets/index-synth.css">
  </head>
  <body>
    <div id="root"></div>
  </body>
</html>
"""

_ASSET_REF = re.compile(r'(?:src|href)="(/assets/[^"]+)"')


def write_spa_dist(dest: Path, *, js_bytes: int = _JS_BYTES, css_bytes: int = _CSS_BYTES) -> Path:
    """Write a synthetic Vite-shaped SPA build into *dest* and return it."""
    assets = dest / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (dest / "index.html").write_text(_INDEX_HTML)
    (assets / "index-synth.js").write_bytes(_filler(b"/* potluck synthetic js */\n", js_bytes))
    (assets / "index-synth.css").write_bytes(_filler(b"/* potluck synthetic css */\n", css_bytes))
    return dest


def referenced_assets(index_html: str) -> list[str]:
    """The ``/assets/...`` URLs a browser must fetch to cold-load *index_html*.

    Works on the synthetic build above and on a real Vite ``index.html``
    (both emit ``src=``/``href=`` attributes with absolute asset paths).
    """
    return _ASSET_REF.findall(index_html)


def _filler(pattern: bytes, size: int) -> bytes:
    """Deterministic *size*-byte payload built by repeating *pattern*."""
    return (pattern * (size // len(pattern) + 1))[:size]
