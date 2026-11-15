"""NASA's 20 m LOLA elevation (2.7 GB) fetched tile by tile, only where we look.

The GeoTIFF is tiled (512 x 512 px tiles, deflate-compressed), so any window of it is a handful
of byte ranges.  ``TileStore`` keeps a local file of the full size in which only the tiles we
have fetched are filled in; ordinary rasterio reads then work on it, provided ``ensure`` has been
called for the window first (``LolaTerrain(store=...)`` does that).  The server limits each
connection to ~0.12 MB/s, so tiles are fetched over many connections at once.

A site's ~8 km neighbourhood is a few tiles (a few MB); the ~40 leaderboard sites are ~200 MB
instead of 2.7 GB.  A previous partial download of the whole file is reused: file order is
tile order, so its first N bytes are valid tiles (recorded as ``prefix`` in the sidecar).
"""
from __future__ import annotations

import json
import math
import struct
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

CONNECTIONS = 12
HEADER_BYTES = 256 * 1024


class TileStore:
    def __init__(self, url: str, path: Path, reuse_part: Path | None = None):
        self.url, self.path = url, Path(path)
        self.reuse_part = reuse_part
        self.meta_path = self.path.with_suffix(self.path.suffix + ".tiles.json")
        self._lock = threading.Lock()
        self._client = httpx.Client(timeout=httpx.Timeout(30, read=120), follow_redirects=True,
                                    limits=httpx.Limits(max_connections=CONNECTIONS * 2))
        self._open()

    # ---- setup ---------------------------------------------------------------
    def _get(self, start: int, end: int) -> bytes:
        last = None
        for _ in range(5):
            try:
                res = self._client.get(self.url, headers={"Range": f"bytes={start}-{end}"})
                if res.status_code == 206:
                    return res.content
                last = RuntimeError(f"HTTP {res.status_code}")
            except httpx.HTTPError as err:
                last = err
        raise RuntimeError(f"could not fetch bytes {start}-{end} of {self.url}: {last}")

    def _open(self):
        part = self.reuse_part or self.path.with_suffix(self.path.suffix + ".part")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.meta_path.exists() and self.path.exists():
            meta = json.loads(self.meta_path.read_text())
            self.prefix, self.tiles = meta["prefix"], set(meta["tiles"])
        else:
            total = int(self._client.head(self.url).headers["content-length"])
            if part.exists():                                   # reuse an interrupted full download
                part.replace(self.path)
                self.prefix = self.path.stat().st_size
            else:
                self.path.write_bytes(self._get(0, HEADER_BYTES - 1))
                self.prefix = HEADER_BYTES
            self.tiles = set()
            with open(self.path, "r+b") as f:
                f.truncate(total)                               # full length; unfetched tiles read as zeros
            self._save()
        head = self._read(0, HEADER_BYTES)
        if len(head) < HEADER_BYTES or head[:4] != b"II+\x00":
            raise RuntimeError("unexpected GeoTIFF layout (expected little-endian BigTIFF)")
        self._parse(head)

    def _parse(self, head: bytes):
        ifd = struct.unpack("<Q", head[8:16])[0]
        count = struct.unpack("<Q", head[ifd:ifd + 8])[0]
        tags = {}
        for i in range(count):
            e = head[ifd + 8 + 20 * i: ifd + 28 + 20 * i]
            tag, typ, n = struct.unpack("<HHQ", e[:12])
            tags[tag] = (typ, n, e[12:20])
        scalar = lambda tag: (struct.unpack("<H", tags[tag][2][:2])[0] if tags[tag][0] == 3
                              else struct.unpack("<I", tags[tag][2][:4])[0])
        self.width, self.height = scalar(256), scalar(257)
        self.tile_w, self.tile_h = scalar(322), scalar(323)
        self.across = math.ceil(self.width / self.tile_w)
        self.down = math.ceil(self.height / self.tile_h)
        n_tiles = self.across * self.down
        off_ptr, cnt_ptr = (struct.unpack("<Q", tags[t][2])[0] for t in (324, 325))
        if max(off_ptr + 8 * n_tiles, cnt_ptr + 4 * n_tiles) > len(head):
            raise RuntimeError("tile table is outside the header block")
        self.offsets = struct.unpack(f"<{n_tiles}Q", head[off_ptr: off_ptr + 8 * n_tiles])
        self.counts = struct.unpack(f"<{n_tiles}I", head[cnt_ptr: cnt_ptr + 4 * n_tiles])

    def _read(self, start: int, n: int) -> bytes:
        with open(self.path, "rb") as f:
            f.seek(start)
            return f.read(n)

    def _save(self):
        self.meta_path.write_text(json.dumps({"prefix": self.prefix, "tiles": sorted(self.tiles)}))

    # ---- use -----------------------------------------------------------------
    def have(self, index: int) -> bool:
        return index in self.tiles or self.offsets[index] + self.counts[index] <= self.prefix

    def tiles_for(self, left, bottom, right, top, origin_x, origin_y, res) -> list[int]:
        """Tile numbers covering a projected window (metres), given the raster's top-left corner and pixel size."""
        c0, c1 = (left - origin_x) / res, (right - origin_x) / res
        r0, r1 = (origin_y - top) / res, (origin_y - bottom) / res
        tx0, tx1 = max(int(c0 // self.tile_w), 0), min(int(c1 // self.tile_w), self.across - 1)
        ty0, ty1 = max(int(r0 // self.tile_h), 0), min(int(r1 // self.tile_h), self.down - 1)
        return [ty * self.across + tx for ty in range(ty0, ty1 + 1) for tx in range(tx0, tx1 + 1)]

    def ensure(self, tile_indices) -> int:
        """Fetch any of these tiles we don't have yet; returns how many were fetched."""
        with self._lock:
            missing = [i for i in tile_indices if not self.have(i) and self.counts[i] > 0]
            if not missing:
                return 0

            def fetch(i):
                return i, self._get(self.offsets[i], self.offsets[i] + self.counts[i] - 1)

            with ThreadPoolExecutor(CONNECTIONS) as pool, open(self.path, "r+b") as f:
                for i, data in pool.map(fetch, missing):
                    f.seek(self.offsets[i])
                    f.write(data)
                    self.tiles.add(i)
            self._save()
            return len(missing)
