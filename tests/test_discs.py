import numpy as np

from backend.geometry import EARTH_RADIUS_KM, MOON_RADIUS_KM, sky_disc, visible_fraction


def test_visible_fraction_limits():
    f = visible_fraction(np.array([-1.0, 0.0, 1.0]), 0.27, 0.0)
    assert f[0] == 0 and abs(f[1] - 0.5) < 1e-9 and f[2] == 1


def test_disc_edge_clears_before_centre():
    # centre 0.2 deg below the horizon, radius 0.27: a sliver is still visible
    assert 0 < visible_fraction(-0.2, 0.27, 0.0) < 0.1


def test_earth_parallax_lowers_earth_at_the_limb():
    # Earth on the local horizon as seen from the Moon's centre: from the surface it is lower.
    d = 384_400.0
    el, _, r = sky_disc(np.array([[d, 0.0, 0.0]]), 0.0, 90.0, EARTH_RADIUS_KM)  # site 90 deg east of Earth
    assert abs(el[0] + np.degrees(MOON_RADIUS_KM / d)) < 0.01     # about -0.26 deg
    assert abs(r[0] - 0.95) < 0.01


def test_sun_below_the_flat_horizon_is_visible_over_falling_ground():
    """On a hilltop the true horizon dips below the local flat plane, so a Sun 1 deg 'below the
    horizon' can still be above the distant ground.  (A 0 deg floor on the horizon hid it.)"""
    from datetime import datetime
    from backend.pipeline import evaluate_site
    from backend.providers.terrain import FlatTerrain

    el = np.radians(-1.0)
    sun = np.tile([np.cos(el), 0.0, np.sin(-el)], (3, 1)) * 1.5e8        # at the south pole 'up' is -z
    earth = np.tile([0.0, 1.0, 0.0], (3, 1)) * 384_400.0
    common = dict(lat=-90.0, lon=0.0, start=datetime(2028, 1, 1), days=0.1, step_min=60.0, sun_min_deg=0.0, earth_min_deg=0.0,
                  slope={"mean": 0, "p90": 0, "max": 0}, vectors=(sun, earth), use_dsn=False)
    dipping = evaluate_site(None, FlatTerrain(), profile=np.full(360, -5.0), **common)
    level = evaluate_site(None, FlatTerrain(), profile=np.zeros(360), **common)
    assert all(dipping["sun_ok"]) and not any(level["sun_ok"])
