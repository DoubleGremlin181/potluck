"""Shared CSV reading utility for Reddit GDPR export ingestion."""

import csv
from collections.abc import Iterator
from pathlib import Path


def read_csv(path: Path) -> Iterator[dict[str, str]]:
    """Read a Reddit CSV file using stdlib csv.DictReader.

    We use stdlib csv instead of Polars parse_csv because Reddit's date
    format ('2023-06-15 14:30:00 UTC') confuses Polars' type inference.
    """
    with path.open(encoding="utf-8", newline="") as f:
        yield from csv.DictReader(f)
