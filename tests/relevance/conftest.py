"""Session corpus for the relevance mini-eval (#129).

One deterministic mixed corpus per test session (per xdist worker): 400
synthetic Keep notes + 600 synthetic Gmail messages, both seed 7 — the same
generators that produce committed fixtures, so expectations in
golden_queries.py are generator ground truth, not search output.
"""

from collections.abc import Iterator

import pytest

from potluck.core.config import Settings
from potluck.services.context import AppContext, create_context
from potluck.services.imports import import_path
from potluck.testing.keep import write_keep_takeout
from potluck.testing.mbox import write_gmail_takeout

KEEP_COUNT = 400
GMAIL_COUNT = 600
SEED = 7


@pytest.fixture(scope="session")
def relevance_ctx(tmp_path_factory: pytest.TempPathFactory) -> Iterator[AppContext]:
    base = tmp_path_factory.mktemp("relevance")
    ctx = create_context(Settings(db_path=base / "potluck.db"))
    import_path(ctx, write_keep_takeout(base / "keep", KEEP_COUNT, seed=SEED, fmt="dir"))
    import_path(ctx, write_gmail_takeout(base / "gmail", GMAIL_COUNT, seed=SEED, fmt="dir"))
    yield ctx
    ctx.db.close()
