"""Golden queries: the guardrail for FTS5 ranking quality (#129).

Each entry says: running *query* must surface at least one of the expected
items in the top *k*. Expectations are pinned to generator ground truth —
titles for Keep (member-derived external_ids are unwieldy), deterministic
``mid:synth-<seed>-<index>@potluck.test`` external_ids for Gmail. Extended
every search phase; see README.md for the add-a-query workflow.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class GoldenQuery:
    """One relevance expectation: any expected title/external_id in top-k."""

    query: str
    expect_titles: tuple[str, ...] = ()
    expect_external_ids: tuple[str, ...] = ()
    k: int = 5
    prefix: bool = False


GOLDEN: tuple[GoldenQuery, ...] = (
    # --- Keep notes: distinctive three-word titles -------------------------
    GoldenQuery("ember walnut hazel", expect_titles=("Ember Walnut Hazel",)),
    GoldenQuery("tundra ochre zinnia", expect_titles=("Tundra Ochre Zinnia",)),
    GoldenQuery("meadow fjord nutmeg", expect_titles=("Meadow Fjord Nutmeg",)),
    GoldenQuery("orchard garnet rowan", expect_titles=("Orchard Garnet Rowan",)),
    GoldenQuery("indigo valley ochre", expect_titles=("Indigo Valley Ochre",)),
    GoldenQuery("zinnia umber umber", expect_titles=("Zinnia Umber Umber",)),
    # Body-only words still find the note (text match, not just title)
    GoldenQuery("cedar indigo dune rowan", expect_titles=("Ember Walnut Hazel",)),
    # Title match must outrank body-only matches (BM25 title weight)
    GoldenQuery("amber kelp", expect_titles=("Amber Kelp Amber",), k=3),
    # kind filter composes with terms
    GoldenQuery("kind:note ember walnut", expect_titles=("Ember Walnut Hazel",), k=3),
    # --- Gmail: subjects and threads ---------------------------------------
    GoldenQuery(
        "violet glade rowan",
        expect_external_ids=("mid:synth-7-000000@potluck.test",),
        expect_titles=("Re: Violet Glade Rowan",),
    ),
    # (message index 1 has no Message-ID — the generator's 4% ratio — so its
    # external_id is a noid: fingerprint; pin by subject instead)
    GoldenQuery("willow fennel walnut", expect_titles=("Willow Fennel Walnut",)),
    GoldenQuery(
        "fennel fjord dune",
        expect_external_ids=("mid:synth-7-000004@potluck.test",),
    ),
    GoldenQuery(
        "indigo island cliff",
        expect_external_ids=("mid:synth-7-000006@potluck.test",),
        expect_titles=("Re: Indigo Island Cliff",),
    ),
    GoldenQuery(
        "harbor fennel walnut",
        expect_external_ids=("mid:synth-7-000005@potluck.test",),
    ),
    GoldenQuery("garnet violet cliff", expect_external_ids=("mid:synth-7-000007@potluck.test",)),
    # kind filter restricts to email
    GoldenQuery(
        "kind:email violet glade",
        expect_titles=("Violet Glade Rowan", "Re: Violet Glade Rowan"),
    ),
    # --- Operators ----------------------------------------------------------
    GoldenQuery(
        "from:hazel@potluck.test violet glade",
        expect_external_ids=("mid:synth-7-000000@potluck.test",),
        k=3,
    ),
    GoldenQuery(
        "from:hazel violet glade",  # bare-name prefix form
        expect_external_ids=("mid:synth-7-000000@potluck.test",),
        k=3,
    ),
    GoldenQuery(
        "source:gmail fennel fjord",
        expect_external_ids=("mid:synth-7-000004@potluck.test",),
    ),
    GoldenQuery("source:google_keep ember walnut", expect_titles=("Ember Walnut Hazel",), k=3),
    # Date windows: keep notes live in 2020, gmail in 2021
    GoldenQuery("after:2021-01-01 walnut fennel", expect_titles=("Willow Fennel Walnut",)),
    GoldenQuery("before:2021-01-01 ember walnut", expect_titles=("Ember Walnut Hazel",), k=3),
    # Filter-only query (no terms): newest matching items
    GoldenQuery(
        "from:hazel@potluck.test after:2021-01-01",
        expect_external_ids=("mid:synth-7-000000@potluck.test",),
        k=60,
    ),
    # --- Prefix (search-as-you-type) ----------------------------------------
    GoldenQuery("ember walnut haz", expect_titles=("Ember Walnut Hazel",), prefix=True),
    GoldenQuery("violet glade row", expect_titles=("Violet Glade Rowan",), prefix=True),
    GoldenQuery("meadow fjord nutm", expect_titles=("Meadow Fjord Nutmeg",), prefix=True),
    GoldenQuery("orchard garn", expect_titles=("Orchard Garnet Rowan",), prefix=True),
    # --- Robustness ----------------------------------------------------------
    # Diacritics fold in both directions (remove_diacritics=2)
    GoldenQuery("émber wálnut hazel", expect_titles=("Ember Walnut Hazel",)),
    # Case-insensitive
    GoldenQuery("EMBER WALNUT HAZEL", expect_titles=("Ember Walnut Hazel",)),
    # Punctuation noise around terms is ignored
    GoldenQuery('"ember" walnut, hazel!', expect_titles=("Ember Walnut Hazel",)),
)
