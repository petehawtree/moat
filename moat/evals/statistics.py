"""Dependency-free descriptive and bootstrap statistics for v1."""

from __future__ import annotations

import math
import random
from collections import defaultdict


def _average_ranks(values: list[float]) -> list[float]:
    ordered = sorted(range(len(values)), key=values.__getitem__)
    ranks = [0.0] * len(values)
    start = 0
    while start < len(ordered):
        end = start + 1
        while end < len(ordered) and values[ordered[end]] == values[ordered[start]]:
            end += 1
        rank = (start + 1 + end) / 2.0
        for index in ordered[start:end]:
            ranks[index] = rank
        start = end
    return ranks


def spearman_rho(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    rx, ry = _average_ranks(xs), _average_ranks(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    numerator = sum((x - mx) * (y - my) for x, y in zip(rx, ry))
    dx = math.sqrt(sum((x - mx) ** 2 for x in rx))
    dy = math.sqrt(sum((y - my) ** 2 for y in ry))
    return numerator / (dx * dy) if dx and dy else None


def percentile(values: list[float], probability: float) -> float:
    if not values:
        raise ValueError("percentile requires values")
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return ordered[low]
    weight = position - low
    return ordered[low] * (1 - weight) + ordered[high] * weight


def bootstrap_interval(
    rows: list[dict], metric, *, seed: int, replicates: int,
    sector_key: str = "sector",
) -> dict | None:
    """Sector-stratified company bootstrap, with v1's small-sector fallback."""
    if len(rows) < 20:
        return None
    by_sector: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_sector[row.get(sector_key) or "Unknown"].append(row)
    large = {key: value for key, value in by_sector.items() if len(value) >= 5}
    small = [row for key, values in by_sector.items() if len(values) < 5 for row in values]
    if small and len(small) >= 5:
        large["Other-small-sectors"] = small
        strata, method = list(large.values()), "sector_stratified_with_pooled_small_sectors"
    elif small:
        strata, method = [rows], "unstratified_small_sector_fallback"
    else:
        strata, method = list(large.values()), "sector_stratified"

    rng = random.Random(seed)
    samples: list[float] = []
    for _ in range(replicates):
        sampled = [rng.choice(stratum) for stratum in strata for _ in range(len(stratum))]
        value = metric(sampled)
        if value is not None and math.isfinite(value):
            samples.append(value)
    if not samples:
        return None
    return {
        "lower_2_5": percentile(samples, 0.025),
        "upper_97_5": percentile(samples, 0.975),
        "replicates": len(samples),
        "seed": seed,
        "method": method,
        "orientation": "generalisation_to_comparable_companies_or_future_universes",
    }
