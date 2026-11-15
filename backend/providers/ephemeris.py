"""Where are the Sun and Earth, as seen from the Moon?

Data source: NASA/JPL DE421 planetary ephemeris plus NAIF lunar orientation
kernels (Moon principal-axes / "mean Earth" frame), downloaded on first use
into backend/data/ and cached.

Everything downstream only needs ``EphemerisProvider.directions``: unit vectors
from the Moon's centre toward the Sun and Earth, expressed in the Moon-fixed
frame (so a site's latitude/longitude can be used directly).  To use another
organisation's data (e.g. a SPICE kernel set from ESA/JAXA/ISRO missions),
write another class with the same method.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Protocol

import numpy as np


# NASA Deep Space Network 34 m sites (approximate position, metres above sea level).
DSN_STATIONS = {
    "Goldstone": (35.4267, -116.8900, 1000.0),
    "Madrid": (40.4314, -4.2481, 720.0),
    "Canberra": (-35.4014, 148.9813, 690.0),
}
DSN_MIN_ELEVATION_DEG = 6.0         # antennas do not track the Moon lower than this above the local horizon


class EphemerisProvider(Protocol):
    def directions(
        self, start: datetime, step_s: float, n: int
    ) -> tuple[np.ndarray, np.ndarray]:
        """Return (sun, earth), each shape (n, 3): unit vectors in the
        Moon-fixed frame, sampled at start + k * step_s (UTC)."""

    def vectors(
        self, start: datetime, step_s: float, n: int
    ) -> tuple[np.ndarray, np.ndarray]:
        """Like ``directions`` but the actual Moon-centre -> body vectors in km,
        so the pipeline can work out distance (disc size, parallax)."""

    def station_in_view(
        self, start: datetime, step_s: float, n: int, min_elevation_deg: float
    ) -> np.ndarray:
        """Boolean (n,): at least one ground station sees the Moon above ``min_elevation_deg``."""


class SkyfieldEphemeris:
    KERNELS = (
        "de421.bsp",                       # planets + Moon, JPL
        "moon_080317.tf",                  # frame definitions
        "pck00008.tpc",                    # body constants
        "moon_pa_de421_1900-2050.bpc",     # lunar orientation (libration), 1900-2050
    )

    def __init__(self, data_dir: Path):
        from skyfield.api import Loader, PlanetaryConstants

        loader = Loader(str(data_dir))
        self.ts = loader.timescale()
        self.eph = loader("de421.bsp")
        constants = PlanetaryConstants()
        constants.read_text(loader("moon_080317.tf"))
        constants.read_text(loader("pck00008.tpc"))
        constants.read_binary(loader("moon_pa_de421_1900-2050.bpc"))
        self.frame = constants.build_frame_named("MOON_ME_DE421")

    def _times(self, start: datetime, step_s: float, n: int):
        seconds = start.second + start.microsecond / 1e6 + step_s * np.arange(n)
        return self.ts.utc(start.year, start.month, start.day, start.hour, start.minute, seconds)

    def vectors(self, start: datetime, step_s: float, n: int):
        t = self._times(start, step_s, n)
        moon = self.eph["moon"]
        out = [moon.at(t).observe(self.eph[body]).frame_xyz(self.frame).km.T for body in ("sun", "earth")]
        return out[0], out[1]

    def station_in_view(self, start: datetime, step_s: float, n: int, min_elevation_deg: float = DSN_MIN_ELEVATION_DEG):
        from skyfield.api import wgs84

        t = self._times(start, step_s, n)
        moon, earth = self.eph["moon"], self.eph["earth"]
        up = np.zeros(n, dtype=bool)
        for lat, lon, height in DSN_STATIONS.values():
            station = earth + wgs84.latlon(lat, lon, elevation_m=height)
            alt, _, _ = station.at(t).observe(moon).apparent().altaz()
            up |= alt.degrees >= min_elevation_deg
        return up

    def directions(self, start: datetime, step_s: float, n: int):
        sun, earth = self.vectors(start, step_s, n)
        unit = lambda v: v / np.linalg.norm(v, axis=1, keepdims=True)
        return unit(sun), unit(earth)
