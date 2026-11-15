"""Candidate sites offered in the UI.

Crater-centre coordinates from the IAU gazetteer as I remember them (checked
against NASA LOLA elevation: each should sit inside a deep depression -- Shackleton
needed correcting).  These are crater CENTRES, not landing sites: replace with the
official Artemis candidate-region list before drawing conclusions.
Longitudes are east-positive, selenographic.
"""
SITES = [
    {"name": "Shackleton", "lat": -89.54, "lon": 129.78},
    {"name": "Haworth", "lat": -87.5, "lon": -5.0},
    {"name": "Faustini", "lat": -87.3, "lon": 77.0},
    {"name": "de Gerlache", "lat": -88.5, "lon": -87.1},
]
