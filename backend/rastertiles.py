"""Map tiles cut straight out of NASA's polar rasters.

Layers:
  relief     our own shaded relief, computed from the LOLA elevation grid
  sunlight   average sunlight over the 18.6-year cycle (Mazarico et al. 2011)
  earthvis   average Earth visibility, same study
  shadowcam  Danuri/KPLO ShadowCam mosaics: images of the permanently dark craters

All source rasters share our map's polar stereographic system, so a tile is just
a windowed read -- no reprojection.  Tile (z, col, row) uses the same pyramid as
backend/basemap.py.
"""
from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from rasterio.enums import Resampling
from rasterio.windows import from_bounds
from scipy.ndimage import binary_dilation

from . import datasets
from .basemap import HALF_EXTENT_KM, TILE

SHADOWCAM_FULL_SCALE = 0.4      # reflectance mapped to white: crater floors sit around 0.02-0.2, lit rims far above
SHADOWCAM_GAMMA = 0.55


class MissingData(Exception):
    """A required dataset has not been downloaded yet."""


LAYERS = {
    "relief": {"native": 5, "files": ["dem80"]},
    # vmax: the data value drawn in the brightest colour (NASA's sunlight map tops out near 0.78)
    "sunlight": {"native": 5, "files": ["illum85", "illum75"], "vmax": 0.8},
    "earthvis": {"native": 5, "files": ["earth85", "earth75"], "vmax": 1.0},
    "shadowcam": {"native": 7, "files": []},
}

# value 0..1 -> RGB stops
SUN_RAMP = [(0.0, (14, 22, 70)), (0.02, (60, 40, 130)), (0.25, (170, 60, 140)), (0.55, (250, 130, 60)), (1.0, (255, 240, 150))]
EARTH_RAMP = [(0.0, (14, 22, 70)), (0.25, (30, 80, 160)), (0.6, (50, 170, 210)), (1.0, (220, 255, 255))]


def native_max_zoom(layer: str) -> int:
    return LAYERS[layer]["native"]


def tile_bounds_m(z: int, col: int, row: int):
    """(left, bottom, right, top) of a tile in metres from the pole."""
    n = 2 ** z
    size = 2 * HALF_EXTENT_KM * 1000 / n
    left = -HALF_EXTENT_KM * 1000 + col * size
    top = HALF_EXTENT_KM * 1000 - row * size
    return left, top - size, left + size, top


def read_window(ds, bounds, shape, margin: int = 0) -> np.ma.MaskedArray:
    """Read ``bounds`` resampled to ``shape`` (+ ``margin`` extra pixels on every side)."""
    left, bottom, right, top = bounds
    px, py = (right - left) / shape[1], (top - bottom) / shape[0]
    grown = (left - margin * px, bottom - margin * py, right + margin * px, top + margin * py)
    window = from_bounds(*grown, transform=ds.transform)
    out_shape = (shape[0] + 2 * margin, shape[1] + 2 * margin)
    # Averaging when shrinking avoids aliasing; bilinear when enlarging.
    shrink = window.width / out_shape[1]
    resampling = Resampling.average if shrink > 1.5 else Resampling.bilinear
    data = ds.read(1, window=window, out_shape=out_shape, boundless=True, masked=True, resampling=resampling)
    return data


def _scaled(ds, data: np.ma.MaskedArray) -> np.ma.MaskedArray:
    return data.astype("float64") * (ds.scales[0] or 1.0) + (ds.offsets[0] or 0.0)


def _png(rgba: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, "PNG", optimize=False)
    return buf.getvalue()


def ramp_rgba(values: np.ndarray, valid: np.ndarray, stops, alpha: int = 255) -> np.ndarray:
    xs = [s[0] for s in stops]
    out = np.zeros(values.shape + (4,), dtype=np.uint8)
    for c in range(3):
        out[..., c] = np.interp(np.clip(values, 0, 1), xs, [s[1][c] for s in stops]).astype(np.uint8)
    out[..., 3] = np.where(valid, alpha, 0)
    return out


def hillshade(z: np.ndarray, cell_m: float, light_az_deg: float, light_el_deg: float) -> np.ndarray:
    """Shaded relief 0..1 for a margin-padded elevation grid (rows run top -> bottom).

    ``light_az_deg`` is the direction the light comes FROM, clockwise from the top
    of the map (0 = lon 0 side).  Flat ground renders mid-grey, slopes facing the
    light brighter, slopes facing away darker.  This is local shading only: it does
    not cast shadows of distant mountains (the horizon work does that later).
    """
    a = z[:-2, :-2]; b = z[:-2, 1:-1]; c = z[:-2, 2:]
    d = z[1:-1, :-2];                  f = z[1:-1, 2:]
    g = z[2:, :-2];  h = z[2:, 1:-1];  i = z[2:, 2:]
    dzdx = ((c + 2 * f + i) - (a + 2 * d + g)) / (8 * cell_m)       # east (right) positive
    dzdy = ((a + 2 * b + c) - (g + 2 * h + i)) / (8 * cell_m)       # up the map positive
    norm = np.sqrt(dzdx ** 2 + dzdy ** 2 + 1.0)
    az, el = np.radians(light_az_deg), np.radians(light_el_deg)
    towards = np.array([np.sin(az) * np.cos(el), np.cos(az) * np.cos(el), np.sin(el)])
    shade = (-dzdx * towards[0] - dzdy * towards[1] + towards[2]) / norm
    flat = np.sin(el)
    return np.where(shade >= flat, 0.5 + 0.5 * (shade - flat) / (1 - flat + 1e-9), 0.5 * np.clip(shade, 0, None) / (flat + 1e-9))


def _require(names: list[str]) -> list[Path]:
    missing = [n for n in names if not datasets.available(n)]
    if missing:
        raise MissingData(f"dataset(s) not downloaded yet: {', '.join(missing)}")
    return [datasets.path_of(n) for n in names]


def render_relief(z: int, col: int, row: int, az: float, el: float) -> bytes:
    (path,) = _require(["dem80"])
    bounds = tile_bounds_m(z, col, row)
    cell = (bounds[2] - bounds[0]) / TILE
    with rasterio.open(path) as ds:
        raw = read_window(ds, bounds, (TILE, TILE), margin=1)
        elev = _scaled(ds, raw)
    bad = np.ma.getmaskarray(elev)
    shade = hillshade(elev.filled(0.0), cell, az, el)
    invalid = binary_dilation(bad, iterations=1)[1:-1, 1:-1]
    gray = (np.clip(shade, 0, 1) * 255).astype(np.uint8)
    rgba = np.dstack([gray, gray, gray, np.where(invalid, 0, 255).astype(np.uint8)])
    return _png(rgba)


def render_ramp(layer: str, z: int, col: int, row: int, stops) -> bytes:
    files = LAYERS[layer]["files"]            # finest first, coarsest (full coverage) last
    _require(files[-1:])
    paths = [datasets.path_of(n) for n in files if datasets.available(n)]
    bounds = tile_bounds_m(z, col, row)
    values = np.zeros((TILE, TILE)); valid = np.zeros((TILE, TILE), dtype=bool)
    for path in paths:                       # finest data first; coarser data fills what's left
        if valid.all():
            break
        with rasterio.open(path) as ds:
            data = _scaled(ds, read_window(ds, bounds, (TILE, TILE)))
        ok = ~np.ma.getmaskarray(data) & ~valid
        values[ok] = data.filled(0.0)[ok]
        valid |= ok
    return _png(ramp_rgba(values / LAYERS[layer]["vmax"], valid, stops, alpha=235))


def render_shadowcam(z: int, col: int, row: int) -> bytes:
    bounds = tile_bounds_m(z, col, row)
    left, bottom, right, top = bounds
    canvas = np.zeros((TILE, TILE, 4), dtype=np.uint8)
    for m in datasets.shadowcam_mosaics():
        with rasterio.open(m["path"]) as ds:
            b = ds.bounds
            if b.right < left or b.left > right or b.top < bottom or b.bottom > top:
                continue
            data = read_window(ds, bounds, (TILE, TILE))
        ok = ~np.ma.getmaskarray(data)
        if not ok.any():
            continue
        v = np.clip(data.astype('float64').filled(0.0) / SHADOWCAM_FULL_SCALE, 0, 1) ** SHADOWCAM_GAMMA
        gray = (v * 255).astype(np.uint8)
        canvas[ok] = np.dstack([gray, gray, gray, np.full_like(gray, 255)])[ok]
    return _png(canvas)


def render(layer: str, z: int, col: int, row: int, az: float = 315.0, el: float = 12.0) -> bytes:
    if layer == "relief":
        return render_relief(z, col, row, az, el)
    if layer == "sunlight":
        return render_ramp(layer, z, col, row, SUN_RAMP)
    if layer == "earthvis":
        return render_ramp(layer, z, col, row, EARTH_RAMP)
    if layer == "shadowcam":
        return render_shadowcam(z, col, row)
    raise KeyError(layer)
