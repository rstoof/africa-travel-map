const LEVELS = {
  1: { name: "Groen", label: "Geen bijzondere veiligheidsrisico's", color: "#7feb00" },
  2: { name: "Geel", label: "Let op, veiligheidsrisico's", color: "#ffeb00" },
  3: { name: "Oranje", label: "Alleen noodzakelijke reizen", color: "#ffa000" },
  4: { name: "Rood", label: "Niet reizen", color: "#e8221b" },
};

const AFRICA = [[-35, -19], [38, 52]];
const map = L.map("map", {
  minZoom: 2.5,
  maxZoom: 9,
  maxBounds: [[-45, -40], [45, 75]],
  zoomSnap: 0.5,
}).fitBounds(AFRICA);
const dark = matchMedia("(prefers-color-scheme: dark)").matches;
map.attributionControl.addAttribution(
  'Bron: <a href="https://opendata.nederlandwereldwijd.nl/">Nederland Wereldwijd</a> · <a href="https://www.naturalearthdata.com/">Natural Earth</a>',
);

const $ = (id) => document.getElementById(id);
map.createPane("labels").style.zIndex = 450; // above zones, below tooltips
map.createPane("exceptions").style.zIndex = 460; // city-exception dots above labels

// Country names when zoomed out, city names (by Natural Earth min_zoom) when zoomed in.
const COUNTRY_LABELS_BELOW = 5;
let labelData = null;
const labelMarkers = new Map();
function labelMarker(key, lat, lon, cls, text) {
  if (!labelMarkers.has(key)) {
    const html = document.createElement("span");
    html.className = `lbl ${cls}`;
    html.textContent = text;
    labelMarkers.set(key, L.marker([lat, lon], {
      pane: "labels", interactive: false, keyboard: false,
      icon: L.divIcon({ className: "lbl-wrap", html, iconSize: null }),
    }));
  }
  return labelMarkers.get(key);
}
// Greedy placement: candidates in priority order, skip any that overlaps a placed label.
function updateLabels() {
  if (!labelData) return;
  const z = map.getZoom();
  const placed = [];
  const fits = (lat, lon, text, size, centred) => {
    const p = map.latLngToLayerPoint([lat, lon]);
    const w = text.length * size * 0.62 + 6;
    const x0 = centred ? p.x - w / 2 : p.x - 8;
    const box = [x0, p.y - size, x0 + w, p.y + size];
    if (placed.some((b) => box[0] < b[2] && box[2] > b[0] && box[1] < b[3] && box[3] > b[1])) return false;
    placed.push(box);
    return true;
  };
  for (const c of labelData.countries) {
    const m = labelMarker(`c:${c.iso}`, c.lat, c.lon, "lbl-country", c.name);
    z < COUNTRY_LABELS_BELOW && fits(c.lat, c.lon, c.name, 11, true) ? m.addTo(map) : m.remove();
  }
  for (const c of labelData.cities) {
    const m = labelMarker(`p:${c.name}:${c.lat}`, c.lat, c.lon, `lbl-city${c.cap ? " cap" : ""}`, c.name);
    z >= Math.max(c.z, COUNTRY_LABELS_BELOW) && fits(c.lat, c.lon, c.name, 11, false) ? m.addTo(map) : m.remove();
  }
}
map.on("zoomend", updateLabels);
const visible = new Set([1, 2, 3, 4]);
let countries = {};
let layer;
let selected = null;

const style = (f) => ({
  fillColor: LEVELS[f.properties.level].color,
  fillOpacity: visible.has(f.properties.level) ? 0.72 : 0,
  color: dark ? "#161a22" : "#ffffff",
  weight: f.properties.city ? 1.5 : 0.6,
  opacity: visible.has(f.properties.level) ? 1 : 0,
});

function zoneText(p) {
  const lvl = LEVELS[p.level];
  return p.city ? `${lvl.name} — stad ${p.city}` : `${lvl.name} — ${lvl.label.toLowerCase()}`;
}

function formatDate(iso) {
  return iso ? new Date(iso).toLocaleDateString("nl-NL", { day: "numeric", month: "long", year: "numeric" }) : "";
}

function whyText(c, level, byLevel = c.by_level) {
  if (byLevel && byLevel[level]) return byLevel[level];
  // single-colour countries have no regional section; their summary explains it
  return c.levels.length === 1 ? c.summary : "";
}

function showWhy(c, zone, byLevel) {
  const html = zone ? whyText(c, zone.level, byLevel) : "";
  $("d-why").hidden = !html;
  if (!html) return;
  const lvl = LEVELS[zone.level];
  $("d-why").style.borderColor = lvl.color;
  $("d-why-title").textContent = zone.city
    ? `Waarom ${lvl.name.toLowerCase()} voor ${zone.city}?`
    : `Waarom ${lvl.name.toLowerCase()}? ${lvl.label}`;
  // HTML is sanitised to an allow-list at build time (src/build.py)
  $("d-why-body").innerHTML = html;
}

function showCountry(iso, zone) {
  const c = countries[iso];
  if (!c) return;
  $("d-change").hidden = true;
  showWhy(c, zone);
  selected = iso;
  history.replaceState(null, "", `#${iso}`);
  $("intro").hidden = true;
  $("detail").hidden = false;
  $("d-name").textContent = c.name;
  const z = $("d-zone");
  if (zone) {
    z.hidden = false;
    z.style.background = LEVELS[zone.level].color;
    z.textContent = zoneText(zone);
  } else {
    z.hidden = true;
  }
  $("d-warn").hidden = !c.mismatch;
  $("d-date").textContent = `Laatst gewijzigd ${formatDate(c.lastmodified)}`;
  $("d-link").href = c.url;
  // HTML is sanitised to an allow-list at build time (src/build.py)
  $("d-summary").innerHTML = c.summary || "";
  $("d-regional").innerHTML = c.regional || "";
  $("d-regional-wrap").hidden = !c.regional;
  $("d-current").innerHTML = c.current || "";
  $("d-current-wrap").hidden = !c.current;
  $("d-hist-wrap").hidden = true;
  showCountryHistory(iso);
  $("panel").scrollTop = 0;
  if (!map.hasLayer(layer)) return; // changes mode draws its own layers
  layer.setStyle(style);
  layer.eachLayer((l) => {
    if (l.feature.properties.iso !== iso) return;
    l.setStyle({ color: dark ? "#ffffff" : "#1d2330", weight: l.feature.properties.city ? 1 : 2 });
    l.bringToFront();
  });
  // keep city circles above the zone they sit in
  layer.eachLayer((l) => l.feature.properties.iso === iso && l.feature.properties.city && l.bringToFront());
}

function closeDetail() {
  selected = null;
  $("detail").hidden = true;
  if (!map.hasLayer(layer)) {
    $("changes").hidden = false; // back to the list of changes
    return;
  }
  history.replaceState(null, "", location.pathname);
  $("intro").hidden = false;
  layer.setStyle(style);
}

function buildLegend(features) {
  const counts = { 1: new Set(), 2: new Set(), 3: new Set(), 4: new Set() };
  features.forEach((f) => counts[f.properties.level].add(f.properties.iso));
  const ul = $("legend");
  for (const lvl of [4, 3, 2, 1]) {
    const li = document.createElement("li");
    const btn = document.createElement("button");
    btn.setAttribute("aria-pressed", "true");
    btn.title = "Toon/verberg deze kleur";
    btn.innerHTML = `<span class="swatch" style="background:${LEVELS[lvl].color}"></span>
      <span><strong>${LEVELS[lvl].name}</strong> · ${LEVELS[lvl].label}</span>
      <span class="n" title="Aantal landen met deze kleur">${counts[lvl].size}</span>`;
    btn.onclick = () => {
      visible.has(lvl) ? visible.delete(lvl) : visible.add(lvl);
      btn.setAttribute("aria-pressed", visible.has(lvl));
      selected ? showCountry(selected) : layer.setStyle(style);
    };
    li.appendChild(btn);
    ul.appendChild(li);
  }
}

function buildSearch() {
  const list = $("country-list");
  const byName = {};
  Object.entries(countries)
    .sort((a, b) => a[1].name.localeCompare(b[1].name, "nl"))
    .forEach(([iso, c]) => {
      byName[c.name.toLowerCase()] = iso;
      const o = document.createElement("option");
      o.value = c.name;
      list.appendChild(o);
    });
  $("search").addEventListener("change", (e) => {
    const iso = byName[e.target.value.trim().toLowerCase()];
    if (!iso) return;
    focusCountry(iso);
    e.target.value = "";
  });
}

function focusCountry(iso) {
  const bounds = L.latLngBounds([]);
  layer.eachLayer((l) => l.feature.properties.iso === iso && bounds.extend(l.getBounds()));
  if (bounds.isValid()) map.fitBounds(bounds, { padding: [30, 30], maxZoom: 7 });
  showCountry(iso);
}

Promise.all([
  fetch("data/land.geojson").then((r) => r.json()),
  fetch("data/zones.geojson").then((r) => r.json()),
  fetch("data/countries.json").then((r) => r.json()),
  fetch("data/labels.json").then((r) => r.json()),
]).then(([land, zones, meta, labels]) => {
  L.geoJSON(land, {
    interactive: false,
    style: { fillColor: dark ? "#2a303c" : "#e9e7e2", fillOpacity: 1, color: dark ? "#3a4252" : "#c9c6bf", weight: 0.6 },
  }).addTo(map);
  countries = meta.countries;
  $("generated").textContent = `Laatste wijziging reisadvies: ${formatDate(meta.generated)}`;
  // draw cities last so they sit on top of the zone that surrounds them
  zones.features.sort((a, b) => (a.properties.city ? 1 : 0) - (b.properties.city ? 1 : 0));
  layer = L.geoJSON(zones, {
    style,
    onEachFeature: (f, l) => {
      l.bindTooltip(
        `<strong>${countries[f.properties.iso].name}</strong><br>` +
          `<span class="tip-level" style="background:${LEVELS[f.properties.level].color}"></span>${zoneText(f.properties)}`,
        { sticky: true, direction: "top", offset: [0, -6] },
      );
      l.on("click", () => showCountry(f.properties.iso, f.properties));
      l.on("mouseover", () => l.setStyle({ fillOpacity: visible.has(f.properties.level) ? 0.9 : 0 }));
      l.on("mouseout", () => l.setStyle({ fillOpacity: visible.has(f.properties.level) ? 0.72 : 0 }));
    },
  }).addTo(map);
  // city exceptions are only a few pixels wide when zoomed out: mark them with a fixed-size dot
  layer.eachLayer((l) => {
    const p = l.feature.properties;
    if (!p.city) return;
    const dot = L.circleMarker(l.getBounds().getCenter(), {
      pane: "exceptions", radius: 5, fillColor: LEVELS[p.level].color, fillOpacity: 1, color: "#1d2330", weight: 1.5,
    });
    dot.feature = l.feature;
    dot.bindTooltip(l.getTooltip().getContent(), { direction: "top", offset: [0, -6] });
    dot.on("click", () => showCountry(p.iso, p));
    layer.addLayer(dot);
  });
  labelData = labels;
  updateLabels();
  buildLegend(zones.features);
  buildSearch();
  const n = Object.keys(countries).length;
  $("counts").textContent = `${n} landen en gebieden.`;
  const [head, from, to] = location.hash.slice(1).split("/");
  if (head === "wijzigingen") setMode("changes", { from, to });
  else if (countries[head.toUpperCase()]) focusCountry(head.toUpperCase());
});

$("close").onclick = closeDetail;
document.addEventListener("keydown", (e) => e.key === "Escape" && selected && closeDetail());
