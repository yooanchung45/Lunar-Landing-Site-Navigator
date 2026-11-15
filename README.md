# Lunar Navigator

Lunar south pole landing-site explorer: for a candidate site and a date range,
show when the Sun is usable for power and Earth is in line of sight for comms,
and when both hold at once.

## Run

```
run.bat
```
then open http://127.0.0.1:8000. The first request downloads ~20 MB of NASA/NAIF
kernels into `backend/data/` (cached after that).

One-time download of the real map/terrain data (~650 MB, resumable, safe to re-run):

```
.venv\Scripts\python -m backend.datasets
```

Until it finishes, the layers that need it are greyed out and the analysis uses a flat Moon.

Tests: `.venv\Scripts\python -m pytest tests`

## Layout

| Path | What it does | Status |
|---|---|---|
| `backend/providers/ephemeris.py` | Sun and Earth directions in the Moon-fixed frame (JPL DE421 + NAIF lunar orientation, via Skyfield) | working, real data |
| `backend/geometry.py` | direction -> elevation/azimuth at a site | working, tested |
| `backend/providers/terrain.py` | `TerrainProvider` interface; `FlatTerrain` and `LolaTerrain` (NASA LOLA 80 m DEM) | working |
| `backend/horizon.py` | terrain horizon per compass direction (walk outward over the DEM, keep the steepest angle) | working, tested on synthetic terrain; **TODO(joint)** range/step/edge choices |
| `backend/windows.py` | boolean series -> windows | baseline, edges accurate to 1 step; **TODO(joint)** |
| `backend/scoring.py` | site metrics + total score (power / comms / survival / terrain) | v1, weights are judgement calls |
| `backend/mission.py` | stay-window analysis (every landing date) + pass/fail requirements | done |
| `backend/remote_dem.py` | fetches NASA's 20 m elevation tile by tile, only where needed | done |
| `backend/validation.py` | `python -m backend.validation`: our simulation vs NASA's maps at published Artemis-region sites | done |
| `backend/slope.py` | slope around a site from the LOLA DEM | done (80 m DEM: hills, not boulders) |
| `backend/candidates.py` | finds candidate spots from NASA sun/Earth/DEM maps | done |
| `backend/pipeline.py` | wires it together for one site | working |
| `backend/sites.py` | candidate sites | **approximate coordinates, replace with the official list** |
| `backend/basemap.py` | on-demand polar tiles: reprojects NASA Moon Trek (LRO WAC imagery, LOLA shaded elevation) into polar stereographic, cached in `backend/data/tiles/` | working |
| `backend/datasets.py` | downloads and indexes the NASA rasters and ShadowCam mosaics into `backend/data/geo/` | working |
| `backend/rastertiles.py` | map tiles cut from those rasters: shaded relief (computed here), sunlight, Earth visibility, ShadowCam | working |
| `frontend/js/polar.js`, `map.js` | zoomable Leaflet map in polar coordinates, adaptive lat/lon grid, draggable site marker | working (not yet visually reviewed) |
| `frontend/` | map, elevation chart, timeline, score cards (no build step; Plotly from CDN) | working |

## Data sources

- Imagery: NASA LRO WAC mosaic and LOLA colour-shaded elevation, via NASA Moon Trek (tiles are reprojected on demand).
- Elevation: LOLA south polar DEM, `LDEM_80S_80MPP_ADJ` (80 m/pixel), NASA PGDA. 20 m and 10 m versions exist (2.6 GB and 4.8 GB) for later.
- Sunlight and Earth visibility maps: Mazarico et al. (2011), `AVGVISIB_*` and `AVGVISIB_*_EARTH`, averaged over the 18.6-year lunar cycle.
- ShadowCam: NASA instrument on KARI's Danuri (KPLO); controlled mosaics from the PDS archive at pds.shadowcam.im-ldi.com. Only the ~20 craters NASA/KARI have mosaicked, so coverage is patchy.

All polar rasters share our map's projection: polar stereographic, sphere radius 1737.4 km, x toward 90 E, y toward 0 E.

## Known simplifications (to resolve)

- The horizon uses a point Sun and point Earth; real discs are partly hidden at the skyline (the Sun is ~0.27 deg in radius).
- Sunlight/Earth-visibility overlays are long-term averages, not tied to your chosen dates.
- Sun and Earth are treated as points at disk centre (the Sun is ~0.27° in radius, which matters when elevations are within a degree or two).
- No atmosphere/refraction concerns on the Moon, but there is no antenna height or lander height yet.
- Ephemeris covers 1900-2050 (libration kernel limit).

## Real data to wire in

- NASA LOLA gridded polar DEMs (PDS Geosciences node), LROC NAC DTMs for small areas.
- Other agencies' terrain/illumination products (JAXA SELENE/Kaguya, ISRO Chandrayaan-2) behind the same `TerrainProvider`.
- Any SPICE kernel set behind `EphemerisProvider`.
