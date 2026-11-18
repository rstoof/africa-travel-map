"""Version history of the advisory zones.

A country gets a new snapshot only when its zones change. Everything the web app
needs is written under web/data/history/:

  index.json                  snapshots per country, change events, text updates
  <ISO>/<date>.geojson        zones of that country from <date> on
  <ISO>/diff_<a>_<b>.geojson  areas whose level differs between two snapshots
  <ISO>/text_<date>.json      advice text as of <date> (kept from the first run on)

and web/feed.xml, an Atom feed of the changes.

Usage:
  history.py record [--date YYYY-MM-DD]   snapshot web/data (run after build.py)
  history.py backfill <maps_dir> <date>   trace old <locationkey>_legend.png maps as of <date>
"""
import json
import re
import sys
from datetime import date as Date, datetime, timezone
from html import escape
from itertools import combinations
from os import environ
from pathlib import Path

from pyproj import Transformer
from shapely.geometry import mapping, shape
from shapely.ops import transform as shp_transform, unary_union

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
HIST = WEB / "data" / "history"
RAW = ROOT / "data" / "raw"

MIN_PIECE_KM2 = 25     # smaller differences are tracing noise
MIN_CHANGE_KM2 = 50    # a country needs at least this much change for a new snapshot
LEVEL_NAMES = {1: "groen", 2: "geel", 3: "oranje", 4: "rood"}

_EQUAL_AREA = Transformer.from_crs(4326, 6933, always_xy=True)


def km2(geom):
    return shp_transform(_EQUAL_AREA.transform, geom).area / 1e6


def by_level(feats):
    groups = {}
    for f in feats:
        groups.setdefault(f["properties"]["level"], []).append(shape(f["geometry"]))
    return {lvl: unary_union(gs) for lvl, gs in groups.items()}


def diff(old_feats, new_feats):
    """Pieces whose level changed, as features with from/to/area properties."""
    old, new = by_level(old_feats), by_level(new_feats)
    out = []
    for a, ga in old.items():
        for b, gb in new.items():
            if a == b:
                continue
            piece = ga.intersection(gb)
            if piece.is_empty or piece.buffer(-0.01).is_empty:
                continue  # empty or a sliver along a zone border
            area = km2(piece)
            if area < MIN_PIECE_KM2:
                continue
            out.append({"type": "Feature",
                        "properties": {"from": a, "to": b, "km2": round(area)},
                        "geometry": mapping(piece.simplify(0.002))})
    return out


def load_index():
    p = HIST / "index.json"
    if p.exists():
        return json.loads(p.read_text())
    return {"snapshots": {}, "lastmodified": {}, "text_updates": []}


def save_snapshot(index, iso, day, feats, **meta):
    (HIST / iso).mkdir(parents=True, exist_ok=True)
    (HIST / iso / f"{day}.geojson").write_text(json.dumps(
        {"type": "FeatureCollection", "features": feats}, separators=(",", ":")))
    snaps = [s for s in index["snapshots"].get(iso, []) if s["date"] != day]
    snaps.append({"date": day, "levels": sorted({f["properties"]["level"] for f in feats}), **meta})
    index["snapshots"][iso] = sorted(snaps, key=lambda s: s["date"])


def save_text(index, iso, c):
    """Keep each version of the advice text, dated by its lastmodified."""
    if not c.get("lastmodified"):
        return
    texts = index.setdefault("texts", {}).setdefault(iso, [])
    if texts and texts[-1]["lastmodified"] == c["lastmodified"]:
        return
    day = c["lastmodified"][:10]
    (HIST / iso).mkdir(parents=True, exist_ok=True)
    (HIST / iso / f"text_{day}.json").write_text(json.dumps(
        {k: c.get(k) for k in ("lastmodified", "summary", "regional", "by_level")},
        ensure_ascii=False, separators=(",", ":")))
    texts[:] = [t for t in texts if t["date"] != day] + [{"date": day, "lastmodified": c["lastmodified"]}]
    texts.sort(key=lambda t: t["date"])


def snapshot_feats(iso, day):
    return json.loads((HIST / iso / f"{day}.geojson").read_text())["features"]


def changed_km2(old_feats, new_feats):
    return sum(f["properties"]["km2"] for f in diff(old_feats, new_feats))


def map_date(map_file):
    """Date in the ministry's map file name, e.g. Reisadvies_Mali_12-11-2026.png."""
    m = re.search(r"(\d{2})-(\d{2})-(\d{4})", map_file or "")
    if not m:
        return None
    try:
        return Date(int(m[3]), int(m[2]), int(m[1])).isoformat()
    except ValueError:
        return None


def record(index, observed):
    """Snapshot changed countries; returns [(iso, date)] of new changes."""
    new = []
    zones = json.loads((WEB / "data" / "zones.geojson").read_text())["features"]
    countries = json.loads((WEB / "data" / "countries.json").read_text())["countries"]
    current = {}
    for f in zones:
        current.setdefault(f["properties"]["iso"], []).append(f)

    for iso, c in countries.items():
        feats = current.get(iso, [])
        snaps = index["snapshots"].get(iso, [])
        if feats:
            last = snaps[-1] if snaps else None
            if last is None or (set(last["levels"]) != {f["properties"]["level"] for f in feats}
                                or changed_km2(snapshot_feats(iso, last["date"]), feats) >= MIN_CHANGE_KM2):
                # date the change by the map's own file name when that is plausible;
                # otherwise it happened somewhere between the last snapshot and now
                baseline = min((s["date"] for v in index["snapshots"].values() for s in v), default=observed)
                floor = last["date"] if last else baseline
                md = map_date(c.get("map_file"))
                if md and md <= observed and md > floor:
                    day, dated_by = md, "map"
                elif last is None:
                    day, dated_by = min(baseline, observed), "baseline"
                else:
                    day, dated_by = observed, "observed"
                save_snapshot(index, iso, day, feats, observed=observed, dated_by=dated_by,
                              after=floor if dated_by == "observed" else None,
                              map_file=c.get("map_file"))
                if last is not None:
                    new.append((iso, day))
                print(f"  {iso}: new snapshot {day}")

        save_text(index, iso, c)

        prev = index["lastmodified"].get(iso)
        if prev and c["lastmodified"] and c["lastmodified"] != prev:
            index["text_updates"].append({"date": c["lastmodified"][:10], "iso": iso})
        if c["lastmodified"]:
            index["lastmodified"][iso] = c["lastmodified"]
    return new


def backfill(index, maps_dir, day):
    from build import publishable
    from trace import trace

    keys = {c["iso"]: c["key"] for c in json.loads((RAW / "index.json").read_text())}
    for iso, key in sorted(keys.items()):
        path = Path(maps_dir) / f"{key}_legend.png"
        if not path.exists():
            continue
        feats, _ = trace(iso, path)
        save_snapshot(index, iso, day, publishable(iso, feats), observed=day, source="backfill")
        print(f"  {iso}: backfilled {day}")


def finalize(index, names):
    """Write pairwise diffs, derive change events, write index and feed."""
    events = []
    for iso, snaps in index["snapshots"].items():
        dates = [s["date"] for s in snaps]
        feats = {d: snapshot_feats(iso, d) for d in dates}
        for a, b in combinations(dates, 2):
            d = diff(feats[a], feats[b])
            (HIST / iso / f"diff_{a}_{b}.geojson").write_text(json.dumps(
                {"type": "FeatureCollection", "features": d}, separators=(",", ":")))
        for a, b in zip(dates, dates[1:]):
            pieces = json.loads((HIST / iso / f"diff_{a}_{b}.geojson").read_text())["features"]
            p = [x["properties"] for x in pieces]
            snap_b = next(s for s in snaps if s["date"] == b)
            events.append({
                "date": b, "iso": iso, "name": names.get(iso, iso),
                "prev": a,
                "after": snap_b.get("after"),  # set when the exact date is unknown
                "worse_km2": sum(x["km2"] for x in p if x["to"] > x["from"]),
                "better_km2": sum(x["km2"] for x in p if x["to"] < x["from"]),
                "transitions": sorted({(x["from"], x["to"]) for x in p}),
            })
    index["events"] = sorted(events, key=lambda e: (e["date"], e["iso"]), reverse=True)
    index["text_updates"] = sorted({(t["date"], t["iso"]) for t in index["text_updates"]}, reverse=True)
    index["text_updates"] = [{"date": d, "iso": i} for d, i in index["text_updates"]]
    index["dates"] = sorted({s["date"] for snaps in index["snapshots"].values() for s in snaps})
    (HIST / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1))
    write_feed(index)


def describe(e):
    parts = [f"{LEVEL_NAMES[a]} → {LEVEL_NAMES[b]}" for a, b in e["transitions"]]
    return f"{e['name']}: {', '.join(parts)}" if parts else f"{e['name']}: kaart aangepast"


def write_feed(index):
    site = environ.get("SITE_URL", "")
    entries = []
    for e in index["events"][:100]:
        url = f"{site}#{e['iso']}"
        detail = f"Strenger: {e['worse_km2']:,} km². Milder: {e['better_km2']:,} km².".replace(",", ".")
        entries.append(f"""  <entry>
    <id>tag:africa-travel-map,{e['date']}:{e['iso']}</id>
    <title>{escape(describe(e))}</title>
    <updated>{e['date']}T00:00:00Z</updated>
    <link href="{escape(url)}"/>
    <summary>{escape(detail)}</summary>
  </entry>""")
    updated = index["events"][0]["date"] if index["events"] else "1970-01-01"
    (WEB / "feed.xml").write_text(f"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Reisadvies Afrika: gewijzigde kleurcodes</title>
  <id>tag:africa-travel-map,2026:feed</id>
  <updated>{updated}T00:00:00Z</updated>
  <link href="{escape(site)}"/>
{chr(10).join(entries)}
</feed>
""")


def write_report(index, new, path):
    """Markdown summary of new changes, used by CI to open an issue."""
    site = environ.get("SITE_URL", "")
    lines = []
    for e in index["events"]:
        if (e["iso"], e["date"]) in new:
            lines.append(f"- **{describe(e)}** ({e['date']}): {e['worse_km2']:,} km² stricter, "
                         f"{e['better_km2']:,} km² milder. [map]({site}#wijzigingen/"
                         f"{e['prev']}/{e['date']}) · [advice]({site}#{e['iso']})")
    path.write_text("Travel advice colour codes changed:\n\n" + "\n".join(lines) + "\n")


def main(argv):
    HIST.mkdir(parents=True, exist_ok=True)
    index = load_index()
    if argv and argv[0] == "backfill":
        backfill(index, argv[1], argv[2])
    else:
        observed = argv[argv.index("--date") + 1] if "--date" in argv else \
            datetime.now(timezone.utc).date().isoformat()
        new = record(index, observed)
    names = {iso: c["name"] for iso, c in
             json.loads((WEB / "data" / "countries.json").read_text())["countries"].items()}
    finalize(index, names)
    report = ROOT / "data" / "changes.md"
    report.unlink(missing_ok=True)
    if argv[:1] != ["backfill"] and new:
        write_report(index, new, report)
    print(f"{len(index['events'])} change events, {len(index['text_updates'])} text updates, "
          f"dates: {', '.join(index['dates'])}")


if __name__ == "__main__":
    main(sys.argv[1:])
