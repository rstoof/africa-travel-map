// "Wijzigingen" mode: the map on a chosen date, with areas that changed colour
// since an earlier date outlined. Data comes from web/data/history (src/history.py).

let hist = null;
let histLayer = null;
let renderToken = 0;
const jsonCache = {};
const getJSON = (url) => (jsonCache[url] ??= fetch(url).then((r) => r.json()));
const today = () => new Date().toISOString().slice(0, 10);
const km = (n) => `${Math.round(n).toLocaleString("nl-NL")} km²`;

function loadHistory() {
  hist ??= getJSON("data/history/index.json");
  return hist;
}

// Snapshot of a country in force on `date` (latest snapshot on or before it).
function snapshotAt(h, iso, date) {
  let found = null;
  for (const s of h.snapshots[iso] || []) if (s.date <= date) found = s.date;
  return found;
}

function chip(from, to) {
  return `<span class="chip"><i style="background:${LEVELS[from].color}"></i>${LEVELS[from].name.toLowerCase()} →
    <i style="background:${LEVELS[to].color}"></i>${LEVELS[to].name.toLowerCase()}</span>`;
}

function eventDate(e) {
  return e.after ? `tussen ${formatDate(e.after)} en ${formatDate(e.date)}` : formatDate(e.date);
}

function eventItem(e, onClick) {
  const li = document.createElement("li");
  const btn = document.createElement("button");
  const amounts = [
    e.worse_km2 ? `${km(e.worse_km2)} strenger` : "",
    e.better_km2 ? `${km(e.better_km2)} milder` : "",
  ].filter(Boolean).join(" · ");
  btn.innerHTML = `<span class="ev-top"><strong>${e.name}</strong><span class="ev-date">${eventDate(e)}</span></span>
    <span class="ev-trans">${e.transitions.map(([a, b]) => chip(a, b)).join("")}</span>
    <span class="ev-km">${amounts || "kaart aangepast"}</span>`;
  btn.onclick = onClick;
  li.appendChild(btn);
  return li;
}

async function renderChanges({ zoomTo } = {}) {
  const h = await loadHistory();
  const token = ++renderToken;
  let from = $("c-from").value || h.dates[0];
  let to = $("c-to").value || today();
  if (from > to) [from, to] = [to, from];
  history.replaceState(null, "", `#wijzigingen/${from}/${to}`);

  const base = [];
  const diffs = [];
  await Promise.all(Object.keys(h.snapshots).map(async (iso) => {
    const b = snapshotAt(h, iso, to);
    if (!b) return;
    base.push(...(await getJSON(`data/history/${iso}/${b}.geojson`)).features);
    const a = snapshotAt(h, iso, from);
    if (a && a !== b) {
      const d = await getJSON(`data/history/${iso}/diff_${a}_${b}.geojson`);
      d.features.forEach((f) => diffs.push({ ...f, properties: { ...f.properties, iso } }));
    }
  }));
  if (token !== renderToken) return; // a newer range was picked meanwhile

  if (histLayer) histLayer.remove();
  histLayer = L.featureGroup().addTo(map);
  L.geoJSON({ type: "FeatureCollection", features: base }, {
    style: (f) => ({
      fillColor: LEVELS[f.properties.level].color, fillOpacity: 0.3,
      color: dark ? "#161a22" : "#ffffff", weight: 0.5,
    }),
    onEachFeature: (f, l) => l.bindTooltip(
      `<strong>${countries[f.properties.iso]?.name ?? f.properties.iso}</strong><br>` +
        `${zoneText(f.properties)} <span class="ev-date">op ${formatDate(to)}</span>`,
      { sticky: true },
    ),
  }).addTo(histLayer);
  const diffLayer = L.geoJSON({ type: "FeatureCollection", features: diffs }, {
    style: (f) => ({
      fillColor: LEVELS[f.properties.to].color, fillOpacity: 0.95,
      color: f.properties.to > f.properties.from ? "#7a0010" : "#1565c0", weight: 2,
    }),
    onEachFeature: (f, l) => {
      l.bindTooltip(
        `<strong>${countries[f.properties.iso]?.name ?? f.properties.iso}</strong><br>` +
          `${chip(f.properties.from, f.properties.to)}<br>${km(f.properties.km2)}<br>` +
          `<em>Klik voor de toelichting in het reisadvies</em>`,
        { sticky: true },
      );
      l.on("click", () => showChange(f.properties.iso, f.properties, from, to));
    },
  }).addTo(histLayer);

  // panel: events whose date falls in (from, to]
  const events = h.events.filter((e) => e.date > from && e.date <= to);
  const worse = events.reduce((s, e) => s + e.worse_km2, 0);
  const better = events.reduce((s, e) => s + e.better_km2, 0);
  $("c-summary").textContent = events.length
    ? `${events.length} wijziging${events.length > 1 ? "en" : ""}: ${km(worse)} strenger, ${km(better)} milder`
    : "Geen kleurwijzigingen in deze periode.";
  const list = $("c-events");
  list.replaceChildren(...events.map((e) => eventItem(e, () => {
    zoomToDiff(diffLayer, e.iso);
    // explain the transition covering the largest area
    let biggest = null;
    diffLayer.eachLayer((l) => {
      const p = l.feature.properties;
      if (p.iso === e.iso && (!biggest || p.km2 > biggest.km2)) biggest = p;
    });
    showChange(e.iso, biggest, from, to);
  })));
  const texts = h.text_updates.filter((t) => t.date > from && t.date <= to);
  $("c-text-wrap").hidden = !texts.length;
  $("c-text").replaceChildren(...texts.map((t) => {
    const li = document.createElement("li");
    li.className = "plain";
    li.textContent = `${formatDate(t.date)} · ${countries[t.iso]?.name ?? t.iso}`;
    return li;
  }));
  if (zoomTo) zoomToDiff(diffLayer, zoomTo);
}

// Text version in force on `date`, or null when we have none that old.
function textAt(h, iso, date) {
  let found = null;
  for (const t of h.texts?.[iso] || []) if (t.date <= date) found = t.date;
  return found && getJSON(`data/history/${iso}/text_${found}.json`).then((t) => ({ ...t, date: found }));
}

function sentences(html) {
  const div = document.createElement("div");
  div.innerHTML = html || "";
  return div.textContent.split(/(?<=[.!?])\s+/).map((x) => x.trim()).filter(Boolean);
}

// Sentence-level diff (LCS); returns [{op: "same"|"add"|"del", text}].
function diffSentences(a, b) {
  const n = a.length, m = b.length;
  const dp = Array.from({ length: n + 1 }, () => new Uint16Array(m + 1));
  for (let i = n - 1; i >= 0; i--)
    for (let j = m - 1; j >= 0; j--)
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  const out = [];
  let i = 0, j = 0;
  while (i < n || j < m) {
    if (i < n && j < m && a[i] === b[j]) { out.push({ op: "same", text: a[i] }); i++; j++; }
    else if (j < m && (i === n || dp[i][j + 1] >= dp[i + 1][j])) out.push({ op: "add", text: b[j++] });
    else out.push({ op: "del", text: a[i++] });
  }
  return out;
}

async function showChange(iso, piece, from, to) {
  const h = await loadHistory();
  const c = countries[iso];
  showCountry(iso, piece ? { level: piece.to } : null);
  $("changes").hidden = true;
  $("detail").hidden = false;
  history.replaceState(null, "", `#wijzigingen/${from}/${to}`);

  const [before, after] = await Promise.all([textAt(h, iso, from), textAt(h, iso, to)]);
  // explain with the text that was in force on the end date when we have it
  if (piece && after) showWhy(c, { level: piece.to }, after.by_level);

  $("d-change").hidden = false;
  $("d-change-title").innerHTML = piece
    ? `Gewijzigd: ${chip(piece.from, piece.to)}`
    : "Gewijzigd";
  $("d-change-meta").textContent = piece
    ? `${km(piece.km2)} tussen ${formatDate(from)} en ${formatDate(to)}`
    : `Tussen ${formatDate(from)} en ${formatDate(to)}`;

  const show = (id, on) => ($(id).hidden = !on);
  if (before && after && before.date !== after.date) {
    const ops = diffSentences(sentences(before.regional || before.summary), sentences(after.regional || after.summary))
      .filter((o) => o.op !== "same");
    const body = $("d-textdiff-body");
    body.replaceChildren(...ops.map((o) => {
      const el = document.createElement(o.op === "add" ? "ins" : "del");
      el.textContent = o.text;
      return el;
    }));
    show("d-textdiff", ops.length);
    show("d-textdiff-none", !ops.length);
    $("d-textdiff-none").textContent = `Geen tekstverschil in 'Regionale risico's' tussen ${formatDate(before.date)} en ${formatDate(after.date)}.`;
  } else {
    show("d-textdiff", false);
    show("d-textdiff-none", true);
    const first = h.texts?.[iso]?.[0]?.date;
    $("d-textdiff-none").textContent = first
      ? `Tekstversies worden bewaard vanaf ${formatDate(first)}; van vóór die datum is geen tekst om mee te vergelijken. De toelichting hieronder is de tekst van ${formatDate(after?.date || first)}.`
      : "Nog geen eerdere tekstversie bewaard om mee te vergelijken.";
  }
}

function zoomToDiff(diffLayer, iso) {
  const bounds = L.latLngBounds([]);
  diffLayer.eachLayer((l) => l.feature.properties.iso === iso && bounds.extend(l.getBounds()));
  if (!bounds.isValid()) layer.eachLayer((l) => l.feature.properties.iso === iso && bounds.extend(l.getBounds()));
  if (bounds.isValid()) map.fitBounds(bounds, { padding: [40, 40], maxZoom: 7 });
}

async function setMode(mode, opts = {}) {
  const changes = mode === "changes";
  $("tab-now").setAttribute("aria-selected", !changes);
  $("tab-changes").setAttribute("aria-selected", changes);
  $("changes").hidden = !changes;
  $("legend").hidden = changes;
  $("search").parentElement.hidden = changes;
  if (changes) {
    $("detail").hidden = true;
    $("intro").hidden = true;
    selected = null;
    layer.remove();
    const h = await loadHistory();
    $("c-since").textContent = formatDate(h.dates[0]);
    for (const id of ["c-from", "c-to"]) {
      $(id).min = h.dates[0];
      $(id).max = today();
    }
    $("c-from").value = opts.from || $("c-from").value || h.dates[0];
    $("c-to").value = opts.to || $("c-to").value || today();
    await renderChanges({ zoomTo: opts.zoomTo });
  } else {
    if (histLayer) histLayer.remove();
    histLayer = null;
    layer.addTo(map);
    closeDetail();
  }
}

// Country detail: list this country's colour changes.
async function showCountryHistory(iso) {
  const h = await loadHistory();
  const events = h.events.filter((e) => e.iso === iso);
  $("d-hist-wrap").hidden = !events.length;
  $("d-hist").replaceChildren(...events.map((e) =>
    eventItem(e, () => setMode("changes", { from: e.prev, to: e.date, zoomTo: iso }))));
}

$("tab-now").onclick = () => setMode("now");
$("tab-changes").onclick = () => setMode("changes");
for (const id of ["c-from", "c-to"]) $(id).addEventListener("change", () => renderChanges());
$("c-presets").addEventListener("click", async (e) => {
  const days = e.target.dataset?.days;
  if (days === undefined) return;
  const h = await loadHistory();
  const start = days === "0" ? h.dates[0] : new Date(Date.now() - days * 864e5).toISOString().slice(0, 10);
  $("c-from").value = start < h.dates[0] ? h.dates[0] : start;
  $("c-to").value = today();
  renderChanges();
});
