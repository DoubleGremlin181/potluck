"""Bench result DTOs (the JSON schema of `potluck bench run --json`)."""

from pydantic import BaseModel

from potluck.bench.registry import Tier


class ScenarioResult(BaseModel):
    name: str
    reps: int
    median_s: float
    p95_s: float
    throughput_items_s: float
    peak_rss_kb: int


class BenchReport(BaseModel):
    tier: Tier
    fingerprint: dict[str, str]
    results: list[ScenarioResult]
