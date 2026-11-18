"""Render data/traced/*.geojson into a quick-look PNG (QA only)."""
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from rasterio import features
from rasterio.transform import from_bounds
from shapely.geometry import shape

from geo import all_land
from trace import PALETTE

ROOT = Path(__file__).resolve().parent.parent


def render(paths, out, bounds=(-26, -36, 64, 39), width=1800):
    w, s, e, n = bounds
    height = int(width * (n - s) / (e - w))
    aff = from_bounds(w, s, e, n, width, height)
    img = np.full((height, width, 3), (200, 225, 240), np.uint8)
    land = features.rasterize(all_land().values(), (height, width), transform=aff)
    img[land > 0] = (225, 225, 225)
    for p in paths:
        for f in json.loads(Path(p).read_text())["features"]:
            m = features.rasterize([shape(f["geometry"])], (height, width), transform=aff)
            img[m > 0] = PALETTE[f["properties"]["level"]]
    # country outlines
    edges = features.rasterize([g.boundary for g in all_land().values()], (height, width), transform=aff)
    img[edges > 0] = (90, 90, 90)
    Image.fromarray(img).save(out)


if __name__ == "__main__":
    paths = sorted((ROOT / "data" / "traced").glob("*.geojson"))
    render(paths, sys.argv[1] if len(sys.argv) > 1 else ROOT / "data" / "preview.png")
