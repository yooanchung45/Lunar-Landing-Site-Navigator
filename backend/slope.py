"""How steep is the ground around a site?  (Lander safety.)"""
from __future__ import annotations

import numpy as np

from .providers.terrain import TerrainProvider, to_xy


def slope_stats(terrain: TerrainProvider, lat: float, lon: float, radius_m: float = 300.0) -> dict:
    """Slope (degrees) over a disc of ``radius_m`` around the site: mean, 90th percentile and max.

    Slope at each DEM cell is the steepest tilt of the plane through its four neighbours
    (central differences over two pixels).  With an 80 m DEM that means features smaller
    than ~160 m are invisible, so this finds big hills, not boulders or small craters --
    a screening number, not a landing-hazard map.
    """
    res = terrain.resolution_m
    sample = terrain.sampler_around(lat, lon, radius_m + 3 * res)
    x0, y0 = (float(v) for v in to_xy(lat, lon))
    offsets = np.arange(-np.floor(radius_m / res), np.floor(radius_m / res) + 1) * res
    dx, dy = np.meshgrid(offsets, offsets)
    inside = np.hypot(dx, dy) <= radius_m + 1e-9
    x, y = x0 + dx, y0 + dy
    dzdx = (sample(x + res, y) - sample(x - res, y)) / (2 * res)
    dzdy = (sample(x, y + res) - sample(x, y - res)) / (2 * res)
    slope = np.degrees(np.arctan(np.hypot(dzdx, dzdy)))[inside]
    slope = slope[np.isfinite(slope)]
    if slope.size == 0:
        return {"mean": None, "p90": None, "max": None}
    return {"mean": float(slope.mean()), "p90": float(np.percentile(slope, 90)), "max": float(slope.max())}
