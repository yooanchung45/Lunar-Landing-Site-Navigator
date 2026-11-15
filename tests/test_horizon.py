import numpy as np

from backend.horizon import horizon_at, horizon_profile
from backend.providers.terrain import FlatTerrain, to_xy


class FunctionTerrain:
    """Terrain defined by a function of polar-stereographic metres."""
    name = "synthetic"
    resolution_m = 80.0

    def __init__(self, height):
        self.height = height

    def sampler_around(self, lat, lon, radius_m):
        return lambda x, y: self.height(np.asarray(x), np.asarray(y))


def test_flat_moon_horizon_is_the_small_geometric_dip():
    # An eye 2 m up on a smooth sphere sees the ground curve away ~0.09 degrees below level.
    profile = horizon_profile(FlatTerrain(), -89.0, 0.0, observer_height_m=2.0)
    assert profile.shape == (360,)
    assert np.all(profile < 0) and np.all(profile > -0.2)


def test_a_ridge_raises_the_horizon_only_in_its_direction():
    lat, lon = -89.0, 0.0
    x0, _ = to_xy(lat, lon)
    wall = lambda x, y: np.where((x > x0 + 10_000) & (x < x0 + 11_000), 1000.0, 0.0)   # east of the site
    profile = horizon_profile(FunctionTerrain(wall), lat, lon, observer_height_m=2.0)
    east = profile[85:96].max()
    # ~ atan(998 / 10 km) = 5.70 deg, less ~0.17 deg for the curvature of the Moon.
    assert 5.3 < east < 5.8
    assert profile[265:276].max() < 0            # nothing to the west


def test_taller_or_closer_terrain_blocks_more_sky():
    lat, lon = -89.0, 0.0
    x0, _ = to_xy(lat, lon)
    near = lambda x, y: np.where((x > x0 + 5_000) & (x < x0 + 6_000), 500.0, 0.0)
    far = lambda x, y: np.where((x > x0 + 20_000) & (x < x0 + 21_000), 500.0, 0.0)
    assert horizon_profile(FunctionTerrain(near), lat, lon)[90] > horizon_profile(FunctionTerrain(far), lat, lon)[90]


def test_unknown_ground_is_ignored():
    # No data anywhere except the site itself: nothing is blocking, so no NaN leaks out.
    nodata = lambda x, y: np.full(np.broadcast(x, y).shape, np.nan)
    profile = horizon_profile(FunctionTerrain(nodata), -89.0, 0.0)
    assert np.all(np.isfinite(profile))


def test_horizon_lookup_wraps_and_interpolates():
    profile = np.arange(360, dtype=float)
    assert horizon_at(profile, np.array([0.0]))[0] == 0.0
    assert abs(horizon_at(profile, np.array([10.5]))[0] - 10.5) < 1e-9
    # between bin 359 (value 359) and bin 0 (value 0) it wraps around
    assert abs(horizon_at(profile, np.array([359.5]))[0] - 179.5) < 1e-9
