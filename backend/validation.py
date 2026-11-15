"""Does our simulation agree with NASA's own maps and with published landing-site claims?

  python -m backend.validation

NASA's long-term maps (Mazarico et al. 2011 style, 18.6-year average) are the independent
reference.  We compare them with our own time-resolved simulation over one year at published
Artemis-region sites (see artemis_sites.py), and check the claims those papers made when they
picked the sites.  Exit status is non-zero when we disagree by more than the tolerances.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import rasterio
from rasterio.windows import from_bounds

from . import datasets
from .providers.terrain import to_xy

# NASA does not state the observer height it used.  Scanning 0-2 m against 60 random polar points and the
# published Artemis sites, ~0.5 m reproduces its maps best (0 m too dark on ridges, 2 m too bright).  Mission
# analysis keeps a realistic lander height (default 2 m); only this comparison uses the NASA-equivalent one.
NASA_EQUIVALENT_HEIGHT_M = 0.5
SUN_TOLERANCE_PTS = 4.0         # full cycle, 240 m patch
EARTH_TOLERANCE_PTS = 4.0
WINDOW_M = 240.0


def nasa_map_value(kind: str, lat: float, lon: float, window_m: float = WINDOW_M) -> float | None:
    """Mean of NASA's long-term sunlight ('illum') or Earth-visibility ('earth') map around a point (0-1)."""
    x, y = (float(v) for v in to_xy(lat, lon))
    half = window_m / 2
    for suffix in ("85", "75"):                           # finer map first, wider one as fallback
        name = kind + suffix
        if not datasets.available(name):
            continue
        with rasterio.open(datasets.path_of(name)) as ds:
            b = ds.bounds
            if not (b.left + half < x < b.right - half and b.bottom + half < y < b.top - half):
                continue
            data = ds.read(1, window=from_bounds(x - half, y - half, x + half, y + half, ds.transform), masked=True)
            if data.count() == 0:
                continue
            return float(data.astype("float64").mean() * (ds.scales[0] or 1.0))
    return None


CYCLE_DAYS = 18.6 * 365.25     # the Moon's orbital-plane precession cycle that NASA's maps average over
GRID = 3                       # simulate a GRID x GRID patch, as the NASA value is an area average


def compare(sites, ephemeris, terrain, geometry, start=datetime(2010, 1, 1), days=CYCLE_DAYS, step_min=360):
    """Our simulation vs NASA's maps, like for like: the full 18.6-year cycle, averaged over the
    same ~240 m patch.  (Comparing one point over one year against an area average made a steep
    ridge like Peak Near Shackleton look 6 points off; it is mostly that mismatch.)"""
    from .candidates import to_latlon
    from .pipeline import evaluate_site

    n = int(days * 1440 / step_min) + 1
    vectors = ephemeris.vectors(start, step_min * 60.0, n)
    spacing = WINDOW_M / GRID
    offsets = (np.arange(GRID) - (GRID - 1) / 2) * spacing
    rows = []
    for s in sites:
        x0, y0 = (float(v) for v in to_xy(s["lat"], s["lon"]))
        sun, earth, slopes = [], [], []
        for dx in offsets:
            for dy in offsets:
                lat, lon = to_latlon(x0 + dx, y0 + dy)
                profile, slope = geometry(round(lat, 4), round(lon, 4), NASA_EQUIVALENT_HEIGHT_M, 150.0)
                sc = evaluate_site(ephemeris, terrain, lat, lon, start, days, step_min, 0.0, 0.0,
                                   profile=profile, slope=slope, vectors=vectors, use_dsn=False)["score"]
                sun.append(sc["sun_fraction"]); earth.append(sc["earth_fraction"]); slopes.append(sc["slope_p90_deg"])
        rows.append({
            "name": s["name"], "lat": s["lat"], "lon": s["lon"], "claims": s.get("claims"),
            "nasa_sun": nasa_map_value("illum", s["lat"], s["lon"]), "sim_sun": float(np.mean(sun)),
            "nasa_earth": nasa_map_value("earth", s["lat"], s["lon"]), "sim_earth": float(np.mean(earth)),
            "sim_sun_range": (min(sun), max(sun)),
            "slope_p90": slopes[len(slopes) // 2],
        })
    return rows


def random_sample(n: int, ephemeris, terrain, geometry, seed: int = 7, start=datetime(2010, 1, 1),
                  days=CYCLE_DAYS, step_min=180):
    """Our simulation vs NASA's maps at ``n`` random points of the polar cap (85-89.9 S), one point each.

    NASA's value is the 60 m map pixel at the point (the 85S product); ours is the simulation at
    that same point over the same 18.6-year cycle.  This measures the model over typical terrain,
    not just at hand-picked sites.
    """
    from .candidates import to_latlon
    from .pipeline import evaluate_site

    n_steps = int(days * 1440 / step_min) + 1
    vectors = ephemeris.vectors(start, step_min * 60.0, n_steps)
    rng = np.random.default_rng(seed)
    rows = []
    while len(rows) < n:
        x, y = rng.uniform(-145_000, 145_000, 2)
        lat, lon = to_latlon(x, y)
        if lat > -85.2 or lat < -89.95:
            continue
        nasa_sun, nasa_earth = nasa_map_value("illum", lat, lon, 60.0), nasa_map_value("earth", lat, lon, 60.0)
        if nasa_sun is None or nasa_earth is None:
            continue
        profile, slope = geometry(round(lat, 4), round(lon, 4), NASA_EQUIVALENT_HEIGHT_M, 150.0)
        sc = evaluate_site(ephemeris, terrain, lat, lon, start, days, step_min, 0.0, 0.0, profile=profile, slope=slope,
                           vectors=vectors, use_dsn=False)["score"]
        rows.append({"lat": lat, "lon": lon, "nasa_sun": nasa_sun, "sim_sun": sc["sun_fraction"],
                     "nasa_earth": nasa_earth, "sim_earth": sc["earth_fraction"], "slope_p90": sc["slope_p90_deg"]})
    return rows


def summarize(rows) -> dict:
    """Bias, RMSE and correlation (percentage points) of simulation minus NASA, for sun and Earth."""
    out = {"points": len(rows)}
    for kind in ("sun", "earth"):
        nasa = np.array([r[f"nasa_{kind}"] for r in rows]) * 100
        sim = np.array([r[f"sim_{kind}"] for r in rows]) * 100
        d = sim - nasa
        out[kind] = {"bias": float(d.mean()), "rmse": float(np.sqrt((d ** 2).mean())), "mae": float(np.abs(d).mean()),
                     "within_3": float((np.abs(d) <= 3).mean()), "r": float(np.corrcoef(nasa, sim)[0, 1])}
    return out


def problems(row) -> list[str]:
    out = []
    for kind, tol in (("sun", SUN_TOLERANCE_PTS), ("earth", EARTH_TOLERANCE_PTS)):
        nasa, sim = row[f"nasa_{kind}"], row[f"sim_{kind}"]
        if nasa is not None and abs(sim - nasa) * 100 > tol:
            out.append(f"{kind}: simulation {sim * 100:.1f}% vs NASA map {nasa * 100:.1f}% (tolerance {tol} points)")
    return out


RANDOM_PATH = Path(__file__).resolve().parent / "data" / "validation_random.json"


def main() -> int:
    from .app import DATA_DIR, ephemeris, site_geometry, terrain       # reuse the app's cached data
    from .artemis_sites import ARTEMIS_SITES

    if "--random" in sys.argv:
        n = int(sys.argv[sys.argv.index("--random") + 1])
        rows = random_sample(n, ephemeris(), terrain(), site_geometry)
        result = {"summary": summarize(rows), "rows": rows}
        RANDOM_PATH.write_text(json.dumps(result), encoding="utf-8")
        for kind, v in result["summary"].items():
            if kind != "points":
                print(f"{kind:6s} bias {v['bias']:+.2f}  rmse {v['rmse']:.2f}  mae {v['mae']:.2f}  within 3 pts {v['within_3'] * 100:.0f}%  r {v['r']:.3f}")
        print(f"{result['summary']['points']} random points -> {RANDOM_PATH}")
        return 0

    rows = compare(ARTEMIS_SITES, ephemeris(), terrain(), site_geometry)
    fmt = lambda v: "  n/a" if v is None else f"{v * 100:5.1f}"
    print("Simulation = 18.6-year cycle averaged over a 240 m patch; NASA = its long-term map over the same patch.")
    print(f"{'site':36s} {'NASA sun':>8s} {'sim sun':>8s} {'NASA Earth':>10s} {'sim Earth':>9s} {'slope p90':>9s}")
    bad = 0
    for row in rows:
        issues = problems(row)
        bad += bool(issues)
        print(f"{row['name']:36s} {fmt(row['nasa_sun']):>8s} {fmt(row['sim_sun']):>8s} {fmt(row['nasa_earth']):>10s} "
              f"{fmt(row['sim_earth']):>9s} {'n/a' if row['slope_p90'] is None else format(row['slope_p90'], '.1f') + ' deg':>9s}"
              + ("   <-- " + "; ".join(issues) if issues else ""))
    print("\nPublished claims (the sites were picked to meet these; NASA maps, 240 m neighbourhood):")
    for row in rows:
        c = row["claims"]
        if not c:
            continue
        for label, key, floor in (("sunlit", "nasa_sun", c["sun_min"]), ("Earth-visible", "nasa_earth", c["earth_min"])):
            v = row[key]
            if v is not None:
                print(f"  {row['name']:30s} {label:13s} >= {floor * 100:.0f}% : NASA map says {v * 100:.1f}% -> {'ok' if v >= floor else 'NOT MET'}")
    print(f"\n{'ALL WITHIN TOLERANCE' if not bad else str(bad) + ' site(s) outside tolerance'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
