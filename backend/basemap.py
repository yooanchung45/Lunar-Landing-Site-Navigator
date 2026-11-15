"""South-polar basemap tiles, built on demand from NASA Moon Trek imagery.

Moon Trek serves equirectangular (lat/lon) tiles, which are hugely stretched
near the pole.  We reproject them into a polar stereographic view centred on
the south pole -- the projection NASA's polar datasets use -- and cache the
result, so a tile is only downloaded and computed the first time it is looked at.

Projection (kilometres from the pole; the frontend uses the same formulas):
    r = 2 R tan(colatitude / 2),  x = r sin(lon),  y = r cos(lon)
Tile pyramid: zoom z has 2^z x 2^z tiles of 256 px covering a square of
2 * HALF_EXTENT_KM on a side (row 0 is the top / lon 0 side).
"""
from __future__ import annotations

import io
import math
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path

import httpx
import numpy as np
from PIL import Image

MOON_RADIUS_KM = 1737.4
HALF_EXTENT_KM = 320.0          # map covers roughly 80 deg S to the pole (plus corners)
TILE = 256
SUPERSAMPLE = 2                 # 2x2 samples per output pixel to tame aliasing near the pole
TREK = "https://trek.nasa.gov/tiles/Moon/EQ"

# max_src: deepest source zoom we are willing to download (each zoom = 4x the tiles).
LAYERS = {
    "imagery": {"path": "LRO_WAC_Mosaic_Global_303ppd_v02", "ext": "jpg", "max_src": 7},
    "elevation": {"path": "LRO_LOLA_ClrShade_Global_128ppd_v04", "ext": "png", "max_src": 5},
}


def native_max_zoom(layer: str) -> int:
    """Output zoom beyond which we'd only be magnifying the source pixels."""
    return LAYERS[layer]["max_src"] - 3


class Basemap:
    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self._client = httpx.Client(timeout=30, headers={"User-Agent": "lunar-navigator/0.1"})
        self._pool = ThreadPoolExecutor(max_workers=16)
        self._decode = lru_cache(maxsize=700)(self._decode_uncached)
        self._lock = threading.Lock()

    # ---- source tiles -------------------------------------------------
    def _source_path(self, layer: str, s: int, row: int, col: int) -> Path:
        return self.cache_dir / "src" / layer / str(s) / str(row) / f"{col}.{LAYERS[layer]['ext']}"

    def _fetch(self, layer: str, s: int, row: int, col: int) -> bool:
        path = self._source_path(layer, s, row, col)
        if path.exists():
            return True
        url = f"{TREK}/{LAYERS[layer]['path']}/1.0.0/default/default028mm/{s}/{row}/{col}.{LAYERS[layer]['ext']}"
        try:
            res = self._client.get(url)
            if res.status_code != 200:
                return False
        except httpx.HTTPError:
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + f".{threading.get_ident()}.tmp")
        tmp.write_bytes(res.content)
        os.replace(tmp, path)
        return True

    def _decode_uncached(self, layer: str, s: int, row: int, col: int):
        path = self._source_path(layer, s, row, col)
        with Image.open(path) as img:
            return np.asarray(img.convert("RGB"))

    # ---- output tiles -------------------------------------------------
    def tile(self, layer: str, z: int, col: int, row: int) -> bytes:
        out_path = self.cache_dir / "out" / layer / str(z) / str(col) / f"{row}.jpg"
        if out_path.exists():
            return out_path.read_bytes()
        data, complete = self._render(layer, z, col, row)
        if complete:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(data)
        return data

    def _render(self, layer: str, z: int, col: int, row: int):
        n = 2 ** z
        k = TILE * SUPERSAMPLE
        frac = (np.arange(k) + 0.5) / k
        x_km = (-128 + (col + frac) * 256 / n) * HALF_EXTENT_KM / 128
        y_km = (128 - (row + frac) * 256 / n) * HALF_EXTENT_KM / 128
        X, Y = np.meshgrid(x_km, y_km)
        r = np.hypot(X, Y)
        lon = np.degrees(np.arctan2(X, Y))
        lat = -90.0 + np.degrees(2 * np.arctan(r / (2 * MOON_RADIUS_KM)))

        # Equirectangular source pixel for every output sample.
        s = min(LAYERS[layer]["max_src"], z + 3)
        width, height = 512 * 2 ** s, 256 * 2 ** s
        px = (lon + 180.0) / 360.0 * width
        py = np.clip((90.0 - lat) / 180.0 * height, 0, height - 1e-6)
        ti = np.floor(px / TILE).astype(int) % (2 ** (s + 1))
        tj = np.floor(py / TILE).astype(int)
        xx = np.floor(px).astype(int) % TILE
        yy = np.floor(py).astype(int) % TILE

        keys = np.unique(tj * (2 ** (s + 1)) + ti)
        needed = [(int(v) // 2 ** (s + 1), int(v) % 2 ** (s + 1)) for v in keys]
        ok = list(self._pool.map(lambda t: self._fetch(layer, s, t[0], t[1]), needed))

        canvas = np.full((k, k, 3), 40, dtype=np.uint8)   # grey where a source tile is missing
        for (tr, tc), have in zip(needed, ok):
            if not have:
                continue
            mask = (tj == tr) & (ti == tc)
            canvas[mask] = self._decode(layer, s, tr, tc)[yy[mask], xx[mask]]

        small = canvas.reshape(TILE, SUPERSAMPLE, TILE, SUPERSAMPLE, 3).mean(axis=(1, 3)).astype(np.uint8)
        buf = io.BytesIO()
        Image.fromarray(small).save(buf, "JPEG", quality=86)
        return buf.getvalue(), all(ok)
