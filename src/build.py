"""Combine traced zones and advice texts into the static web app's data files.

  web/data/zones.geojson   advisory zones (simplified), one feature per level/city
  web/data/countries.json  per country: name, links, dates, summary + regional risks
"""
import html
import json
import re
from html.parser import HTMLParser
from pathlib import Path

from shapely.geometry import box, mapping, shape
from shapely.ops import polylabel

from geo import all_land, country_shape

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
TRACED = ROOT / "data" / "traced"
WEB = ROOT / "web" / "data"
SIMPLIFY_DEG = 0.004  # ~400 m, invisible at the zoom levels the map allows

ALLOWED = {"p", "ul", "ol", "li", "h2", "h3", "h4", "strong", "em", "b", "i", "br", "a"}


class Sanitizer(HTMLParser):
    """Keep a small allow-list of tags; links keep only an http(s) href."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []

    def handle_starttag(self, tag, attrs):
        if tag not in ALLOWED:
            return
        if tag == "a":
            href = dict(attrs).get("href") or ""
            if not href.startswith(("http://", "https://")):
                return self.out.append("<a>")
            return self.out.append(f'<a href="{html.escape(href)}" target="_blank" rel="noopener">')
        self.out.append(f"<{tag}>")

    def handle_endtag(self, tag):
        if tag in ALLOWED and tag != "br":
            self.out.append(f"</{tag}>")

    def handle_data(self, data):
        self.out.append(html.escape(data))


def clean_html(s):
    p = Sanitizer()
    p.feed(s or "")
    return re.sub(r"\s+", " ", "".join(p.out)).strip()


def raw_block(advice, title_part):
    for cat in advice.get("content", []):
        for b in cat.get("contentblocks", []):
            if title_part in b.get("paragraphtitle", "").lower():
                return b["paragraph"]
    return None


def block(advice, title_part):
    raw = raw_block(advice, title_part)
    return clean_html(raw) if raw else None


def by_level(regional_html):
    """Split 'Regionale risico's' at its '<h4>Rood: niet reizen</h4>'-style headings."""
    out = {}
    if not regional_html:
        return out
    parts = re.split(r"(<h\d[^>]*>.*?</h\d>)", regional_html, flags=re.S)
    current = None
    for part in parts:
        heading = re.match(r"<h\d[^>]*>\s*(\w+)", part)
        if heading and heading[1].lower() in LEVEL_WORDS:
            current = LEVEL_WORDS[heading[1].lower()]
            out[current] = ""
        elif current is not None:
            out[current] += part
    return {lvl: clean_html(html_) for lvl, html_ in out.items() if clean_html(html_)}


LEVEL_WORDS = {"groen": 1, "geel": 2, "oranje": 3, "rood": 4}


def text_levels(advice):
    """Colour codes named in the summary: a cheap check that the map is current."""
    t = re.sub(r"<[^>]+>", " ", html.unescape(advice.get("introduction") or "")).lower()
    return sorted({v for k, v in LEVEL_WORDS.items() if re.search(rf"\b{k}\b", t)})


def publishable(iso, zones):
    """Simplify and round traced zones into the features the app (and history) uses."""
    out = []
    for z in zones:
        geom = shape(z["geometry"]).simplify(SIMPLIFY_DEG, preserve_topology=True)
        if geom.is_empty:
            continue
        props = {"iso": iso, "level": z["properties"]["level"]}
        if z["properties"].get("city"):
            props["city"] = z["properties"]["city"]
        out.append({"type": "Feature", "properties": props, "geometry": _round(mapping(geom))})
    return out


def main():
    WEB.mkdir(parents=True, exist_ok=True)
    index = {c["iso"]: c for c in json.loads((RAW / "index.json").read_text())}
    features, countries = [], {}
    for iso, info in sorted(index.items()):
        advice = json.loads((RAW / iso / "advice.json").read_text())
        path = TRACED / f"{iso}.geojson"
        zones = publishable(iso, json.loads(path.read_text())["features"] if path.exists() else [])
        features += zones
        levels = {z["properties"]["level"] for z in zones}

        countries[iso] = {
            "name": advice["location"],
            "url": advice["canonical"],
            "lastmodified": advice.get("lastmodified"),
            "map_file": info.get("map_file"),
            "levels": sorted(levels),
            "summary": clean_html(advice.get("introduction")),
            "regional": block(advice, "regionale"),
            "by_level": by_level(raw_block(advice, "regionale")),
            "current": block(advice, "actueel"),
            "text_levels": text_levels(advice),
        }
        countries[iso]["mismatch"] = bool(countries[iso]["text_levels"]) and \
            countries[iso]["text_levels"] != countries[iso]["levels"]

    (WEB / "zones.geojson").write_text(json.dumps(
        {"type": "FeatureCollection", "features": features}, separators=(",", ":")))
    (WEB / "countries.json").write_text(json.dumps({
        # newest advice change, not build time, so unchanged data gives an identical file
        "generated": max(c["lastmodified"] or "" for c in countries.values()),
        "countries": countries,
    }, ensure_ascii=False, separators=(",", ":")))
    write_background()
    write_labels(countries)
    size = (WEB / "zones.geojson").stat().st_size / 1e6
    for iso, c in countries.items():
        if c["mismatch"]:
            print(f"  CHECK {iso}: map levels {c['levels']} != text levels {c['text_levels']}")
    print(f"{len(countries)} countries, {len(features)} zone features, zones.geojson {size:.1f} MB")


SHORT_NAMES = {
    "COD": "DR Congo",
    "COG": "Congo",
    "CAF": "Centraal-Afr. Rep.",
    "SWZ": "Eswatini",
    "STP": "Sao Tomé",
    "GNQ": "Equat.-Guinea",
}


def write_labels(countries):
    """Country names (shown zoomed out) and city names (shown zoomed in)."""
    labels = {"countries": [], "cities": []}
    # largest countries first: they win when labels collide
    for iso in sorted(countries, key=lambda i: -country_shape(i).area):
        geom = country_shape(iso)
        main = max(getattr(geom, "geoms", [geom]), key=lambda g: g.area)
        pt = polylabel(main, tolerance=0.05)
        labels["countries"].append({"iso": iso, "name": SHORT_NAMES.get(iso, countries[iso]["name"]),
                                    "lon": round(pt.x, 3), "lat": round(pt.y, 3)})
    codes = set(countries) | {"SOL", "SAH", "SDS"}
    places = json.loads((ROOT / "data" / "ref" / "ne_10m_populated_places_simple.geojson").read_text())
    for f in places["features"]:
        p = f["properties"]
        if p["adm0_a3"] not in codes or p["min_zoom"] > 8:
            continue
        lon, lat = f["geometry"]["coordinates"]
        labels["cities"].append({
            "name": p["name"], "lon": round(lon, 3), "lat": round(lat, 3),
            # zoom from which the label shows; capitals first
            "z": 4.5 if p["adm0cap"] else max(5, p["min_zoom"]),
            "cap": bool(p["adm0cap"]),
        })
    # Natural Earth has a few duplicates of capital names (e.g. a 'Niamey' in Nigeria)
    capitals = {c["name"] for c in labels["cities"] if c["cap"]}
    labels["cities"] = [c for c in labels["cities"] if c["cap"] or c["name"] not in capitals]
    labels["cities"].sort(key=lambda c: (c["z"], not c["cap"]))
    (WEB / "labels.json").write_text(json.dumps(labels, ensure_ascii=False, separators=(",", ":")))


def write_background():
    """Simplified land of the surrounding region, used instead of a tile basemap."""
    view = box(-45, -50, 80, 50)
    feats = []
    for _, geom in sorted(all_land().items()):
        g = geom.intersection(view)
        if g.is_empty:
            continue
        g = g.simplify(0.02, preserve_topology=True)
        if not g.is_empty:
            feats.append({"type": "Feature", "properties": {}, "geometry": _round(mapping(g), 3)})
    (WEB / "land.geojson").write_text(json.dumps(
        {"type": "FeatureCollection", "features": feats}, separators=(",", ":")))


def _round(geom, nd=4):
    def r(c):
        if isinstance(c[0], (int, float)):
            return [round(c[0], nd), round(c[1], nd)]
        return [r(x) for x in c]
    if geom["type"] == "GeometryCollection":
        return {"type": "GeometryCollection", "geometries": [_round(g, nd) for g in geom["geometries"]]}
    return {"type": geom["type"], "coordinates": r(geom["coordinates"])}


if __name__ == "__main__":
    main()
