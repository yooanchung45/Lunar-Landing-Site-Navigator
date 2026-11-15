"""Published landing-site positions inside NASA's Artemis III candidate regions.

NASA's August 2022 announcement names 13 regions (each ~15 x 15 km) but publishes no centre
coordinates.  The points below are specific sites that researchers proposed or ranked
*inside* those regions, with the coordinates exactly as printed in the cited papers
(degrees-minutes-seconds converted to decimal).  They are one author's pick within a region,
not NASA's official landing spots.  Longitudes east-positive.

  [A] Gracy & Lee, "Candidate landing sites for Artemis 3 in two NASA candidate landing
      regions nearest the lunar south pole", LPSC 2024, abstract 1695.  Picked by the criteria
      slope <= 5 deg over >= 25% of a 2 km zone, sunlit >= 33% of the time, direct-to-Earth >= 50%.
  [B] Table 3 of arXiv:2406.19863, "Evaluating potential landing sites for the Artemis III
      mission using a multi-criteria decision making approach" (TOPSIS ranking of 1247 points).
"""
from __future__ import annotations


def dms(deg: int, minutes: int, seconds: float, south_or_west: bool = False) -> float:
    value = deg + minutes / 60 + seconds / 3600
    return -value if south_or_west else value


ARTEMIS_SITES = [
    {"name": "Connecting Ridge", "region": "Connecting Ridge", "lat": -89.53432, "lon": 209.94767 - 360, "source": "A",
     "claims": {"sun_min": 0.33, "earth_min": 0.50, "slope_max_deg": 5.0}},
    {"name": "Peak Near Shackleton", "region": "Peak Near Shackleton", "lat": -89.01701, "lon": 126.27302, "source": "A",
     "claims": {"sun_min": 0.33, "earth_min": 0.50, "slope_max_deg": 5.0}},
    {"name": "Nobile Rim 2 (top TOPSIS pick)", "region": "Nobile Rim 2", "lat": -dms(84, 12, 5.61), "lon": dms(60, 41, 59.61), "source": "B"},
    {"name": "Amundsen Rim", "region": "Amundsen Rim", "lat": -dms(84, 11, 30.63), "lon": dms(67, 13, 44.55), "source": "B"},
    {"name": "Faustini Rim A", "region": "Faustini Rim A", "lat": -dms(88, 5, 13.01), "lon": dms(98, 21, 19.76), "source": "B"},
    {"name": "Peak Near Shackleton (alt. pick)", "region": "Peak Near Shackleton", "lat": -dms(89, 0, 18.22), "lon": dms(125, 18, 33.49), "source": "B"},
    {"name": "de Gerlache Rim 2", "region": "de Gerlache Rim 2", "lat": -dms(88, 30, 56.38), "lon": -dms(61, 10, 2.35), "source": "B"},
    {"name": "Site 004 (science site, near pole)", "region": "Site 004", "lat": -dms(89, 57, 33.08), "lon": dms(135, 49, 6.44), "source": "B"},
]
