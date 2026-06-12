"""Deterministic synthetic data generators (stdlib randomness only).

Shipped inside the package so tests, committed fixtures, and bench scenarios
all share one source of synthetic data. Same arguments -> identical output,
on every machine, forever. Never put real personal data here.
"""

import random
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

WORDS = (
    "amber", "basil", "cedar", "dahlia", "ember", "fennel", "garnet", "hazel",
    "indigo", "juniper", "kelp", "lichen", "maple", "nutmeg", "ochre", "pepper",
    "quartz", "rowan", "saffron", "thyme", "umber", "violet", "walnut", "yarrow",
    "zinnia", "brook", "cliff", "dune", "fjord", "glade", "harbor", "island",
    "meadow", "orchard", "prairie", "ridge", "summit", "tundra", "valley", "willow",
)  # fmt: skip


def synthetic_notes(count: int, seed: int = 42) -> Iterator[dict[str, str]]:
    """Yield ``count`` fake notes as ``{"title", "text", "ts"}`` dicts.

    Timestamps are ISO-8601 UTC, spaced minutes apart from 2020-01-01.
    """
    rng = random.Random(seed)
    start = datetime(2020, 1, 1, tzinfo=UTC)
    for i in range(count):
        title = " ".join(rng.choices(WORDS, k=3)).title()
        sentences = [
            " ".join(rng.choices(WORDS, k=rng.randint(5, 12))).capitalize() + "."
            for _ in range(rng.randint(1, 4))
        ]
        ts = (start + timedelta(minutes=i * 7 + rng.randint(0, 5))).isoformat()
        yield {"title": title, "text": " ".join(sentences), "ts": ts}
