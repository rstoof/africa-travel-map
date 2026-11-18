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

function showCountry(iso, zone) {
  const c = countries[iso];
  if (!c) return;
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
  history.replaceState(null, "", location.pathname);
  $("detail").hidden = true;
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
]).then(([land, zones, meta]) => {
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
