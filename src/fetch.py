"""Download travel advice (JSON) and advice maps (PNG) for African countries.

Raw data lands in data/raw/<ISO3>/:
  advice.json   full travel advice document from the open-data API
  map.png       the 'legend' map image (if the advice has one)
A per-run index is written to data/raw/index.json.
"""
import json
import sys
import time
from pathlib import Path

import requests

from countries import AFRICA_ISO3

API = "https://opendata.nederlandwereldwijd.nl/v2/sources/nederlandwereldwijd/infotypes"
RAW = Path(__file__).resolve().parent.parent / "data" / "raw"

session = requests.Session()
session.headers["User-Agent"] = "africa-travel-map/0.1 (+https://github.com/)"


def get(url, **params):
    for attempt in range(4):
        try:
            r = session.get(url, params=params, timeout=60)
            r.raise_for_status()
            return r
        except requests.RequestException:
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)


def list_countries():
    out, offset = [], 0
    while True:
        page = get(f"{API}/countries", output="json", rows=200, offset=offset).json()
        out += page
        offset += len(page)
        if len(page) < 200:
            return out


def fetch_country(iso, key, force=False):
    d = RAW / iso
    d.mkdir(parents=True, exist_ok=True)
    advice = get(f"{API}/countries/{key}/traveladvice", output="json").json()
    if isinstance(advice, list):
        advice = advice[0]
    old = d / "advice.json"
    prev = json.loads(old.read_text()) if old.exists() else {}
    changed = prev.get("lastmodified") != advice.get("lastmodified")
    old.write_text(json.dumps(advice, ensure_ascii=False, indent=1))

    legend = next((f for f in advice.get("files", []) if (f.get("mapType") or f.get("maptype")) == "legend"), None)
    map_path = d / "map.png"
    if legend and (force or changed or not map_path.exists()):
        map_path.write_bytes(get(legend["fileurl"]).content)
    return {
        "iso": iso,
        "key": key,
        "name": advice.get("location"),
        "lastmodified": advice.get("lastmodified"),
        "map_file": legend.get("filename") if legend else None,
        "map_modified": legend.get("fileModifiedDate") if legend else None,
        "has_map": map_path.exists(),
        "changed": changed,
    }


def main(force=False):
    RAW.mkdir(parents=True, exist_ok=True)
    countries = list_countries()
    (RAW / "countries.json").write_text(json.dumps(countries, ensure_ascii=False, indent=1))
    by_iso = {c["isocode"]: c for c in countries}
    index = []
    for iso in AFRICA_ISO3:
        c = by_iso.get(iso)
        if not c:
            print(f"  {iso}: not in API, skipped")
            continue
        try:
            info = fetch_country(iso, c["locationkey"], force)
        except requests.RequestException as e:
            print(f"  {iso}: FAILED {e}")
            continue
        flag = "changed" if info["changed"] else "same"
        print(f"  {iso} {info['name']:<32} {info['lastmodified']}  map={info['map_file']}  [{flag}]")
        index.append(info)
    (RAW / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1))
    print(f"{len(index)} countries, {sum(i['changed'] for i in index)} changed")


if __name__ == "__main__":
    main(force="--force" in sys.argv)
