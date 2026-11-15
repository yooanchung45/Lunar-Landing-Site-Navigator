"""Find candidate landing spots from the real data instead of guessing coordinates.

Walks NASA's south-pole maps (average sunlight, average Earth visibility, LOLA elevation) on a
coarse grid, keeps flat spots that are both sunlit and Earth-visible on average, and picks the
best ones at least ``min_separation_km`` apart.  These are *screening* candidates: the leaderboard
then evaluates each with the full time-resolved model.  Cached in backend/data/candidates.json.
"""
from __future__ import annotations

import json
import math

import numpy as np
import rasterio
from rasterio.enums import Resampling

from . import datasets
from .providers.terrain import MOON_RADIUS_M

CELL_M = 480.0                 # screening grid
REGION_M = 150_000.0           # the 85-90 S maps cover +-151.7 km; we use the inscribed circle (lat <= -85)
MAX_SCREEN_SLOPE_DEG = 10.0
MIN_SUN = 0.15
MIN_EARTH = 0.15


def to_latlon(x: float, y: float) -> tuple[float, float]:
    """Polar stereographic metres -> (lat, lon) degrees (inverse of terrain.to_xy)."""
    r = math.hypot(x, y)
    colat = 2 * math.atan(r / (2 * MOON_RADIUS_M))
    return -90.0 + math.degrees(colat), math.degrees(math.atan2(x, y))


def _grid(name: str, n: int, resampling=Resampling.average) -> np.ndarray:
    """Dataset resampled to an n x n grid over +-REGION_M, NaN where there is no data."""
    with rasterio.open(datasets.path_of(name)) as ds:
        win = rasterio.windows.from_bounds(-REGION_M, -REGION_M, REGION_M, REGION_M, transform=ds.transform)
        data = ds.read(1, window=win, out_shape=(n, n), resampling=resampling, masked=True, boundless=True)
        return (data.astype("float64") * (ds.scales[0] or 1.0) + (ds.offsets[0] or 0.0)).filled(np.nan)


def refine(x: float, y: float, half_m: float = CELL_M) -> tuple[float, float, dict]:
    """Move a coarse pick to the best single 60 m pixel within ``half_m`` of it.

    The screening grid is 480 m wide and sunlight changes a lot within that, so the coarse cell centre
    is not itself a good spot.  Here we read NASA's maps at their native 60 m resolution (85S products),
    keep pixels that are flat enough, score each by the same sun^2 x Earth proxy averaged over its
    3 x 3 neighbourhood (so one noisy pixel cannot win), and return the winner's coordinates.
    """
    from rasterio.windows import from_bounds
    from scipy.ndimage import uniform_filter

    with rasterio.open(datasets.path_of("illum85")) as su, rasterio.open(datasets.path_of("earth85")) as ea,             rasterio.open(datasets.path_of("dem80")) as dm:
        win = from_bounds(x - half_m, y - half_m, x + half_m, y + half_m, transform=su.transform).round_offsets().round_lengths()
        shape = (int(win.height), int(win.width))
        read = lambda ds, **kw: (ds.read(1, window=win, boundless=True, masked=True, **kw).astype("float64") * (ds.scales[0] or 1.0) + (ds.offsets[0] or 0.0))
        sun, earth = read(su).filled(np.nan), read(ea).filled(np.nan)
        # elevation on the same 60 m grid
        dwin = from_bounds(*rasterio.windows.bounds(win, su.transform), transform=dm.transform)
        dem = dm.read(1, window=dwin, out_shape=shape, boundless=True, masked=True, resampling=Resampling.bilinear).astype("float64").filled(np.nan)
        origin = su.window_transform(win)
    dz_dy, dz_dx = np.gradient(dem, su.res[0])
    slope = np.degrees(np.arctan(np.hypot(dz_dx, dz_dy)))
    ok = np.isfinite(sun) & np.isfinite(earth) & np.isfinite(slope) & (slope <= MAX_SCREEN_SLOPE_DEG) & (sun >= MIN_SUN) & (earth >= MIN_EARTH)
    proxy = np.where(ok, np.nan_to_num(sun) ** 2 * np.nan_to_num(earth), 0.0)
    smooth = uniform_filter(proxy, size=3, mode="constant")
    smooth[~ok] = -1.0                                           # the winner must itself be a usable pixel
    if not ok.any() or smooth.max() <= 0:
        return x, y, {}
    r, c = np.unravel_index(int(np.argmax(smooth)), smooth.shape)
    nx, ny = origin.c + (c + 0.5) * origin.a, origin.f + (r + 0.5) * origin.e
    return nx, ny, {"sun": float(sun[r, c]), "earth": float(earth[r, c]), "slope": float(slope[r, c])}


def pick_peaks(score: np.ndarray, count: int, min_separation_cells: float) -> list[tuple[int, int]]:
    """Greedy non-maximum suppression: best cell first, then the best cell not near an earlier pick."""
    order = np.argsort(np.where(np.isfinite(score), score, -np.inf), axis=None)[::-1]
    rows, cols = np.unravel_index(order, score.shape)
    picked: list[tuple[int, int]] = []
    for r, c in zip(rows, cols):
        if not np.isfinite(score[r, c]) or score[r, c] <= 0:
            break
        if all(math.hypot(r - pr, c - pc) >= min_separation_cells for pr, pc in picked):
            picked.append((int(r), int(c)))
            if len(picked) >= count:
                break
    return picked


def find_candidates(count: int = 24, min_separation_km: float = 6.0) -> list[dict]:
    n = int(2 * REGION_M / CELL_M)
    sun, earth = _grid("illum85", n), _grid("earth85", n)
    dem = _grid("dem80", n)
    # tilt over each coarse cell's neighbours (480 m cells, so only hills, not boulders)
    dz_dy, dz_dx = np.gradient(dem, CELL_M)
    slope = np.degrees(np.arctan(np.hypot(dz_dx, dz_dy)))
    yy, xx = np.mgrid[0:n, 0:n]
    in_circle = np.hypot(xx - (n - 1) / 2, yy - (n - 1) / 2) * CELL_M <= REGION_M
    ok = in_circle & (slope <= MAX_SCREEN_SLOPE_DEG) & (sun >= MIN_SUN) & (earth >= MIN_EARTH)
    # Sunlight is the scarce resource at the pole (Earth is visible from most places), so weight it double.
    proxy = np.where(ok, sun ** 2 * earth, np.nan)
    out = []
    for r, c in pick_peaks(proxy, count, min_separation_km * 1000.0 / CELL_M):
        x = -REGION_M + (c + 0.5) * CELL_M
        y = REGION_M - (r + 0.5) * CELL_M
        x, y, px = refine(x, y)
        lat, lon = to_latlon(x, y)
        out.append({"lat": round(lat, 4), "lon": round(lon, 4),
                    "screen_sun": round(px.get("sun", float(sun[r, c])), 3), "screen_earth": round(px.get("earth", float(earth[r, c])), 3),
                    "screen_slope": round(px.get("slope", float(slope[r, c])), 1)})
    return out


def nearest_feature(lat: float, lon: float, features: list[dict]) -> tuple[str, float]:
    """Name and distance (km) of the closest known crater centre, for human-friendly labels."""
    from .providers.terrain import to_xy
    x, y = (float(v) for v in to_xy(lat, lon))
    best = min(features, key=lambda f: math.hypot(*(float(a) - b for a, b in zip(to_xy(f["lat"], f["lon"]), (x, y)))))
    bx, by = (float(v) for v in to_xy(best["lat"], best["lon"]))
    return best["name"], math.hypot(bx - x, by - y) / 1000.0


def load_candidates(sites: list[dict], cache_path) -> list[dict]:
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))
    found = find_candidates()
    for i, cand in enumerate(found, 1):
        name, km = nearest_feature(cand["lat"], cand["lon"], sites)
        cand["name"] = f"Site {i}"
        cand["near"] = f"{km:.0f} km from {name}"
    cache_path.write_text(json.dumps(found, indent=1), encoding="utf-8")
    return found
