"""Terrain horizon: the lowest elevation at which something is visible, per azimuth."""
from __future__ import annotations

import numpy as np

from .providers.terrain import MOON_RADIUS_M, TerrainProvider, to_xy

AZIMUTH_BINS = 360
DEFAULT_OBSERVER_HEIGHT_M = 2.0     # lander antenna / astronaut eye height above the ground
DEFAULT_MAX_RANGE_KM = 150.0


def horizon_profile(
    terrain: TerrainProvider,
    lat: float,
    lon: float,
    observer_height_m: float = DEFAULT_OBSERVER_HEIGHT_M,
    max_range_km: float = DEFAULT_MAX_RANGE_KM,
    n_azimuth: int = AZIMUTH_BINS,
    min_range_km: float = 0.0,
) -> np.ndarray:
    """Horizon elevation (degrees above the local horizontal) for each azimuth
    bin, 0 = north, 90 = east, clockwise.  ``n_azimuth`` bins evenly spaced.

    Method: from the site, walk outward along a great circle in every compass
    direction in steps of one DEM pixel.  At each step look up the ground height,
    work out the angle at which that ground point is seen from the observer on
    the curved Moon, and keep the largest angle -- the terrain that blocks the
    most sky.

    ``min_range_km`` skips the nearest ground, so a fine DEM can handle the near field and a
    coarse one the far field (see ``combined_profile``).

    TODO(joint): decisions worth revisiting together -- the maximum range (the
    farthest mountain that can matter), step size vs DEM resolution (near the
    site small features matter, far away the DEM can be coarser), how to treat
    the edge of the DEM, and the observer height.
    """
    sample = terrain.sampler_around(lat, lon, max_range_km * 1000.0)
    x0, y0 = (float(v) for v in to_xy(lat, lon))
    ground = float(np.nan_to_num(sample(np.array([x0]), np.array([y0]))[0]))
    eye = ground + observer_height_m

    step = terrain.resolution_m
    dist = np.arange(max(step, min_range_km * 1000.0), max_range_km * 1000.0, step)
    theta = dist / MOON_RADIUS_M                                   # central angle travelled
    lat1, lon1 = np.radians(lat), np.radians(lon)
    result = np.empty(n_azimuth)

    chunk = 45                                                     # azimuths per batch keeps memory modest
    for start in range(0, n_azimuth, chunk):
        idx = np.arange(start, min(start + chunk, n_azimuth))
        az = np.radians(idx * 360.0 / n_azimuth)[:, None]
        t = theta[None, :]
        sin_lat2 = np.sin(lat1) * np.cos(t) + np.cos(lat1) * np.sin(t) * np.cos(az)
        lat2 = np.arcsin(np.clip(sin_lat2, -1.0, 1.0))
        lon2 = lon1 + np.arctan2(np.sin(az) * np.sin(t) * np.cos(lat1), np.cos(t) - np.sin(lat1) * sin_lat2)
        x, y = to_xy(np.degrees(lat2), np.degrees(lon2))
        h = sample(x, y)
        # Angle of the ground point above the observer's local horizontal.
        angle = np.arctan2((MOON_RADIUS_M + h) * np.cos(t) - (MOON_RADIUS_M + eye), (MOON_RADIUS_M + h) * np.sin(t))
        with np.errstate(all="ignore"):
            best = np.nanmax(np.where(np.isnan(h), -np.inf, angle), axis=1)
        result[idx] = np.where(np.isfinite(best), np.degrees(best), 0.0)
    return result


def horizon_at(profile: np.ndarray, azimuth_deg: np.ndarray) -> np.ndarray:
    """Look up the horizon elevation for arbitrary azimuths (linear interpolation)."""
    n = len(profile)
    x = np.asarray(azimuth_deg) % 360.0 * n / 360.0
    lo = np.floor(x).astype(int) % n
    hi = (lo + 1) % n
    frac = x - np.floor(x)
    return profile[lo] * (1 - frac) + profile[hi] * frac


NEAR_FIELD_KM = 8.0


def combined_profile(fine: TerrainProvider | None, coarse: TerrainProvider, lat, lon, observer_height_m, max_range_km):
    """Horizon from a fine DEM close to the site and a coarse DEM farther out.

    A 20 m DEM over a 300 km window would need ~1 GB of memory, but nearby ridges and boulders
    are exactly what the fine data is for; distant mountains subtend the same angle either way.
    """
    if fine is None:
        return horizon_profile(coarse, lat, lon, observer_height_m, max_range_km)
    near = horizon_profile(fine, lat, lon, observer_height_m, NEAR_FIELD_KM)
    far = horizon_profile(coarse, lat, lon, observer_height_m, max_range_km, min_range_km=NEAR_FIELD_KM)
    return np.maximum(near, far)
