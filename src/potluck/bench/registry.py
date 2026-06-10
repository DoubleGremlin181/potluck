"""Benchmark scenario registry types."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

Tier = Literal["smoke", "full"]
TIERS: tuple[str, ...] = get_args(Tier)


@dataclass(frozen=True)
class Scenario:
    """A named, repeatable unit of measured work.

    ``run`` receives a private tmp directory and must be self-contained and
    deterministic in workload size. ``tier`` is the smallest tier that runs
    the scenario; smoke scenarios are included in full runs (smoke ⊆ full).
    """

    name: str
    tier: Tier
    item_count: int
    run: Callable[[Path], None]


def scenarios_for(tier: Tier, scenarios: list[Scenario]) -> list[Scenario]:
    """Select scenarios for a tier (smoke = smoke-only; full = everything)."""
    if tier == "smoke":
        return [s for s in scenarios if s.tier == "smoke"]
    return list(scenarios)
