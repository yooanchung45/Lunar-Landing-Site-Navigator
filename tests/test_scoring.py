import numpy as np

from backend.candidates import pick_peaks, to_latlon
from backend.providers.terrain import to_xy
from backend.scoring import combine, score_site, terrain_subscore
from backend.slope import slope_stats


class Ramp:
    """Terrain rising `tilt` metres per metre toward +x."""
    name, resolution_m = "ramp", 80.0

    def __init__(self, tilt):
        self.tilt = tilt

    def sampler_around(self, lat, lon, radius_m):
        return lambda x, y: self.tilt * np.asarray(x, dtype=float) + 0 * np.asarray(y, dtype=float)


def test_slope_of_a_known_ramp():
    out = slope_stats(Ramp(np.tan(np.radians(10))), -88.0, 30.0)
    assert abs(out["p90"] - 10) < 0.01 and abs(out["max"] - 10) < 0.01


def test_flat_ground_has_zero_slope():
    assert slope_stats(Ramp(0.0), -88.0, 30.0)["max"] == 0


def test_terrain_subscore_shape():
    assert terrain_subscore(3) == 1 and terrain_subscore(15) == 0 and abs(terrain_subscore(10) - 0.5) < 1e-9
    assert terrain_subscore(None) == 0.5


def test_more_light_scores_higher_and_long_darkness_hurts():
    n, step = 1000, 3600.0
    always = np.ones(n, bool)
    half = np.arange(n) % 100 < 50            # 50% lit, dark spells of 50 h
    a, b = score_site(always, always, step), score_site(half, half, step)
    assert a.total > b.total
    assert a.longest_dark_h == 0 and abs(b.longest_dark_h - 50) < 1e-9
    assert a.subscores["reliability"] == 1 and b.subscores["reliability"] < 1


def test_unsafe_slope_is_flagged_and_partial_sun_counts():
    ok = np.ones(10, bool)
    s = score_site(ok, ok, 3600.0, sun_frac=np.full(10, 0.4), slope_p90_deg=20.0)
    assert not s.safe and abs(s.sun_visible_mean - 0.4) < 1e-9


def test_combine_is_a_weighted_average():
    subs = {"power": 1.0, "comms": 0.0, "reliability": 0.0, "terrain": 0.0}
    assert combine(subs, {"power": 1, "comms": 1, "reliability": 1, "terrain": 1}) == 25.0
    assert combine(subs, {"power": 3, "comms": 1, "reliability": 0, "terrain": 0}) == 75.0


def test_peak_picking_keeps_a_distance():
    grid = np.zeros((20, 20)); grid[5, 5] = 9; grid[5, 6] = 8; grid[15, 15] = 7
    assert pick_peaks(grid, 5, 3.0) == [(5, 5), (15, 15)]


def test_latlon_roundtrip():
    x, y = to_xy(-87.3, 77.0)
    lat, lon = to_latlon(float(x), float(y))
    assert abs(lat + 87.3) < 1e-6 and abs(lon - 77.0) < 1e-6
