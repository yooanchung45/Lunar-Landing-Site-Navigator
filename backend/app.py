"""API + static frontend.   Run from the project root:

    .venv\\Scripts\\python -m uvicorn backend.app:app --reload
"""
from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import numpy as np
from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles

from . import datasets, rastertiles, scoring
from .basemap import LAYERS, Basemap, native_max_zoom
from . import candidates as cand
from . import validation
from .artemis_sites import ARTEMIS_SITES
from .horizon import combined_profile
from .mission import DEFAULT_REQUIREMENTS, DEFAULT_STAY_DAYS
from .validation import nasa_map_value
from .pipeline import evaluate_site
from .slope import slope_stats
from .providers.ephemeris import DSN_MIN_ELEVATION_DEG, SkyfieldEphemeris
from .providers.terrain import FlatTerrain, LolaTerrain
from .sites import SITES

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "backend" / "data"
MAX_POINTS = 200_000

app = FastAPI(title="Lunar Navigator")
app.add_middleware(GZipMiddleware, minimum_size=1024)      # the time-series JSON is large and compresses ~5x


@lru_cache(maxsize=1)
def ephemeris() -> SkyfieldEphemeris:
    # First call downloads ~20 MB of NASA/NAIF kernels into backend/data/.
    return SkyfieldEphemeris(DATA_DIR)


@lru_cache(maxsize=1)
def terrain_for(have_dem: bool):
    return LolaTerrain(datasets.path_of("dem80")) if have_dem else FlatTerrain()


@lru_cache(maxsize=1)
def fine_terrain():
    """20 m LOLA elevation (near a site and for slope), fetched tile by tile from NASA; None if unavailable."""
    try:
        from .remote_dem import TileStore
        store = TileStore(datasets.ON_DEMAND["dem20"][0], datasets.path_of("dem20"),
                          reuse_part=datasets.GEO_DIR / (datasets.path_of("dem20").name + ".part"))
        return LolaTerrain(datasets.path_of("dem20"), "NASA LOLA elevation", store=store)
    except Exception as err:                      # no network and nothing cached: coarse data only
        print(f"20 m terrain unavailable ({err}); using 80 m")
        return None


def terrain():
    """The best elevation data we have right now (flat sphere until the DEM is downloaded)."""
    return terrain_for(datasets.available("dem80"))


def parse_start(start: str) -> datetime:
    try:
        start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(422, "start must be an ISO date, e.g. 2028-03-01")
    if start_dt.tzinfo is not None:
        start_dt = start_dt.astimezone(timezone.utc).replace(tzinfo=None)
    if not 1900 <= start_dt.year <= 2049:                    # libration kernel covers 1900-2050
        raise HTTPException(422, "start must be between 1900 and 2049")
    return start_dt


def requirements_from(slope_max_deg, sun_cover_min, earth_cover_min, dates_min) -> dict:
    return {"slope_max_deg": slope_max_deg, "sun_cover_min": sun_cover_min,
            "earth_cover_min": earth_cover_min, "dates_min": dates_min}


@app.get("/api/defaults")
def defaults():
    """Starting values for the mission and requirement controls."""
    return {"stay_days": DEFAULT_STAY_DAYS, "requirements": DEFAULT_REQUIREMENTS, "dsn_min_elevation_deg": DSN_MIN_ELEVATION_DEG}


@app.get("/api/sites")
def sites():
    """Named crater centres, then sites published inside NASA's Artemis III candidate regions."""
    return SITES + [{"name": a["name"], "lat": round(a["lat"], 5), "lon": round(a["lon"], 5), "kind": "published"} for a in ARTEMIS_SITES]


@app.get("/api/evaluate")
def evaluate(
    lat: float = Query(..., ge=-90, le=-60),
    lon: float = Query(..., ge=-180, le=360),
    start: str = Query(..., description="ISO date or datetime, UTC"),
    days: float = Query(30, gt=0, le=3650),
    step_min: float = Query(30, ge=1, le=1440),
    sun_min_deg: float = Query(0.0, ge=0, le=30),
    earth_min_deg: float = Query(0.0, ge=0, le=30),
    observer_height_m: float = Query(2.0, ge=0, le=100),
    max_range_km: float = Query(150.0, ge=5, le=300),
    stay_days: float = Query(DEFAULT_STAY_DAYS, gt=0, le=60),
    dsn: bool = True,
    slope_max_deg: float = Query(DEFAULT_REQUIREMENTS["slope_max_deg"], ge=0, le=45),
    sun_cover_min: float = Query(DEFAULT_REQUIREMENTS["sun_cover_min"], ge=0, le=1),
    earth_cover_min: float = Query(DEFAULT_REQUIREMENTS["earth_cover_min"], ge=0, le=1),
    dates_min: float = Query(DEFAULT_REQUIREMENTS["dates_min"], ge=0, le=1),
):
    start_dt = parse_start(start)
    if days * 86400 / (step_min * 60) > MAX_POINTS:
        raise HTTPException(422, "too many samples: shorten the period or raise the step")
    profile, slope = site_geometry(round(lat, 4), round(lon, 4), observer_height_m, max_range_km)
    return evaluate_site(
        ephemeris(), terrain(), lat, lon, start_dt, days, step_min, sun_min_deg, earth_min_deg,
        observer_height_m, max_range_km, profile=profile, slope=slope, use_dsn=dsn, stay_days=stay_days,
        requirements=requirements_from(slope_max_deg, sun_cover_min, earth_cover_min, dates_min),
    )


_geometry_lock = threading.Lock()


@lru_cache(maxsize=None)
def site_geometry(lat: float, lon: float, height_m: float, range_km: float):
    """Horizon + slope for a point: the slow part (~2 s), independent of dates, so kept on disk."""
    fine = fine_terrain()
    tag = f"{terrain().resolution_m:g}" + (f"+{fine.resolution_m:g}" if fine else "")
    path = DATA_DIR / "horizons" / f"{lat:.4f}_{lon:.4f}_{height_m:g}_{range_km:g}_{tag}.npz"
    if path.exists():
        saved = np.load(path)
        return saved["profile"], {k: (None if np.isnan(v) else float(v)) for k, v in zip(("mean", "p90", "max"), saved["slope"])}
    profile = combined_profile(fine, terrain(), lat, lon, height_m, range_km)
    slope = slope_stats(fine or terrain(), lat, lon)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(path, profile=profile, slope=[np.nan if slope[k] is None else slope[k] for k in ("mean", "p90", "max")])
    return profile, slope


def all_sites() -> list[dict]:
    """Auto-found candidates plus the published Artemis-region sites, as leaderboard entries."""
    found = [{**c, "kind": "found"} for c in cand.load_candidates(SITES, DATA_DIR / "candidates.json")]
    published = [{"name": a["name"], "near": f"{a['region']} region", "lat": round(a["lat"], 4), "lon": round(a["lon"], 4),
                  "kind": "published", "source": a["source"]} for a in ARTEMIS_SITES]
    return published + found


def warm_up():
    """Precompute every leaderboard site's horizon in the background so the first ranking is quick."""
    try:
        if datasets.available("dem80"):
            with ThreadPoolExecutor(4) as pool:
                list(pool.map(lambda c: site_geometry(round(c["lat"], 4), round(c["lon"], 4), 2.0, 150.0), all_sites()))
    except Exception:                      # warm-up is best effort; real requests report their own errors
        pass


@app.on_event("startup")
def _start_warm_up():
    threading.Thread(target=warm_up, daemon=True).start()


@app.get("/api/leaderboard")
def leaderboard(
    start: str = Query(...),
    days: float = Query(60, gt=0, le=3650),
    step_min: float = Query(60, ge=5, le=1440),
    sun_min_deg: float = Query(0.0, ge=0, le=30),
    earth_min_deg: float = Query(0.0, ge=0, le=30),
    observer_height_m: float = Query(2.0, ge=0, le=100),
    stay_days: float = Query(DEFAULT_STAY_DAYS, gt=0, le=60),
    dsn: bool = True,
    slope_max_deg: float = Query(DEFAULT_REQUIREMENTS["slope_max_deg"], ge=0, le=45),
    sun_cover_min: float = Query(DEFAULT_REQUIREMENTS["sun_cover_min"], ge=0, le=1),
    earth_cover_min: float = Query(DEFAULT_REQUIREMENTS["earth_cover_min"], ge=0, le=1),
    dates_min: float = Query(DEFAULT_REQUIREMENTS["dates_min"], ge=0, le=1),
):
    """Every candidate and published site scored over the same period."""
    if not datasets.available("dem80"):
        raise HTTPException(503, "needs the LOLA elevation data: python -m backend.datasets")
    start_dt = parse_start(start)
    if days * 86400 / (step_min * 60) > MAX_POINTS:
        raise HTTPException(422, "too many samples: shorten the period or raise the step")
    sites_ = all_sites()
    eph, ter = ephemeris(), terrain()
    req = requirements_from(slope_max_deg, sun_cover_min, earth_cover_min, dates_min)
    n = int(days * 86400 / (step_min * 60)) + 1
    # Sun/Earth/ground-station positions are the same for every site: compute once.
    vectors = eph.vectors(start_dt, step_min * 60.0, n)
    station_up = eph.station_in_view(start_dt, step_min * 60.0, n, DSN_MIN_ELEVATION_DEG) if dsn else None

    def one(c):
        profile, slope = site_geometry(round(c["lat"], 4), round(c["lon"], 4), observer_height_m, 150.0)
        r = evaluate_site(eph, ter, c["lat"], c["lon"], start_dt, days, step_min, sun_min_deg, earth_min_deg,
                          profile=profile, slope=slope, vectors=vectors, station_up=station_up, use_dsn=dsn,
                          stay_days=stay_days, requirements=req)
        return {**c, "score": r["score"], "slope": slope, "mission": r["mission"], "requirements": r["requirements"],
                "nasa_sun": nasa_map_value("illum", c["lat"], c["lon"]), "nasa_earth": nasa_map_value("earth", c["lat"], c["lon"])}

    with ThreadPoolExecutor(4) as pool:
        rows = list(pool.map(one, sites_))
    rows.sort(key=lambda row: row["score"]["total"], reverse=True)
    return {"start": start, "days": days, "weights": scoring.WEIGHTS, "stay_days": stay_days, "requirement_values": req, "fine_terrain": fine_terrain() is not None, "sites": rows}


@app.get("/api/validation")
def validation_report(refresh: bool = False):
    """Our simulation vs NASA's long-term maps: at published Artemis-region sites and at random polar points."""
    path = DATA_DIR / "validation_published.json"
    fine = fine_terrain() is not None
    # what the saved numbers were computed with; any change forces a recompute
    key = f"{fine}|{validation.NASA_EQUIVALENT_HEIGHT_M}|{validation.GRID}|{validation.CYCLE_DAYS:.0f}"
    saved = json.loads(path.read_text(encoding="utf-8")) if path.exists() and not refresh else None
    if saved is None or saved.get("key") != key:
        if not datasets.available("dem80"):
            raise HTTPException(503, "needs the LOLA elevation data: python -m backend.datasets")
        rows = validation.compare(ARTEMIS_SITES, ephemeris(), terrain(), site_geometry)
        saved = {"key": key, "fine_terrain": fine, "rows": rows}
        path.write_text(json.dumps(saved), encoding="utf-8")
    for row in saved["rows"]:
        row["problems"] = validation.problems(row)
    random_path = validation.RANDOM_PATH
    random = json.loads(random_path.read_text(encoding="utf-8"))["summary"] if random_path.exists() else None
    return {"published": saved["rows"], "random": random, "fine_terrain": saved["fine_terrain"],
            "tolerance_pts": {"sun": validation.SUN_TOLERANCE_PTS, "earth": validation.EARTH_TOLERANCE_PTS}}


basemap = Basemap(DATA_DIR / "tiles")


@app.get("/api/basemap/{layer}/{z}/{col}/{row}.jpg")
def basemap_tile(layer: str, z: int, col: int, row: int):
    if layer not in LAYERS:
        raise HTTPException(404, "unknown layer")
    if not 0 <= z <= native_max_zoom(layer) or not (0 <= col < 2 ** z and 0 <= row < 2 ** z):
        raise HTTPException(404, "tile out of range")
    return Response(
        basemap.tile(layer, z, col, row),
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/api/datasets")
def dataset_status():
    """Which real-data layers are ready (so the UI can grey out the rest)."""
    have = datasets.available
    return {
        "relief": have("dem80"),
        "sunlight": have("illum75"),
        "earthvis": have("earth75"),
        "shadowcam": [
            {"id": m["id"], "feature": m["feature"], "lat": m["lat"], "lon": m["lon"]}
            for m in datasets.shadowcam_mosaics()
        ],
        "native_zoom": {name: rastertiles.native_max_zoom(name) for name in rastertiles.LAYERS},
        "ramp_max": {name: spec["vmax"] for name, spec in rastertiles.LAYERS.items() if "vmax" in spec},
    }


@app.get("/api/tiles/{layer}/{z}/{col}/{row}.png")
def raster_tile(layer: str, z: int, col: int, row: int, az: float = 315.0, el: float = 12.0):
    if layer not in rastertiles.LAYERS:
        raise HTTPException(404, "unknown layer")
    if not 0 <= z <= rastertiles.native_max_zoom(layer) or not (0 <= col < 2 ** z and 0 <= row < 2 ** z):
        raise HTTPException(404, "tile out of range")
    az, el = round(az) % 360, min(max(round(el), 1), 60)      # whole degrees keeps the cache finite
    key = f"{az}_{el}" if layer == "relief" else (f"n{len(datasets.shadowcam_mosaics())}" if layer == "shadowcam" else "std")
    cached = DATA_DIR / "tiles" / "out" / layer / key / str(z) / str(col) / f"{row}.png"
    if cached.exists():
        data = cached.read_bytes()
    else:
        try:
            data = rastertiles.render(layer, z, col, row, az, el)
        except rastertiles.MissingData as err:
            raise HTTPException(503, str(err))
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(data)
    return Response(data, media_type="image/png", headers={"Cache-Control": "public, max-age=3600"})


# Must come last so it doesn't shadow /api.
app.mount("/", StaticFiles(directory=ROOT / "frontend", html=True), name="frontend")
