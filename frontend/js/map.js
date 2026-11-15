// Zoomable south-pole map (Leaflet in a flat coordinate system) with a
// polar-projected NASA basemap, an adaptive lat/lon grid and a draggable site marker.
import { KM_PER_DEG, fromMap, kmFromUnits, kmPerPixel, toMap, unitsFromKm } from "./polar.js";

// Tile sources.  `native` is the deepest zoom the server renders; beyond it Leaflet enlarges tiles.
const SPECS = {
  imagery: { native: 4, attribution: "Imagery: NASA LRO WAC mosaic via Moon Trek", url: (z, c, r) => `/api/basemap/imagery/${z}/${c}/${r}.jpg` },
  elevation: { native: 2, attribution: "Elevation: NASA LOLA via Moon Trek", url: (z, c, r) => `/api/basemap/elevation/${z}/${c}/${r}.jpg` },
  relief: { native: 5, attribution: "Shaded relief computed from NASA LOLA elevation", url: (z, c, r, o) => `/api/tiles/relief/${z}/${c}/${r}.png?az=${o.az}&el=${o.el}` },
  sunlight: { native: 5, attribution: "Sunlight: Mazarico et al. 2011 / NASA LOLA", url: (z, c, r) => `/api/tiles/sunlight/${z}/${c}/${r}.png` },
  earthvis: { native: 5, attribution: "Earth visibility: Mazarico et al. 2011 / NASA LOLA", url: (z, c, r) => `/api/tiles/earthvis/${z}/${c}/${r}.png` },
  shadowcam: { native: 7, attribution: "ShadowCam: NASA / KARI Danuri (KPLO)", url: (z, c, r) => `/api/tiles/shadowcam/${z}/${c}/${r}.png` },
};
const BLANK = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7";
const LAT_STEPS = [5, 2, 1, 0.5, 0.2, 0.1, 0.05, 0.02, 0.01, 0.005, 0.002, 0.001];
const LON_STEPS = [90, 45, 30, 15, 10, 5, 2, 1, 0.5, 0.2, 0.1, 0.05, 0.02, 0.01];
const MAP_EDGE_LAT = -78; // beyond this the square map's corners have no useful data

const fmt = (v, d = 4) => v.toFixed(d);
const toDeg = (r) => (r * 180) / Math.PI;
const toRad = (d) => (d * Math.PI) / 180;
const wrapLon = (lon) => ((((lon + 180) % 360) + 360) % 360) - 180;
// Largest step that is still <= the wanted spacing (so lines are never sparser than asked).
const pickStep = (steps, wanted) => steps.find((s) => s <= wanted) ?? steps[steps.length - 1];

export function createMap(container, { onPick, native = {} }) {
  const map = L.map(container, {
    crs: L.CRS.Simple, minZoom: 0.5, maxZoom: 9, zoomSnap: 0.25, zoomDelta: 0.5,
    wheelPxPerZoomLevel: 90, maxBounds: [[-150, -150], [150, 150]], maxBoundsViscosity: 0.8,
  });
  map.attributionControl.setPrefix(false);

  let selected = null;
  const light = { az: 315, el: 12 };
  const layers = {};            // name -> active tile layer
  const opacity = { sunlight: 0.7, earthvis: 0.7, shadowcam: 1 };   // each overlay has its own
  const STACK = { sunlight: 4, earthvis: 5, shadowcam: 6 };            // fixed order: ShadowCam photos on top
  const grid = L.layerGroup().addTo(map);
  const sitesLayer = L.layerGroup().addTo(map);

  // ---- tile layers --------------------------------------------------
  function makeLayer(name) {
    const spec = SPECS[name];
    const layer = L.tileLayer("", {
      tileSize: 256, minNativeZoom: 1, maxNativeZoom: native[name] ?? spec.native, maxZoom: 9,
      bounds: [[-128, -128], [128, 128]], attribution: spec.attribution, noWrap: true, errorTileUrl: BLANK,
    });
    // Leaflet's tile index (x, y) has its origin at the pole; the server's (col, row) at the top-left.
    layer.getTileUrl = (c) => {
      const offset = 2 ** (c.z - 1);
      return spec.url(c.z, c.x + offset, c.y + offset, light);
    };
    return layer;
  }

  function show(name, on, { base = false } = {}) {
    if (layers[name]) { map.removeLayer(layers[name]); delete layers[name]; }
    if (!on) return;
    layers[name] = makeLayer(name).setOpacity(base ? 1 : opacity[name]).addTo(map);
    layers[name].setZIndex(base ? 1 : STACK[name]);
  }

  // When both data overlays are on, blend them ("screen") instead of letting the top one
  // cover the other: sunlit-only spots stay orange, Earth-only spots cyan, both bright.
  function refreshBlend() {
    const both = layers.sunlight && layers.earthvis;
    for (const name of ["sunlight", "earthvis"]) {
      const el = layers[name]?.getContainer();
      if (el) el.style.mixBlendMode = both ? "screen" : "normal";
    }
  }

  const BASES = ["imagery", "elevation", "relief"];
  function setBase(next) {
    for (const b of BASES) show(b, b === next, { base: true });
    container.classList.toggle("plain", next === "plain");
  }
  function setOverlay(name, on) {
    show(name, on);
    refreshBlend();
    if (name === "shadowcam") on ? shadowcamLayer.addTo(map) : map.removeLayer(shadowcamLayer);
  }
  function setOverlayOpacity(name, v) {
    opacity[name] = v;
    layers[name]?.setOpacity(v);
  }
  function setLight(az, el) {
    light.az = az; light.el = el;
    layers.relief?.redraw();
  }

  // ---- adaptive grid ------------------------------------------------
  function drawGrid() {
    grid.clearLayers();
    const kmpp = kmPerPixel(map.getZoom());
    const b = map.getBounds(), c = map.getCenter();
    const containsPole = b.getSouth() <= 0 && b.getNorth() >= 0 && b.getWest() <= 0 && b.getEast() >= 0;
    const corners = [[b.getSouth(), b.getWest()], [b.getSouth(), b.getEast()], [b.getNorth(), b.getWest()], [b.getNorth(), b.getEast()]];
    const cornerR = corners.map(([y, x]) => kmFromUnits(Math.hypot(x, y)));
    const rMax = Math.max(...cornerR);
    const rMin = containsPole ? 0 : Math.max(0, Math.min(...cornerR) - 1);
    const centerR = kmFromUnits(Math.hypot(c.lng, c.lat));
    const centerAz = centerR < 1 ? 0 : toDeg(Math.atan2(c.lng, c.lat));
    const halfDiagKm = kmFromUnits(Math.hypot(b.getEast() - b.getWest(), b.getNorth() - b.getSouth()) / 2);
    const style = { color: "#ffffff", weight: 1, opacity: 0.35, fill: false, interactive: false };
    const label = (pos, text, cls) => L.marker(pos, { interactive: false, icon: L.divIcon({ className: `grid-label ${cls}`, html: `<span>${text}</span>`, iconSize: [0, 0] }) }).addTo(grid);

    // Latitude rings about 70 px apart, labelled at the azimuth the view is centred on.
    const latStep = pickStep(LAT_STEPS, (70 * kmpp) / KM_PER_DEG);
    const latLo = fromMap(0, unitsFromKm(rMin)).lat, latHi = fromMap(0, unitsFromKm(rMax)).lat;
    for (let lat = Math.ceil((latLo + 1e-9) / latStep) * latStep; lat <= latHi; lat += latStep) {
      if (lat < -90 + 1e-9 || lat > MAP_EDGE_LAT) continue;
      const rUnits = toMap(lat, 0)[0];
      L.circle([0, 0], { ...style, radius: rUnits }).addTo(grid);
      const a = toRad(centerAz);
      label([rUnits * Math.cos(a), rUnits * Math.sin(a)], `${+lat.toFixed(4)}°`, "lat");
    }

    // Longitude spokes about 110 px apart, only those inside the visible wedge.
    const lonStep = containsPole ? 45 : pickStep(LON_STEPS, toDeg((110 * kmpp) / Math.max(centerR, 1)));
    const wholeCircle = containsPole || halfDiagKm >= centerR;
    const span = wholeCircle ? 180 : toDeg(Math.asin(halfDiagKm / centerR)) + lonStep;
    const first = Math.ceil((centerAz - span) / lonStep), last = Math.floor((centerAz + span) / lonStep);
    const r0 = unitsFromKm(rMin), r1 = unitsFromKm(Math.min(rMax, 330));
    const rLabel = containsPole ? unitsFromKm(285) : unitsFromKm(Math.min(Math.max(centerR, rMin), 330));
    for (let i = first; i <= last && last - first < 400; i++) {
      const lon = i * lonStep, a = toRad(lon);
      L.polyline([[r0 * Math.cos(a), r0 * Math.sin(a)], [r1 * Math.cos(a), r1 * Math.sin(a)]], style).addTo(grid);
      label([rLabel * Math.cos(a), rLabel * Math.sin(a)], `${+wrapLon(lon).toFixed(4)}°`, "lon");
    }
  }

  // ---- markers ------------------------------------------------------
  function setSites(sites) {
    sitesLayer.clearLayers();
    for (const s of sites) {
      L.circleMarker(toMap(s.lat, s.lon), { radius: 5, color: "#000", weight: 1.5, fillColor: s.kind === "published" ? "#ffffff" : "#4dd0e1", fillOpacity: 1 })
        .bindTooltip(s.name, { direction: "top" })
        .on("click", (ev) => { L.DomEvent.stopPropagation(ev); onPick(s.lat, s.lon, s); })
        .addTo(sitesLayer);
    }
  }

  const shadowcamLayer = L.layerGroup();
  function setShadowcam(list) {
    shadowcamLayer.clearLayers();
    const seen = new Set();
    for (const m of list) {
      if (seen.has(m.feature)) continue;
      seen.add(m.feature);
      L.circleMarker(toMap(m.lat, m.lon), { radius: 4, color: "#000", weight: 1, fillColor: "#e8e8e8", fillOpacity: 0.9 })
        .bindTooltip(`ShadowCam: ${m.feature.replace(/\d+$/, "")}`, { direction: "top" }).addTo(shadowcamLayer);
    }
  }

  const pin = L.divIcon({ className: "site-pin", html: "<span></span>", iconSize: [26, 26], iconAnchor: [13, 13] });
  function setSelected(lat, lon, { pan = false } = {}) {
    const pos = toMap(lat, lon);
    if (!selected) {
      selected = L.marker(pos, { icon: pin, draggable: true, zIndexOffset: 1000, title: "Drag to fine-tune" }).addTo(map);
      selected.on("dragend", () => {
        const p = fromMap(selected.getLatLng().lat, selected.getLatLng().lng);
        onPick(+fmt(p.lat), +fmt(wrapLon(p.lon)));
      });
    } else selected.setLatLng(pos);
    if (pan && !map.getBounds().contains(pos)) map.panTo(pos);
  }

  map.on("click", (ev) => {
    const p = fromMap(ev.latlng.lat, ev.latlng.lng);
    if (p.lat > MAP_EDGE_LAT) return;
    onPick(+fmt(p.lat), +fmt(wrapLon(p.lon)));
  });

  // ---- coordinate readout + scale bar -------------------------------
  const Readout = L.Control.extend({
    options: { position: "topright" },
    onAdd() { this.el = L.DomUtil.create("div", "map-readout"); this.el.textContent = "Hover for coordinates"; return this.el; },
  });
  const readout = new Readout().addTo(map);
  map.on("mousemove", (ev) => {
    const p = fromMap(ev.latlng.lat, ev.latlng.lng);
    readout.el.textContent = `${fmt(p.lat)}°, ${fmt(wrapLon(p.lon))}°  ·  ${p.rKm.toFixed(1)} km from pole`;
  });
  map.on("mouseout", () => { readout.el.textContent = "Hover for coordinates"; });

  const Scale = L.Control.extend({
    options: { position: "bottomleft" },
    onAdd() { this.el = L.DomUtil.create("div", "map-scale"); this.el.innerHTML = "<i></i><b></b>"; return this.el; },
  });
  const scale = new Scale().addTo(map);
  function drawScale() {
    const kmpp = kmPerPixel(map.getZoom());
    const km = [0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100, 200].filter((k) => k / kmpp <= 140).pop() ?? 0.01;
    scale.el.querySelector("i").style.width = `${km / kmpp}px`;
    scale.el.querySelector("b").textContent = km >= 1 ? `${km} km` : `${Math.round(km * 1000)} m`;
  }

  map.on("zoomend moveend", () => { drawGrid(); drawScale(); });
  map.setView([0, 0], 1.5);
  setBase("imagery");
  drawGrid(); drawScale();

  return {
    setBase, setOverlay, setOverlayOpacity, setLight, setSites, setSelected, setShadowcam,
    resetView: () => map.setView([0, 0], 1.5),
    zoomToSite: (lat, lon) => map.setView(toMap(lat, lon), 6),
  };
}
