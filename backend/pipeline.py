"""Ties the data providers and the algorithms together for one site."""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np

from .geometry import EARTH_RADIUS_KM, SUN_RADIUS_KM, sky_disc, visible_fraction
from .horizon import horizon_at, horizon_profile
from .providers.ephemeris import DSN_MIN_ELEVATION_DEG, EphemerisProvider
from .providers.terrain import TerrainProvider
from .mission import DEFAULT_STAY_DAYS, check_requirements, mission_stats
from .scoring import score_site
from .slope import slope_stats
from .windows import find_windows


def evaluate_site(
    ephemeris: EphemerisProvider,
    terrain: TerrainProvider,
    lat: float,
    lon: float,
    start: datetime,
    days: float,
    step_min: float,
    sun_min_deg: float,
    earth_min_deg: float,
    observer_height_m: float = 2.0,
    max_range_km: float = 150.0,
    profile: np.ndarray | None = None,
    slope: dict | None = None,
    vectors: tuple | None = None,
    station_up: np.ndarray | None = None,
    use_dsn: bool = True,
    stay_days: float = DEFAULT_STAY_DAYS,
    requirements: dict | None = None,
) -> dict:
    step_s = step_min * 60.0
    n = int(days * 86400 / step_s) + 1
    sun_vec, earth_vec = vectors if vectors is not None else ephemeris.vectors(start, step_s, n)

    # Sun and Earth are discs (about 0.53 and 1.9 deg wide), seen from the site itself
    # (Earth parallax).  The terrain horizon plus the margin thresholds set the bar at
    # each azimuth; a body counts as usable once any part of its disc clears it, and
    # *_frac says how much of it does.
    sun_el, sun_az, sun_r = sky_disc(sun_vec, lat, lon, SUN_RADIUS_KM)
    earth_el, earth_az, earth_r = sky_disc(earth_vec, lat, lon, EARTH_RADIUS_KM)

    if profile is None:
        profile = horizon_profile(terrain, lat, lon, observer_height_m, max_range_km)
    if slope is None:
        slope = slope_stats(terrain, lat, lon)
    sun_frac = visible_fraction(sun_el, sun_r, horizon_at(profile, sun_az) + sun_min_deg)
    earth_frac = visible_fraction(earth_el, earth_r, horizon_at(profile, earth_az) + earth_min_deg)
    sun_ok = sun_frac > 0
    earth_ok = earth_frac > 0
    # Earth above the skyline is not enough to talk: a Deep Space Network station must also see the Moon.
    if use_dsn:
        if station_up is None:
            station_up = ephemeris.station_in_view(start, step_s, n, DSN_MIN_ELEVATION_DEG)
    else:
        station_up = np.ones(n, dtype=bool)
    link_ok = earth_ok & station_up

    both = sun_ok & link_ok
    windows = [
        {
            "start": (start + timedelta(seconds=s * step_s)).isoformat(),
            "end": (start + timedelta(seconds=e * step_s)).isoformat(),
            "hours": (e - s) * step_s / 3600.0,
        }
        for s, e in find_windows(both, step_s)
    ]
    mission = mission_stats(sun_ok, link_ok, step_s, stay_days, start, requirements=requirements)
    score = score_site(sun_ok, link_ok, step_s, sun_frac, slope["p90"], mission)
    failures = check_requirements(requirements, slope["p90"], mission) if requirements is not None else []
    r = lambda a: np.round(a, 3).tolist()
    return {
        "site": {"lat": lat, "lon": lon},
        "terrain": terrain.name,
        "step_s": step_s,
        "t0": start.isoformat(),
        "horizon": np.round(profile, 2).tolist(),
        "sun_el": r(sun_el),
        "sun_az": r(sun_az),
        "earth_el": r(earth_el),
        "earth_az": r(earth_az),
        "sun_frac": r(sun_frac),
        "earth_frac": r(earth_frac),
        "sun_ok": sun_ok.astype(int).tolist(),
        "earth_ok": earth_ok.astype(int).tolist(),
        "dsn_ok": station_up.astype(int).tolist(),
        "both_ok": both.astype(int).tolist(),
        "windows": windows,
        "slope": slope,
        "score": score.to_dict(),
        "mission": mission,
        "requirements": None if requirements is None else {"failures": failures, "pass": not failures},
    }
