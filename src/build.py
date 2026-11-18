"""Combine traced zones and advice texts into the static web app's data files.

  web/data/zones.geojson   advisory zones (simplified), one feature per level/city
  web/data/countries.json  per country: name, links, dates, summary + regional risks
"""
import html
import json
import re
from html.parser import HTMLParser
from pathlib import Path

from shapely.geometry import mapping, shape

from shapely.geometry import box

from geo import all_land, country_shape

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
TRACED = ROOT / "data" / "traced"
OVERRIDES = ROOT / "overrides"
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


def block(advice, title_part):
    for cat in advice.get("content", []):
        for b in cat.get("contentblocks", []):
            if title_part in b.get("paragraphtitle", "").lower():
                return clean_html(b["paragraph"])
    return None


LEVEL_WORDS = {"groen": 1, "geel": 2, "oranje": 3, "rood": 4}


def text_levels(advice):
    """Colour codes named in the summary: a cheap check that the map is current."""
    t = re.sub(r"<[^>]+>", " ", html.unescape(advice.get("introduction") or "")).lower()
    return sorted({v for k, v in LEVEL_WORDS.items() if re.search(rf"\b{k}\b", t)})


def load_override(iso):
    p = OVERRIDES / f"{iso}.json"
    return json.loads(p.read_text()) if p.exists() else None


def main():
    WEB.mkdir(parents=True, exist_ok=True)
    index = {c["iso"]: c for c in json.loads((RAW / "index.json").read_text())}
    features, countries = [], {}
    for iso, info in sorted(index.items()):
        advice = json.loads((RAW / iso / "advice.json").read_text())
        override = load_override(iso)
        if override and "level" in override:
            # whole-country override, e.g. when the map is missing or known stale
            zones = [{"properties": {"iso": iso, "level": override["level"]},
                      "geometry": mapping(country_shape(iso))}]
        else:
            path = TRACED / f"{iso}.geojson"
            zones = json.loads(path.read_text())["features"] if path.exists() else []
        report_path = TRACED / f"{iso}.json"
        report = json.loads(report_path.read_text()) if report_path.exists() else {}

        levels = set()
        for z in zones:
            geom = shape(z["geometry"]).simplify(SIMPLIFY_DEG, preserve_topology=True)
            if geom.is_empty:
                continue
            props = {"iso": iso, "level": z["properties"]["level"]}
            if z["properties"].get("city"):
                props["city"] = z["properties"]["city"]
            levels.add(props["level"])
            features.append({"type": "Feature", "properties": props,
                             "geometry": _round(mapping(geom))})

        countries[iso] = {
            "name": advice["location"],
            "url": advice["canonical"],
            "lastmodified": advice.get("lastmodified"),
            "map_file": info.get("map_file"),
            "levels": sorted(levels),
            "summary": clean_html(advice.get("introduction")),
            "regional": block(advice, "regionale"),
            "current": block(advice, "actueel"),
            "method": "override" if override and "level" in override else report.get("method"),
            "fit_iou": report.get("iou"),
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
    size = (WEB / "zones.geojson").stat().st_size / 1e6
    for iso, c in countries.items():
        if c["mismatch"]:
            print(f"  CHECK {iso}: map levels {c['levels']} != text levels {c['text_levels']}")
    print(f"{len(countries)} countries, {len(features)} zone features, zones.geojson {size:.1f} MB")


def write_background():
    """Simplified land of the surrounding region, used instead of a tile basemap."""
    view = box(-45, -50, 80, 50)
    feats = []
    for code, geom in sorted(all_land().items()):
        g = geom.intersection(view)
        if g.is_empty:
            continue
        g = g.simplify(0.02, preserve_topology=True)
        if not g.is_empty:
            feats.append({"type": "Feature", "properties": {"a3": code}, "geometry": _round(mapping(g), 3)})
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
