"""Mission-window analysis: what would a stay of N days starting at each possible landing time look like?

A flat average over the whole period hides what mission planners care about: a 6.5-day Artemis III
surface stay is a *window*, and whether it works depends on when it starts.  For every possible
start time (each time step) we measure how much of the stay has the Sun up and how much has both
Sun and an Earth link, then summarise across all start times, and list the best landing-date ranges.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np

DEFAULT_STAY_DAYS = 6.5            # Artemis III surface stay (NASA)
FULLY_LIT_COVER = 0.90             # "the Sun is available" = up for at least this share of the stay


def _cover(mask: np.ndarray, w: int) -> np.ndarray:
    """Share of each length-``w`` window that is True; one value per possible start."""
    cs = np.concatenate(([0.0], np.cumsum(np.asarray(mask, dtype=float))))
    return (cs[w:] - cs[:-w]) / w


RANGE_TOLERANCE = 0.02             # a start is "as good as the best" within this much coverage


def best_ranges(score: np.ndarray, w: int, count: int, tolerance: float = RANGE_TOLERANCE) -> list[tuple[int, int, int]]:
    """The best landing-date *ranges*: (first start, last start, peak start) index triples.

    A long good window allows many start times (an 11-day window holds a 6.5-day stay starting
    anywhere in its first 4.5 days), so listing single best dates picks the same window over and
    over.  Instead: take the best start, grow it into the run of neighbouring starts whose coverage
    is within ``tolerance`` of it, then look for the next best start at least one stay length away
    from every range already chosen (the coverage ramp beside a window is not a separate window).
    Ranges with no coverage at all are never reported.
    """
    remaining = np.asarray(score, dtype=float).copy()
    out: list[tuple[int, int, int]] = []
    while len(out) < count and len(remaining):
        peak_i = int(np.argmax(remaining))
        peak = float(remaining[peak_i])
        if not np.isfinite(peak) or peak <= 0:
            break
        a = b = peak_i
        while a > 0 and score[a - 1] >= peak - tolerance:
            a -= 1
        while b < len(score) - 1 and score[b + 1] >= peak - tolerance:
            b += 1
        out.append((a, b, peak_i))
        remaining[max(a - w, 0): b + w + 1] = -np.inf
    return out


def mission_stats(sun_ok, link_ok, step_s: float, stay_days: float, t0: datetime, top: int = 3,
                  requirements: dict | None = None):
    """Summary over every possible start of a ``stay_days`` stay, or None if the period is shorter.

    ``link_ok`` = Earth link usable (Earth above the skyline and, when modelled, a ground station
    in view).  Coverage values are fractions (0-1) of the stay.
    """
    w = max(int(round(stay_days * 86400.0 / step_s)), 1)
    if len(sun_ok) <= w:
        return None
    sun_ok, link_ok = np.asarray(sun_ok, dtype=bool), np.asarray(link_ok, dtype=bool)
    sun_cov = _cover(sun_ok, w)
    link_cov = _cover(link_ok, w)
    both_cov = _cover(sun_ok & link_ok, w)

    iso = lambda i: (t0 + timedelta(seconds=int(i) * step_s)).isoformat()
    out = {
        "stay_days": stay_days,
        "starts": int(len(sun_cov)),
        "mean_sun_cover": float(sun_cov.mean()),
        "mean_both_cover": float(both_cov.mean()),
        "p10_both_cover": float(np.percentile(both_cov, 10)),     # a bad-luck landing date
        "fully_lit_share": float((sun_cov >= FULLY_LIT_COVER).mean()),
        "continuous_sun_share": float((sun_cov >= 0.999).mean()),
        "best": [
            {
                "start": iso(a), "latest_start": iso(b), "slack_days": (b - a) * step_s / 86400.0,
                "peak_start": iso(i), "sun_cover": float(sun_cov[i]), "link_cover": float(link_cov[i]),
                "both_cover": float(both_cov[i]),
                "stay_ends": iso(b + w),                 # if you land at the latest start in the range
                "at_period_start": a == 0,               # the range may really begin earlier than the period
                "at_period_end": b == len(both_cov) - 1,
            }
            for a, b, i in best_ranges(both_cov + 1e-3 * sun_cov, w, top)
        ],
    }
    if requirements:
        ok = (sun_cov >= requirements["sun_cover_min"]) & (link_cov >= requirements["earth_cover_min"])
        out["requirement_share"] = float(ok.mean())
    return out


# ---- pass/fail requirements --------------------------------------------------
DEFAULT_REQUIREMENTS = {
    "slope_max_deg": 8.0,          # steepest 10% of the ground within 300 m (HLS spec is 5 deg at the landing spot)
    "sun_cover_min": 0.90,         # share of the stay with the Sun up ...
    "earth_cover_min": 0.50,       # ... and with an Earth link ...
    "dates_min": 0.10,             # ... on at least this share of possible landing dates
}


def check_requirements(requirements: dict, slope_p90_deg, mission) -> list[str]:
    """Human-readable reasons a site fails the requirements; empty list = pass."""
    fails = []
    r = requirements
    if r.get("slope_max_deg") is not None and slope_p90_deg is not None and slope_p90_deg > r["slope_max_deg"]:
        fails.append(f"slope {slope_p90_deg:.1f}° is steeper than {r['slope_max_deg']:g}°")
    if mission is None:
        fails.append("period is shorter than the stay, so landing dates cannot be assessed")
    else:
        share = mission["requirement_share"]
        if share < r["dates_min"]:
            fails.append(
                f"only {share * 100:.0f}% of landing dates give Sun for {r['sun_cover_min'] * 100:.0f}% and an Earth link "
                f"for {r['earth_cover_min'] * 100:.0f}% of the {mission['stay_days']:g}-day stay (need {r['dates_min'] * 100:.0f}%)")
    return fails
