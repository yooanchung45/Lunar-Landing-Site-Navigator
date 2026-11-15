"""Real NASA data we pull down and keep in backend/data/geo/.

Every raster here is in the same south polar stereographic system as our map
(origin at the pole, Moon sphere radius 1737.4 km, x toward 90 E, y toward 0 E),
so tiles are cut straight from the files with no reprojection.

Run ``python -m backend.datasets`` once to download everything (resumable).
"""
from __future__ import annotations

import csv
import io
import os
import sys
import time
from pathlib import Path

import httpx

GEO_DIR = Path(__file__).resolve().parent / "data" / "geo"
PGDA = "https://pgda.gsfc.nasa.gov/data/"
SHADOWCAM = "https://pds.shadowcam.im-ldi.com/derived/"

# name -> (url, what it is)
DATASETS = {
    "dem80": (PGDA + "LOLA_20mpp/LDEM_80S_80MPP_ADJ.TIF",
              "LOLA elevation, 80 m/pixel, 80S to pole (NASA PGDA)"),
    "illum75": (PGDA + "MoonIllumination/AVGVISIB_75S_120M_201608.TIF",
                "average sunlight over the 18.6-year cycle, 120 m, 75S to pole (Mazarico+ 2011)"),
    "illum85": (PGDA + "MoonIllumination/AVGVISIB_85S_060M_201608.TIF",
                "average sunlight, 60 m, 85S to pole"),
    "earth75": (PGDA + "MoonIllumination/AVGVISIB_75S_120M_201608_EARTH.TIF",
                "average Earth visibility, 120 m, 75S to pole"),
    "earth85": (PGDA + "MoonIllumination/AVGVISIB_85S_060M_201608_EARTH.TIF",
                "average Earth visibility, 60 m, 85S to pole"),
}


# Too big to download whole; remote_dem.TileStore fetches just the tiles we look at.
ON_DEMAND = {
    "dem20": (PGDA + "LOLA_20mpp/LDEM_80S_20MPP_ADJ.TIF",
              "LOLA elevation, 20 m/pixel, 80S to pole (NASA PGDA, 2.7 GB; fetched tile by tile)"),
}


# Kept outside the project because the project may live in a synced folder (OneDrive would try
# to upload a 2.7 GB sparse file).  Override with the LUNAR_NAVIGATOR_CACHE environment variable.
ON_DEMAND_DIR = Path(os.environ.get("LUNAR_NAVIGATOR_CACHE", Path.home() / ".lunar-navigator"))


def path_of(name: str) -> Path:
    if name in ON_DEMAND:
        return ON_DEMAND_DIR / Path(ON_DEMAND[name][0]).name
    return GEO_DIR / Path(DATASETS[name][0]).name


def available(name: str) -> bool:
    return path_of(name).exists()


# ---- ShadowCam (Danuri/KPLO) controlled mosaics ------------------------------
def _shadowcam_index() -> list[dict]:
    """The 20 m cloud-optimised mosaics NASA/KARI published around shadowed craters."""
    cache = GEO_DIR / "shadowcam_cmosaic.csv"
    if not cache.exists():
        GEO_DIR.mkdir(parents=True, exist_ok=True)
        res = httpx.get(SHADOWCAM + "collection_kplo-shadowcam_derived-cmosaic.csv", timeout=60)
        res.raise_for_status()
        cache.write_bytes(res.content)
    out = {}
    for row in csv.reader(io.StringIO(cache.read_text())):
        if len(row) < 2:
            continue
        lid = row[1].split("::")[0].rsplit(":", 1)[-1]          # shadowcam_cmosaic_<feature>_<pos>_<season>_<time>[_20m|_5m]
        if lid.endswith(("_5m", "_20m")):
            continue
        parts = lid.removeprefix("shadowcam_cmosaic_").split("_")
        feature, pos, season, tod = parts[0], parts[1], parts[2], parts[3]
        # pos looks like p871s0904: south latitude 87.1, longitude 90.4 E
        if "s" not in pos[1:]:
            continue                                            # northern hemisphere
        lat = -int(pos[1:pos.index("s")]) / 10
        lon = int(pos[pos.index("s") + 1:]) / 10
        out[lid] = {
            "id": lid, "feature": feature, "season": season, "time": tod, "lat": lat, "lon": lon,
            "url": f"{SHADOWCAM}cmosaic/{feature}/{lid}_20m_cog.tif",
            "path": GEO_DIR / "shadowcam" / f"{lid}_20m_cog.tif",
        }
    return sorted(out.values(), key=lambda m: m["id"])


def shadowcam_mosaics(only_downloaded: bool = True) -> list[dict]:
    mosaics = _shadowcam_index()
    return [m for m in mosaics if m["path"].exists()] if only_downloaded else mosaics


# ---- downloading -------------------------------------------------------------
def download(url: str, dest: Path, label: str = "", retries: int = 8) -> None:
    """Resumable download; verifies the final size against the server."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    with httpx.Client(timeout=httpx.Timeout(60, read=120), follow_redirects=True) as client:
        head = client.head(url)
        head.raise_for_status()
        if "html" in head.headers.get("content-type", ""):
            raise RuntimeError(f"{url} is not a data file (server returned a web page)")
        total = int(head.headers["content-length"])
        if dest.exists() and dest.stat().st_size == total:
            return
        for attempt in range(1, retries + 1):
            have = part.stat().st_size if part.exists() else 0
            if have == total:
                break
            try:
                with client.stream("GET", url, headers={"Range": f"bytes={have}-"} if have else {}) as res:
                    if res.status_code not in (200, 206):
                        raise httpx.HTTPError(f"HTTP {res.status_code}")
                    if res.status_code == 200 and have:
                        have = 0                                  # server ignored Range: start over
                    with open(part, "ab" if have else "wb") as f:
                        for chunk in res.iter_bytes(1 << 20):
                            f.write(chunk)
            except httpx.HTTPError as err:
                print(f"  {label}: attempt {attempt} interrupted ({err}); retrying", flush=True)
                time.sleep(min(2 * attempt, 15))
        if part.stat().st_size != total:
            raise RuntimeError(f"{label or url}: got {part.stat().st_size} of {total} bytes")
        part.replace(dest)


def fetch_all(include_shadowcam: bool = True) -> None:
    for name, (url, what) in DATASETS.items():
        if available(name):
            print(f"have  {name}", flush=True)
            continue
        print(f"fetch {name}: {what}", flush=True)
        download(url, path_of(name), name)
    if include_shadowcam:
        mosaics = shadowcam_mosaics(only_downloaded=False)
        print(f"ShadowCam: {len(mosaics)} south-polar mosaics", flush=True)
        for m in mosaics:
            if m["path"].exists():
                continue
            print(f"fetch {m['id']}", flush=True)
            try:
                download(m["url"], m["path"], m["id"])
            except Exception as err:                                # one bad mosaic shouldn't stop the rest
                print(f"  skipped {m['id']}: {err}", flush=True)
    print("done", flush=True)


if __name__ == "__main__":
    fetch_all(include_shadowcam="--no-shadowcam" not in sys.argv)
