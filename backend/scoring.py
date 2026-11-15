"""Turn a site's time series (and terrain) into numbers a mission planner can compare.

Score (0-100) = weighted average of four 0-1 sub-scores:

  power        how much of a typical stay has the Sun up, counting a partly-visible Sun as partly
               useful (mean visible Sun-disc fraction).  Replaces "mean solar elevation": at the pole
               the Sun only ever sits 0-1.6 deg above the horizon, so elevation barely differs
               between sites while "is it up at all" differs hugely.
  comms        share of a typical stay with Sun AND an Earth link together (Earth above the skyline
               and a Deep Space Network station able to see the Moon).
  reliability  how often a landing date works: the share of possible start dates for which the Sun
               is up for at least 90% of the whole stay.  Half of all dates working earns full marks.
  terrain      flat ground is safe: 1 at <=5 deg slope, falling linearly to 0 at 15 deg.

"Typical stay" = every possible start time of a stay of ``stay_days`` (default 6.5, Artemis III) in
the analysis period (see mission.py).  If the period is shorter than the stay, the whole-period
averages are used and reliability falls back to a no-Sun-spell penalty.

Sites steeper than MAX_SAFE_SLOPE_DEG (90th percentile within 300 m) are flagged unsafe and never
recommended, whatever their score.  Hard pass/fail requirements live in mission.py.

The reference values and weights below are judgement calls, not physics -- they are the knobs to
argue about, and the leaderboard lets you move the weights live.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np

from .windows import find_windows

WEIGHTS = {"power": 0.30, "comms": 0.30, "reliability": 0.20, "terrain": 0.20}

POWER_REF = 0.80            # best sunlit fraction anywhere near the pole (NASA map max ~0.78)
COMMS_REF = 0.50            # a site with Sun + Earth link half the time is "full marks"
RELIABLE_REF = 0.50         # half of all landing dates fully lit is "full marks"
DARK_TAU_H = 168.0          # fallback when the period is shorter than the stay
SLOPE_GOOD_DEG, SLOPE_BAD_DEG = 5.0, 15.0
MAX_SAFE_SLOPE_DEG = 15.0


@dataclass
class SiteScore:
    total: float                    # 0-100 with the default weights
    subscores: dict                 # name -> 0..1
    both_fraction: float            # fraction of time with Sun AND Earth link usable
    sun_fraction: float
    earth_fraction: float           # Earth link (skyline + ground station)
    sun_visible_mean: float         # mean fraction of the Sun's disc above the skyline
    longest_window_h: float         # longest continuous Sun+Earth window
    longest_blackout_h: float       # longest stretch WITHOUT Sun+Earth
    longest_dark_h: float           # longest stretch with no Sun at all
    window_count: int
    slope_p90_deg: float | None = None
    safe: bool = True
    weights: dict = field(default_factory=lambda: dict(WEIGHTS))

    def to_dict(self):
        return asdict(self)


def combine(subscores: dict, weights: dict) -> float:
    total_w = sum(weights.values()) or 1.0
    return float(100.0 * sum(weights[k] * subscores[k] for k in weights) / total_w)


def terrain_subscore(slope_p90_deg: float | None) -> float:
    if slope_p90_deg is None:
        return 0.5                                  # unknown terrain: neither reward nor fail
    t = (slope_p90_deg - SLOPE_GOOD_DEG) / (SLOPE_BAD_DEG - SLOPE_GOOD_DEG)
    return float(1.0 - np.clip(t, 0.0, 1.0))


def score_site(
    sun_ok: np.ndarray,
    link_ok: np.ndarray,
    step_s: float,
    sun_frac: np.ndarray | None = None,
    slope_p90_deg: float | None = None,
    mission: dict | None = None,
) -> SiteScore:
    both = sun_ok & link_ok
    n = max(len(both), 1)
    hours = step_s / 3600.0
    windows = find_windows(both, step_s)
    blackouts = find_windows(~both, step_s)
    darks = find_windows(~sun_ok, step_s)
    longest = lambda spans: float(max((e - s for s, e in spans), default=0) * hours)

    sun_share = float(sun_ok.sum() / n)
    sun_visible = float(np.mean(sun_frac)) if sun_frac is not None else sun_share
    partial = sun_visible / sun_share if sun_share > 0 else 0.0      # how much of the disc, while the Sun is up
    longest_dark = longest(darks)
    if mission is not None:
        subs = {
            "power": float(min(mission["mean_sun_cover"] * partial / POWER_REF, 1.0)),
            "comms": float(min(mission["mean_both_cover"] / COMMS_REF, 1.0)),
            "reliability": float(min(mission["fully_lit_share"] / RELIABLE_REF, 1.0)),
            "terrain": terrain_subscore(slope_p90_deg),
        }
    else:
        subs = {
            "power": float(min(sun_visible / POWER_REF, 1.0)),
            "comms": float(min(both.sum() / n / COMMS_REF, 1.0)),
            "reliability": float(np.exp(-longest_dark / DARK_TAU_H)),
            "terrain": terrain_subscore(slope_p90_deg),
        }
    return SiteScore(
        total=combine(subs, WEIGHTS),
        subscores=subs,
        both_fraction=float(both.sum() / n),
        sun_fraction=sun_share,
        earth_fraction=float(link_ok.sum() / n),
        sun_visible_mean=sun_visible,
        longest_window_h=longest(windows),
        longest_blackout_h=longest(blackouts),
        longest_dark_h=longest_dark,
        window_count=len(windows),
        slope_p90_deg=slope_p90_deg,
        safe=slope_p90_deg is None or slope_p90_deg <= MAX_SAFE_SLOPE_DEG,
    )
