# Reisadvies Afrika — interactive map

An interactive map of the Dutch government's travel advice (*reisadvies*) for every African country,
**per region**, built automatically from the
[Nederland Wereldwijd open-data API](https://opendata.nederlandwereldwijd.nl/).

The ministry publishes one PNG map per country. This project turns those 55 images into a single
vector map (GeoJSON) and a static web app. Click a region to see its colour code, the summary, and the
regional risks text, with a link to the official advice.

> Unofficial. The zones are traced automatically and can be off. The text on
> [nederlandwereldwijd.nl](https://www.nederlandwereldwijd.nl/reisadvies) is always leading.

## How it works

```
fetch.py   API ──► data/raw/<ISO>/advice.json + map.png
trace.py   map.png ──► data/traced/<ISO>.geojson   (+ <ISO>.json fit report)
build.py   traced + advice ──► web/data/{zones.geojson, countries.json, land.geojson}
web/       static Leaflet app, no API keys, no tile server
```

**Tracing** (`src/trace.py`), per country:

1. Classify pixels against the fixed map palette (red `#ff0000`, orange `#ffa000`, yellow `#ffff00`, green `#7feb00`).
2. Remove legend swatches and 1–2 px strokes (roads and rivers drawn in red); real zones, including
   "40 km from the border" strips, survive.
3. Detect **city exceptions**: small outlined markers filled with a different colour than their
   surroundings ("Kano state is red, except the city of Kano"). They become 8 km circles named after
   the nearest Natural Earth place.
4. Fit the image onto the Natural Earth outline (scale + offset, Web Mercator or plate carrée, chosen by
   best overlap). Current fits: 91–99.6 % IoU, median ~99 %.
5. Every pixel inside the true outline takes the level of the nearest coloured pixel (fills borders,
   labels and misfit), then the result is vectorized and clipped to the outline.

Single-colour countries skip the fit and use the outline directly.

**Checks** (`src/build.py`): the colours named in each advice summary are compared to the traced
colours. A mismatch (usually a map that lags behind a text update) is printed during the build and shown as a
warning in the app. At the time of writing all 55 countries match.

## Run it

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
make all        # ref data, fetch, trace, build  (~1–2 min)
make preview    # data/preview.png, a static quick-look of all zones
make serve      # http://localhost:8000
```

Open `http://localhost:8000/#NGA` to deep-link a country (ISO3 code).

## Automation

`.github/workflows/update.yml` runs daily: fetch → trace → build, commits changed data, and deploys
`web/` to GitHub Pages. Enable Pages with source **GitHub Actions** in the repository settings.

## Overrides

If a map is missing or clearly stale, put `overrides/<ISO3>.json` with `{"level": 2}` to colour the
whole country manually. See [overrides/README.md](overrides/README.md).

## Known limitations

- Exceptions that the map doesn't draw (e.g. specific neighbourhoods of Nairobi or Mombasa) are only in the text.
- Lakes are part of the country outline in Natural Earth and get coloured.
- Territories without their own advice (Western Sahara, Mayotte, St Helena) are not shown separately;
  Western Sahara is covered by the Morocco advice map.

## Sources

- Travel advice and maps: Ministerie van Buitenlandse Zaken, via the Nederland Wereldwijd open-data API.
- Boundaries and places: [Natural Earth](https://www.naturalearthdata.com/) (public domain).
