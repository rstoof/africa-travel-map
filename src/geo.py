"""Reference geography (Natural Earth 10m) keyed by the ISO3 codes we use."""
from functools import lru_cache
import json
from pathlib import Path

from shapely import make_valid
from shapely.geometry import box, shape
from shapely.ops import unary_union

REF = Path(__file__).resolve().parent.parent / "data" / "ref"

# Advice units that map onto several Natural Earth admin-0 units.
# The Dutch advice for Somalia covers Somaliland; Morocco's map includes Western Sahara.
COMPOSITE = {
    "SOM": ["SOM", "SOL"],
    "MAR": ["MAR", "SAH"],
}
# Overseas territories that Natural Earth folds into their sovereign state.
CLIP = {
    "REU": ("FRA", box(55.0, -21.6, 56.0, -20.7)),
}
NE_CODE = {"SSD": "SDS"}


@lru_cache(maxsize=1)
def _admin0():
    data = json.loads((REF / "ne_10m_admin_0_countries.geojson").read_text())
    return {f["properties"]["ADM0_A3"]: make_valid(shape(f["geometry"])) for f in data["features"]}


@lru_cache(maxsize=None)
def country_shape(iso):
    a0 = _admin0()
    if iso in CLIP:
        src, bbox = CLIP[iso]
        return a0[src].intersection(bbox)
    codes = COMPOSITE.get(iso, [NE_CODE.get(iso, iso)])
    return unary_union([a0[c] for c in codes])


def all_land():
    """Every admin-0 shape, for the neutral background layer."""
    return _admin0()


@lru_cache(maxsize=1)
def _places():
    data = json.loads((REF / "ne_10m_populated_places_simple.geojson").read_text())
    return [(f["properties"]["name"], f["properties"]["adm0_a3"], *f["geometry"]["coordinates"])
            for f in data["features"]]


def nearest_place(lon, lat, iso=None, max_deg=0.8):
    """Name of the nearest populated place (same country if possible), or None."""
    best, best_d = None, max_deg ** 2
    for name, a3, x, y in _places():
        d = (x - lon) ** 2 + (y - lat) ** 2
        if d < best_d and (iso is None or a3 == iso or a3 in COMPOSITE.get(iso, [])):
            best, best_d = name, d
    return best
