"""Audit every downloaded NASA / ShadowCam file.   python -m backend.verify_data [--offline]

For each raster: complete (size matches the server), opens, uses the expected polar
stereographic projection (south pole, Moon sphere 1737.4 km), sits where it should,
and holds plausible values.  Exits non-zero if anything fails.
"""
from __future__ import annotations

import re
import sys

import httpx
import numpy as np
import rasterio

from . import datasets
from .providers.terrain import MOON_RADIUS_M, to_xy

# plausible physical value range per dataset (after scale/offset)
EXPECT = {
    "dem80": ("elevation m", -10_000, 10_000, 80),
    "illum75": ("sunlit fraction", 0.0, 1.0, 120),
    "illum85": ("sunlit fraction", 0.0, 1.0, 60),
    "earth75": ("Earth-visible fraction", 0.0, 1.0, 120),
    "earth85": ("Earth-visible fraction", 0.0, 1.0, 60),
}


def projection_problems(ds) -> list[str]:
    wkt = ds.crs.to_wkt() if ds.crs else ""
    problems = []
    if "Polar_Stereographic" not in wkt:
        problems.append("not polar stereographic")
    for pattern, label in ((r'latitude_of_origin",\s*-90(\.0+)?\]', "latitude of origin is not -90"),
                           (r'central_meridian",\s*0(\.0+)?\]', "central meridian is not 0"),
                           (r'false_easting",\s*0(\.0+)?\]', "false easting is not 0"),
                           (r'false_northing",\s*0(\.0+)?\]', "false northing is not 0")):
        if not re.search(pattern, wkt):
            problems.append(label)
    sphere = re.search(r'SPHEROID\["[^"]*",\s*([0-9.]+),\s*([0-9.]+)', wkt)
    if not sphere or abs(float(sphere.group(1)) - MOON_RADIUS_M) > 1 or float(sphere.group(2)) != 0:
        problems.append(f"sphere is not {MOON_RADIUS_M:.0f} m")
    if "scale_factor" in wkt and not re.search(r'scale_factor",\s*1(\.0+)?\]', wkt):
        problems.append("scale factor is not 1")
    return problems


def remote_size(client: httpx.Client, url: str):
    try:
        head = client.head(url, follow_redirects=True)
        if head.status_code == 200 and "html" not in head.headers.get("content-type", ""):
            return int(head.headers["content-length"])
    except (httpx.HTTPError, KeyError, ValueError):
        pass
    return None


def check_raster(path, url, client, label, value_range=None, pixel=None, offline=False, read_values=True):
    issues, notes = [], []
    if not path.exists():
        return ["missing"], notes
    if not offline and read_values:
        expected = remote_size(client, url)
        if expected is None:
            notes.append("could not check size against server")
        elif expected != path.stat().st_size:
            issues.append(f"size {path.stat().st_size} != server {expected}")
    try:
        with rasterio.open(path) as ds:
            issues += projection_problems(ds)
            if pixel and abs(ds.res[0] - pixel) > 1e-6:
                issues.append(f"pixel size {ds.res[0]} m, expected {pixel} m")
            if not read_values:
                return issues, notes + [f"{ds.width}x{ds.height} px, header and projection only (tiles are fetched on demand)"]
            sample = ds.read(1, out_shape=(min(ds.height, 700), min(ds.width, 700)), masked=True)
            scale, offset = ds.scales[0] or 1.0, ds.offsets[0] or 0.0
            vals = sample.astype("float64").compressed() * scale + offset
            if vals.size == 0:
                issues.append("no valid pixels")
            else:
                notes.append(f"{ds.width}x{ds.height} px, {100 * vals.size / sample.size:.0f}% valid, values {vals.min():.3g}..{vals.max():.3g}")
                if not np.all(np.isfinite(vals)):
                    issues.append("non-finite values")
                if value_range and (vals.min() < value_range[0] - 1e-6 or vals.max() > value_range[1] + 1e-6):
                    issues.append(f"values outside plausible range {value_range}")
            b = ds.bounds
            if label.startswith("shadowcam"):
                pass
            elif not (abs(b.left + b.right) < 1 and abs(b.top + b.bottom) < 1):
                issues.append("grid is not centred on the pole")
    except Exception as err:                               # unreadable / truncated file
        issues.append(f"cannot read: {err}")
    return issues, notes


def check_shadowcam_position(mosaic) -> list[str]:
    """The mosaic's footprint should contain the lat/lon in its own name (to ~0.1 deg)."""
    with rasterio.open(mosaic["path"]) as ds:
        x, y = (float(v) for v in to_xy(mosaic["lat"], mosaic["lon"]))
        b = ds.bounds
        slack = 4_000                                      # name is rounded to 0.1 deg (~3 km)
        inside = b.left - slack <= x <= b.right + slack and b.bottom - slack <= y <= b.top + slack
        return [] if inside else [f"footprint does not contain its named position ({mosaic['lat']}, {mosaic['lon']})"]


def main(argv=None) -> int:
    offline = "--offline" in (argv or sys.argv)
    failures = 0
    with httpx.Client(timeout=30) as client:
        print("NASA PGDA rasters")
        for name, (url, what) in datasets.DATASETS.items():
            label, lo, hi, pixel = EXPECT[name]
            issues, notes = check_raster(datasets.path_of(name), url, client, name, (lo, hi), pixel, offline)
            failures += bool(issues)
            print(f"  {'FAIL' if issues else 'ok  '} {name:8s} {'; '.join(issues + notes)}")
        if datasets.available("dem20"):
            issues, notes = check_raster(datasets.path_of("dem20"), "", client, "dem20", None, 20, offline, read_values=False)
            failures += bool(issues)
            print(f"  {'FAIL' if issues else 'ok  '} dem20     {'; '.join(issues + notes)}")
        print("ShadowCam (Danuri/KPLO) controlled mosaics")
        for m in datasets.shadowcam_mosaics(only_downloaded=False):
            issues, notes = check_raster(m["path"], m["url"], client, "shadowcam", None, 20.0, offline)
            if not issues:
                issues += check_shadowcam_position(m)
            failures += bool(issues)
            print(f"  {'FAIL' if issues else 'ok  '} {m['feature']:14s} {'; '.join(issues + notes)}")
    print(f"\n{'ALL CHECKS PASSED' if not failures else str(failures) + ' file(s) FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
