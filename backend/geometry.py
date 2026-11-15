"""Sky geometry at a point on the Moon (Moon-fixed frame, degrees in/out)."""
from __future__ import annotations

import numpy as np

def local_basis(lat_deg: float, lon_deg: float):
    """East, north, up unit vectors at a site, in the Moon-fixed frame."""
    lat, lon = np.radians(lat_deg), np.radians(lon_deg)
    up = np.array([np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)])
    east = np.array([-np.sin(lon), np.cos(lon), 0.0])
    north = np.array([-np.sin(lat) * np.cos(lon), -np.sin(lat) * np.sin(lon), np.cos(lat)])
    return east, north, up


def elevation_azimuth(directions: np.ndarray, lat_deg: float, lon_deg: float):
    """Elevation above the local horizontal and azimuth (0=N, 90=E, degrees).

    ``directions``: (n, 3) unit vectors toward the target, measured from the
    Moon's centre.

    This is the view from the Moon's CENTRE.  For the true view from a site on
    the surface use ``sky_disc``, which corrects Earth's parallax (up to ~0.26 deg).
    """
    east, north, up = local_basis(lat_deg, lon_deg)
    elevation = np.degrees(np.arcsin(np.clip(directions @ up, -1.0, 1.0)))
    azimuth = np.degrees(np.arctan2(directions @ east, directions @ north)) % 360.0
    return elevation, azimuth


MOON_RADIUS_KM = 1737.4
SUN_RADIUS_KM = 695_700.0
EARTH_RADIUS_KM = 6_371.0


def sky_disc(vectors_km: np.ndarray, lat_deg: float, lon_deg: float, body_radius_km: float):
    """Where a body appears from a site ON the surface, and how big it looks.

    ``vectors_km``: (n, 3) Moon-centre -> body vectors.  Subtracting the site's own
    position vector gives the true site -> body direction (parallax).  Returns
    (elevation_deg, azimuth_deg, angular_radius_deg) of the disc centre.
    """
    _, _, up = local_basis(lat_deg, lon_deg)
    to_body = vectors_km - MOON_RADIUS_KM * up
    dist = np.linalg.norm(to_body, axis=1)
    el, az = elevation_azimuth(to_body / dist[:, None], lat_deg, lon_deg)
    radius = np.degrees(np.arcsin(body_radius_km / dist))
    return el, az, radius


def visible_fraction(centre_el, radius, horizon_el):
    """Fraction of a disc's area above a straight horizon line.

    ``centre_el``: elevation of the disc centre; ``horizon_el``: elevation of the
    horizon at that azimuth (both degrees); ``radius``: angular radius (degrees).
    Circular-segment area: 0 when the whole disc is below, 0.5 when the centre sits
    on the horizon, 1 when it is fully clear.
    """
    x = np.clip((centre_el - horizon_el) / radius, -1.0, 1.0)
    return 1.0 - (np.arccos(x) - x * np.sqrt(1.0 - x * x)) / np.pi
