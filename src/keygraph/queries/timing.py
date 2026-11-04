"""Timing statistics for the query log: median, p95 (linear interpolation) and the recent trend."""

from __future__ import annotations

import math
import statistics

TREND_RECENT = 5
TREND_MIN_RUNS = 6


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    v = sorted(values)
    i = (len(v) - 1) * p
    lo, hi = math.floor(i), math.ceil(i)
    return round(v[lo] + (v[hi] - v[lo]) * (i - lo), 3)


def timing(ms_in_order: list[float]) -> dict:
    """ms_in_order: durations oldest first (runs without a duration left out)."""
    out = {"timed_runs": len(ms_in_order), "median_ms": None, "p95_ms": None, "trend": None}
    if not ms_in_order:
        return out
    out["median_ms"] = round(statistics.median(ms_in_order), 3)
    out["p95_ms"] = percentile(ms_in_order, 0.95)
    if len(ms_in_order) >= TREND_MIN_RUNS:
        recent, earlier = ms_in_order[-TREND_RECENT:], ms_in_order[:-TREND_RECENT]
        r, e = statistics.median(recent), statistics.median(earlier)
        out["trend"] = {"recent_median_ms": round(r, 3), "earlier_median_ms": round(e, 3),
                        "change_pct": round(100 * (r - e) / e, 1) if e else None}
    return out
