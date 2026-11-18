"""Download Natural Earth reference data into data/ref/ (skips files already present)."""
from pathlib import Path

import requests

REF = Path(__file__).resolve().parent.parent / "data" / "ref"
BASE = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson"
FILES = ["ne_10m_admin_0_countries.geojson", "ne_10m_populated_places_simple.geojson"]

if __name__ == "__main__":
    REF.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        path = REF / name
        if path.exists():
            continue
        r = requests.get(f"{BASE}/{name}", timeout=120)
        r.raise_for_status()
        path.write_bytes(r.content)
        print(f"downloaded {name}")
