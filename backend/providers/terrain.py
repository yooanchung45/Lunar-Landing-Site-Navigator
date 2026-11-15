"""Lunar terrain: how high is the ground around a site?

The horizon at the south pole is set by crater rims and ridges, so a real
answer needs elevation data.  ``TerrainProvider`` is the seam where it plugs in.

Implemented:
  * ``FlatTerrain``  -- perfect sphere (placeholder / fallback)
  * ``LolaTerrain``  -- NASA LOLA south polar DEM (GeoTIFF from NASA PGDA)

Other agencies' elevation products (JAXA SELENE/Kaguya, ISRO Chandrayaan-2 ...)
plug in by providing the same two members: ``resolution_m`` and ``sampler_around``.

Heights are metres above the 1737.4 km reference sphere.  Positions are metres
in the south polar stereographic system (origin at the pole, x toward 90 E,
y toward 0 E), the native grid of NASA's polar DEMs.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Protocol

import numpy as np

MOON_RADIUS_M = 1_737_400.0

Sampler = Callable[[np.ndarray, np.ndarray], np.ndarray]


def to_xy(lat_deg, lon_deg):
    """Latitude/longitude (degrees) -> polar stereographic metres from the pole."""
    colat = np.radians(90.0 + np.asarray(lat_deg, dtype=float))
    lon = np.radians(lon_deg)
    r = 2 * MOON_RADIUS_M * np.tan(colat / 2)
    return r * np.sin(lon), r * np.cos(lon)


class TerrainProvider(Protocol):
    name: str
    resolution_m: float

    def sampler_around(self, lat: float, lon: float, radius_m: float) -> Sampler:
        """A function (x_m, y_m arrays) -> height_m, valid within ``radius_m`` of the
        site.  Heights are NaN where the data has no coverage."""


class FlatTerrain:
    """Perfect sphere: no mountains, no craters.  Placeholder baseline."""

    name = "flat (no terrain data)"
    resolution_m = 80.0

    def sampler_around(self, lat, lon, radius_m):
        return lambda x, y: np.zeros(np.broadcast(x, y).shape)


class LolaTerrain:
    """NASA LOLA south polar DEM, read from a local GeoTIFF."""

    def __init__(self, path: Path, name: str = "NASA LOLA elevation", store=None):
        import rasterio

        self.path = Path(path)
        self.store = store            # remote_dem.TileStore for tile-by-tile data, else None
        with rasterio.open(self.path) as ds:
            self.resolution_m = float(ds.res[0])
        self.name = f"{name} ({self.resolution_m:g} m/pixel)"

    def sampler_around(self, lat, lon, radius_m):
        import rasterio
        from rasterio.windows import from_bounds
        from scipy.ndimage import map_coordinates

        x0, y0 = (float(v) for v in to_xy(lat, lon))
        pad = 2 * self.resolution_m
        left, right = x0 - radius_m - pad, x0 + radius_m + pad
        bottom, top = y0 - radius_m - pad, y0 + radius_m + pad
        with rasterio.open(self.path) as ds:
            if self.store is not None:
                self.store.ensure(self.store.tiles_for(left, bottom, right, top, ds.transform.c, ds.transform.f, ds.res[0]))
            win = from_bounds(left, bottom, right, top, transform=ds.transform).round_offsets().round_lengths()
            data = ds.read(1, window=win, boundless=True, masked=True)
            heights = (data.astype("float32") * (ds.scales[0] or 1.0) + (ds.offsets[0] or 0.0)).filled(np.nan)
            origin_x = ds.transform.c + win.col_off * ds.transform.a      # left edge of the window
            origin_y = ds.transform.f + win.row_off * ds.transform.e      # top edge of the window
            res = ds.res[0]

        def sample(x, y):
            col = (np.asarray(x) - origin_x) / res - 0.5
            row = (origin_y - np.asarray(y)) / res - 0.5
            inside = (col >= 0) & (row >= 0) & (col <= heights.shape[1] - 1) & (row <= heights.shape[0] - 1)
            out = map_coordinates(heights, [row, col], order=1, mode="nearest", prefilter=False)
            return np.where(inside, out, np.nan)

        return sample
