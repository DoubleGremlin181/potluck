"""Shared identity helpers for sources whose exports carry no native ids.

Fingerprint sources (whatsapp #142, ynab #144) hash RAW exported bytes —
never parsed/cleaned values — so parser evolution can never re-mint
identities and re-insert a whole archive as duplicates (P2 finding 6).
Identical raw blocks are real (two "ok" in the same minute, the same coffee
twice a day), so each fingerprint scope needs a first-seen ``#N`` occurrence
discipline; this module owns that discipline so every such source numbers
duplicates identically.
"""


def occurrence_suffix(counters: dict[str, int], fingerprint: str) -> str:
    """Return the ``#N`` external-id suffix for this sighting of *fingerprint*.

    Mutates *counters* (the caller-scoped occurrence map): the first sighting
    gets ``""``, later ones ``"#2"``, ``"#3"``, … in first-seen order, which
    is stable for re-exports that preserve row order. A truncated re-export
    that drops early occurrences re-numbers only those duplicate groups.

    The caller chooses the counter scope — that choice decides whether
    repeated identical blocks import as distinct items (one shared map per
    run: whatsapp, where archive members are different chats) or dedup across
    members (one map per member: ynab, where two Register members are
    re-exports of the same budget).
    """
    occurrence = counters[fingerprint] = counters.get(fingerprint, 0) + 1
    return "" if occurrence == 1 else f"#{occurrence}"
