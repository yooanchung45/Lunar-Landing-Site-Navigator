from datetime import datetime

import numpy as np
import pytest

from backend.mission import DEFAULT_REQUIREMENTS, best_ranges, check_requirements, mission_stats

STEP = 3600.0                                   # one-hour samples; a 1-day stay = 24 samples
T0 = datetime(2028, 3, 1)


def test_coverage_of_a_known_pattern():
    # lit for 3 days, dark for 3 days, repeating (144-hour period), 100 hours of data, 1-day stay
    sun = (np.arange(400) % 144) < 72
    m = mission_stats(sun, np.ones(400, bool), STEP, 1.0, T0)
    assert m["starts"] == 400 - 24 + 1
    assert 0.45 < m["mean_sun_cover"] < 0.55
    assert 0.30 < m["continuous_sun_share"] < 0.40          # 49 of every 144 starts keep the whole day lit
    assert m["best"][0]["sun_cover"] == 1.0 and m["best"][0]["slack_days"] > 0.5


def test_stay_longer_than_the_period_gives_none():
    assert mission_stats(np.ones(10, bool), np.ones(10, bool), STEP, 1.0, T0) is None


def test_best_ranges_group_a_long_window_into_one_range():
    # 11-day window of full coverage with a 1-day stay: starts 0..(11-1) days all score 1.0 -> ONE range, not many
    w = 24
    score = np.concatenate([np.zeros(24), np.ones(10 * 24 + 1), np.zeros(200), np.full(50, 0.6), np.zeros(100)])
    ranges = best_ranges(score, w, 3)
    assert len(ranges) == 2
    first, second = ranges
    assert (first[1] - first[0]) == 10 * 24 and score[first[2]] == 1.0         # 10 days of landing slack
    assert abs(score[second[2]] - 0.6) < 1e-9
    assert best_ranges(np.zeros(50), w, 3) == []                               # nothing usable -> nothing reported


def test_best_ranges_do_not_report_the_shoulder_of_a_window_as_another_window():
    w = 24
    ramp = np.linspace(0, 1, w)                                                # coverage climbing into a window
    score = np.concatenate([np.zeros(50), ramp, np.ones(100), ramp[::-1], np.zeros(50)])
    assert len(best_ranges(score, w, 3)) == 1


def test_requirements_pass_and_fail_with_reasons():
    always = np.ones(300, bool)
    req = dict(DEFAULT_REQUIREMENTS)
    good = mission_stats(always, always, STEP, 1.0, T0, requirements=req)
    assert check_requirements(req, 3.0, good) == []
    steep = check_requirements(req, 20.0, good)
    assert len(steep) == 1 and "steeper" in steep[0]
    dark = mission_stats(np.zeros(300, bool), always, STEP, 1.0, T0, requirements=req)
    assert any("landing dates" in f for f in check_requirements(req, 3.0, dark))
    assert any("shorter" in f for f in check_requirements(req, 3.0, None))


def test_ground_station_in_view_is_often_but_not_always_at_a_strict_limit():
    from pathlib import Path
    from backend.providers.ephemeris import SkyfieldEphemeris
    data = Path(__file__).resolve().parent.parent / "backend" / "data"
    if not (data / "de421.bsp").exists():
        pytest.skip("ephemeris kernels not downloaded")
    up = SkyfieldEphemeris(data).station_in_view(T0, 1800.0, 48 * 60, 20.0)      # 60 days, strict 20 deg limit
    assert 0.75 < up.mean() < 1.0
