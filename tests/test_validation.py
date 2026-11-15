import numpy as np
import pytest

from backend import datasets
from backend.artemis_sites import ARTEMIS_SITES, dms
from backend.horizon import combined_profile, horizon_profile
from backend.providers.terrain import FlatTerrain
from backend.validation import nasa_map_value, problems


def test_dms_conversion_matches_the_papers_decimals():
    # arXiv:2406.19863 states 84 12' 5.61" S, 60 41' 59.61" E  =  -84.20156, 60.69989
    assert abs(-dms(84, 12, 5.61) + 84.20156) < 1e-5 and abs(dms(60, 41, 59.61) - 60.69989) < 1e-5


def test_all_published_sites_are_in_the_south_polar_cap():
    assert all(-90 <= s["lat"] <= -83 and -180 <= s["lon"] <= 360 for s in ARTEMIS_SITES)


def test_problems_flags_only_real_disagreement():
    ok = {"nasa_sun": 0.30, "sim_sun": 0.33, "nasa_earth": 0.40, "sim_earth": 0.41}
    bad = {**ok, "sim_sun": 0.45}
    assert problems(ok) == [] and len(problems(bad)) == 1


def test_combined_profile_without_fine_data_is_the_plain_profile():
    ter = FlatTerrain()
    assert np.array_equal(combined_profile(None, ter, -89.0, 0.0, 2.0, 20.0), horizon_profile(ter, -89.0, 0.0, 2.0, 20.0))


class Wall:
    """Flat ground with a 100 m wall 2 km east of the site (x = site + 2 km)."""
    name = "wall"

    def __init__(self, res, x0):
        self.resolution_m, self.x0 = res, x0

    def sampler_around(self, lat, lon, radius_m):
        return lambda x, y: np.where(np.asarray(x) > self.x0 + 2000, 100.0, 0.0) + 0 * np.asarray(y)


def test_fine_data_near_the_site_raises_the_horizon():
    from backend.providers.terrain import to_xy
    x0 = float(to_xy(-89.0, 0.0)[0])
    coarse, fine = FlatTerrain(), Wall(20.0, x0)
    with_fine = combined_profile(fine, coarse, -89.0, 0.0, 2.0, 30.0)
    without = combined_profile(None, coarse, -89.0, 0.0, 2.0, 30.0)
    assert with_fine.max() > without.max() + 1.0


@pytest.mark.skipif(not (datasets.available("illum85") and datasets.available("earth85")), reason="NASA maps not downloaded")
def test_nasa_maps_give_plausible_values_at_a_published_site():
    sun = nasa_map_value("illum", -89.534, -150.052)
    earth = nasa_map_value("earth", -89.534, -150.052)
    assert 0.3 < sun < 0.5 and 0.4 < earth < 0.6
