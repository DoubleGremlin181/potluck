# Adding a New Ingester

## Quick Start

1. Add `SourceType.MY_SOURCE` to `src/potluck/models/base.py`
2. Create `src/potluck/ingesters/{source_name}/ingester.py`
3. Subclass `BaseIngester`, implement `detect_contents()` and `ingest()`
4. Decorate with `@register`
5. Add tests for detection and parsing
6. Add instructions markdown at `src/potluck/ingesters/{source_name}/instructions.md` along with any media used to `src/potluck/ingesters/{source_name}/instructions_assets`

## Tips & Tricks

* Common utilities such as csv and json parsers are available under `src/potluck/ingesters/utils/`
