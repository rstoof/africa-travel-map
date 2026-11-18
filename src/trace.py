"""Trace advisory zones from the official map PNGs into GeoJSON polygons.

Per country:
  1. classify pixels into advisory levels using the fixed map palette
  2. drop the legend swatches and build a country mask
  3. fit the PNG onto the Natural Earth outline (scale + offset, in Web
     Mercator or plate carree, whichever overlaps best)
  4. rasterize the true outline in pixel space and give every pixel inside it
     the level of the nearest coloured pixel (fills borders, labels, misfit)
  5. vectorize, reproject to WGS84 and clip to the true outline

Output: data/traced/<ISO3>.geojson and data/traced/<ISO3>.json (fit report).
"""
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from pyproj import Transformer
from rasterio import features
from rasterio.transform import Affine
from scipy import ndimage, optimize
from shapely import make_valid
from shapely.geometry import Point, mapping, shape
from shapely import affinity
from shapely.ops import transform as shp_transform, unary_union

from geo import country_shape, nearest_place

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "traced"

# level -> RGB as used on the official maps
PALETTE = {
    1: (127, 235, 0),   # groen: geen bijzondere veiligheidsrisico's
    2: (255, 255, 0),   # geel: let op, veiligheidsrisico's
    3: (255, 160, 0),   # oranje: alleen noodzakelijke reizen
    4: (255, 0, 0),     # rood: niet reizen
}
MIN_ZONE_PX = 12  # smaller blobs are anti-aliasing noise, not zones
CITY_RADIUS_KM = 8  # city markers become circles of this radius

TO_MERC = Transformer.from_crs(4326, 3857, always_xy=True)
FROM_MERC = Transformer.from_crs(3857, 4326, always_xy=True)


def classify(img):
    """Return an int8 grid: 0 = not an advisory colour, 1..4 = level."""
    rgb = img.astype(np.int16)
    best = np.full(rgb.shape[:2], 1e9)
    labels = np.zeros(rgb.shape[:2], np.int8)
    for level, col in PALETTE.items():
        d = np.abs(rgb - np.array(col)).sum(axis=2)
        hit = (d < 60) & (d < best)
        labels[hit] = level
        best = np.where(hit, d, best)
    return labels


def drop_legend(labels):
    """Remove legend swatches: small, perfectly rectangular colour blocks."""
    out = labels.copy()
    comp, n = ndimage.label(labels > 0)
    for i, sl in enumerate(ndimage.find_objects(comp), start=1):
        h, w = sl[0].stop - sl[0].start, sl[1].stop - sl[1].start
        area = (comp[sl] == i).sum()
        if h <= 30 and w <= 50 and area >= 0.9 * h * w and h * w >= 60:
            out[sl][comp[sl] == i] = 0
    # title bar and footer never contain zones
    out[:30] = 0
    out[-25:] = 0
    return out


def drop_lines(labels):
    """Remove 1-2px wide strokes (roads/rivers drawn in red, anti-aliasing).

    Real zones, including border strips, are at least ~4px wide on these maps.
    """
    out = np.zeros_like(labels)
    for level in PALETTE:
        out[ndimage.binary_opening(labels == level, structure=np.ones((3, 3)))] = level
    return out


def find_markers(labels, img):
    """Find city markers filled with a different level than their surroundings.

    The maps mark exceptions like 'Kano state is red, except Kano city' with a
    small orange-filled circle/square with a dark outline. Returns [(row, col, level)] and a copy of
    labels with the marker pixels cleared.
    """
    out = labels.copy()
    markers = []
    for level in PALETTE:
        comp, n = ndimage.label(labels == level)
        for i, sl in enumerate(ndimage.find_objects(comp), start=1):
            h, w = sl[0].stop - sl[0].start, sl[1].stop - sl[1].start
            blob = comp == i
            area = blob[sl].sum()
            if not (4 <= h <= 18 and 4 <= w <= 18 and area >= 0.5 * h * w and area >= 12):
                continue
            edge = ndimage.binary_dilation(blob, iterations=2) & ~blob
            if (img[edge].max(axis=1) < 110).mean() < 0.35:
                continue  # no dark outline: a small zone, not a marker
            near = ndimage.binary_dilation(blob, iterations=6) & ~ndimage.binary_dilation(blob, iterations=2)
            around = labels[near]
            around = around[around > 0]
            if around.size < 20:
                continue
            surround = np.bincount(around).argmax()
            if surround != level and (around == surround).mean() > 0.7:
                r, c = ndimage.center_of_mass(blob)
                markers.append((float(r), float(c), level))
                out[ndimage.binary_dilation(blob, iterations=2)] = 0
    return markers, out


def city_circle(lon, lat, km=CITY_RADIUS_KM):
    r = km / 111.32
    return affinity.scale(Point(lon, lat).buffer(r, 32), xfact=1 / np.cos(np.radians(lat)), yfact=1)


def country_mask(labels):
    m = ndimage.binary_closing(labels > 0, iterations=3)
    m = ndimage.binary_fill_holes(m)
    comp, n = ndimage.label(m)
    sizes = ndimage.sum(m, comp, range(1, n + 1))
    keep = np.isin(comp, 1 + np.flatnonzero(sizes >= MIN_ZONE_PX))
    return keep


def project(geom, crs):
    return shp_transform(TO_MERC.transform, geom) if crs == "merc" else geom


def raster_of(geom, params, shape_hw):
    sx, sy, tx, ty = params
    # pixel (col,row) -> world: x = tx + col*sx, y = ty - row*sy
    aff = Affine(sx, 0, tx, 0, -sy, ty)
    return features.rasterize([(geom, 1)], out_shape=shape_hw, transform=aff,
                              all_touched=False, dtype=np.uint8).astype(bool)


def iou(a, b):
    u = (a | b).sum()
    return (a & b).sum() / u if u else 0.0


def fit(mask, geom):
    """Find scale/offset mapping pixels onto geom (in geom's planar crs)."""
    rows, cols = np.nonzero(mask)
    minx, miny, maxx, maxy = geom.bounds
    # init from bounding boxes of the mainland-sized parts
    sx0 = (maxx - minx) / (cols.max() - cols.min() + 1)
    sy0 = (maxy - miny) / (rows.max() - rows.min() + 1)
    x0 = [sx0, sy0, minx - cols.min() * sx0, maxy + rows.min() * sy0]

    def loss(p):
        return -iou(mask, raster_of(geom, p, mask.shape))

    # optimise in normalised units so Nelder-Mead steps are sensible
    scale = np.array([sx0, sy0, sx0 * 20, sy0 * 20])
    base = np.array(x0)
    f = lambda z: loss(base + z * scale)
    best = None
    for start in ([0, 0, 0, 0], [0.05, 0.05, 0, 0], [-0.05, -0.05, 0, 0]):
        r = optimize.minimize(f, start, method="Nelder-Mead",
                              options={"xatol": 1e-4, "fatol": 1e-5, "maxiter": 600})
        if best is None or r.fun < best.fun:
            best = r
    params = base + best.x * scale
    return params, -best.fun


def fill_levels(labels, inside):
    """Give each pixel inside the outline the level of its nearest coloured pixel."""
    known = labels > 0
    if not known.any():
        return np.zeros_like(labels)
    _, (ri, ci) = ndimage.distance_transform_edt(~known, return_indices=True)
    filled = labels[ri, ci]
    filled[~inside] = 0
    return filled


def clean_small(levels):
    """Merge tiny blobs into their surroundings (mode of the dilated ring)."""
    out = levels.copy()
    for level in PALETTE:
        comp, n = ndimage.label(out == level)
        if n == 0:
            continue
        sizes = ndimage.sum(np.ones_like(comp), comp, range(1, n + 1))
        for i in np.flatnonzero(sizes < MIN_ZONE_PX) + 1:
            blob = comp == i
            ring = ndimage.binary_dilation(blob) & ~blob & (out > 0)
            if ring.any():
                out[blob] = np.bincount(out[ring]).argmax()
    return out


def trace(iso, image_path=None):
    img = np.array(Image.open(image_path or RAW / iso / "map.png").convert("RGB"))
    labels = drop_legend(classify(img))
    mask = country_mask(labels)
    labels[~mask] = 0
    markers, labels = find_markers(labels, img)
    labels = drop_lines(labels)
    present = sorted(int(l) for l in np.unique(labels) if l)
    counts = {int(l): int((labels == l).sum()) for l in present}
    outline = country_shape(iso)
    report = {"iso": iso, "pixel_counts": counts}

    if len(present) <= 1 and not markers:
        level = present[0] if present else None
        report.update(method="single", levels=[level] if level else [])
        feats = [{"type": "Feature", "properties": {"iso": iso, "level": level},
                  "geometry": mapping(outline)}] if level else []
        return feats, report

    best = None
    for crs in ("merc", "plate"):
        g = project(outline, crs)
        params, score = fit(mask, g)
        if best is None or score > best[2]:
            best = (crs, params, score, g)
    crs, params, score, g = best
    inside = raster_of(g, params, mask.shape)
    levels = clean_small(fill_levels(labels, inside))

    sx, sy, tx, ty = params
    aff = Affine(sx, 0, tx, 0, -sy, ty)
    back = (lambda x, y: FROM_MERC.transform(x, y)) if crs == "merc" else (lambda x, y: (x, y))
    feats = []
    for level in PALETTE:
        polys = [shape(geom) for geom, v in
                 features.shapes(levels.astype(np.int16), mask=levels == level, transform=aff)
                 if v == level]
        if not polys:
            continue
        geom = shp_transform(back, unary_union(polys))
        geom = make_valid(geom).intersection(outline)
        if geom.is_empty:
            continue
        feats.append({"type": "Feature", "properties": {"iso": iso, "level": level},
                      "geometry": mapping(geom)})
    # city markers: cut a circle out of the zones and add it as its own feature
    cities = []
    for r, c, level in markers:
        lon, lat = back(*(aff * (c + 0.5, r + 0.5)))
        place = nearest_place(lon, lat, iso)
        circle = city_circle(lon, lat).intersection(outline)
        cities.append({"name": place, "level": level, "lon": round(lon, 4), "lat": round(lat, 4)})
        for f in feats:
            f["geometry"] = mapping(shape(f["geometry"]).difference(circle))
        feats.append({"type": "Feature", "properties": {"iso": iso, "level": level, "city": place},
                      "geometry": mapping(circle)})
    if cities:
        report["cities"] = cities
    # parts of the outline the image never covered (far islands): nearest zone
    covered = unary_union([shape(f["geometry"]) for f in feats])
    rest = outline.difference(covered.buffer(1e-6))
    if not rest.is_empty and rest.area > 0:
        report["uncovered_area_deg2"] = round(rest.area, 4)
    report.update(method="traced", crs=crs, iou=round(score, 4),
                  params=[float(p) for p in params],
                  levels=sorted({f["properties"]["level"] for f in feats}))
    return feats, report


def main(isos):
    OUT.mkdir(parents=True, exist_ok=True)
    for iso in isos:
        if not (RAW / iso / "map.png").exists():
            print(f"{iso}: no map")
            continue
        feats, report = trace(iso)
        (OUT / f"{iso}.geojson").write_text(json.dumps(
            {"type": "FeatureCollection", "features": feats}))
        (OUT / f"{iso}.json").write_text(json.dumps(report, indent=1))
        print(f"{iso}: {report['method']:<7} levels={report['levels']} "
              f"iou={report.get('iou', '-')} crs={report.get('crs', '-')}")


if __name__ == "__main__":
    isos = sys.argv[1:] or sorted(p.name for p in RAW.iterdir() if p.is_dir())
    main(isos)
