import numpy as np

from backend.geometry import elevation_azimuth
from backend.windows import find_windows


def test_pole_zenith_is_straight_up():
    el, _ = elevation_azimuth(np.array([[0.0, 0.0, -1.0]]), -90.0, 0.0)
    assert el[0] == 90.0 or abs(el[0] - 90.0) < 1e-9


def test_target_on_horizon_and_below():
    # Site on the equator at lon 0: up = +x.  A target along +y is on the horizon (east).
    el, az = elevation_azimuth(np.array([[0.0, 1.0, 0.0], [-1.0, 0.0, 0.0]]), 0.0, 0.0)
    assert abs(el[0]) < 1e-9 and abs(az[0] - 90.0) < 1e-9
    assert abs(el[1] + 90.0) < 1e-9


def test_find_windows():
    mask = np.array([0, 1, 1, 0, 0, 1, 0, 1, 1])
    assert find_windows(mask, 60) == [(1, 3), (5, 6), (7, 9)]
    assert find_windows(np.zeros(4), 60) == []
