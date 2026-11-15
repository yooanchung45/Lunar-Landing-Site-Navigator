import { evaluate, getDatasets, getLeaderboard, getSites, getValidation } from "./api.js";
import { drawElevation, drawSkyline, drawTimeline } from "./charts.js";
import { createMap } from "./map.js";

const $ = (id) => document.getElementById(id);
let sites = [];
let mapCtl;

const fmtH = (h) => (h >= 48 ? `${(h / 24).toFixed(1)} d` : `${h.toFixed(1)} h`);
const fmtDate = (iso) => iso.replace("T", " ").slice(0, 16) + " UTC";

function params() {
  return {
    lat: +$("lat").value, lon: +$("lon").value, start: $("start").value,
    days: +$("days").value, step: +$("step").value,
    sunMin: +$("sunMin").value, earthMin: +$("earthMin").value,
    stayDays: +$("stayDays").value, useDsn: $("useDsn").checked, obsHeight: +$("obsHeight").value,
    reqSlope: +$("reqSlope").value, reqSun: +$("reqSun").value, reqEarth: +$("reqEarth").value, reqDates: +$("reqDates").value,
  };
}

function refreshMap() {
  const p = params();
  if (Number.isFinite(p.lat) && Number.isFinite(p.lon)) mapCtl.setSelected(p.lat, p.lon, { pan: true });
}

// A click, a marker drag or a click on a named site all end up here.
function pick(lat, lon, site) {
  $("lat").value = lat; $("lon").value = lon;
  $("site").value = site ? String(sites.indexOf(site)) : "custom";
  refreshMap();
}

// Hard-edged colour steps (no smooth gradients) sampled from the map colour ramps.
const steps = (colors) => `linear-gradient(to right, ${colors.map((c, i) => `${c} ${(i * 100) / colors.length}% ${((i + 1) * 100) / colors.length}%`).join(", ")})`;
const LEGENDS = {
  sunlight: { title: "Sunlight (share of time lit)", css: steps(["rgb(14,22,70)", "rgb(60,40,130)", "rgb(170,60,140)", "rgb(210,95,100)", "rgb(250,130,60)", "rgb(255,185,100)", "rgb(255,240,150)"]) },
  earthvis: { title: "Earth visible (share of time)", css: steps(["rgb(14,22,70)", "rgb(30,80,160)", "rgb(40,125,185)", "rgb(50,170,210)", "rgb(135,212,232)", "rgb(220,255,255)"]) },
};

function tickClock() {
  const el = $("clock");
  if (el) el.textContent = new Date().toISOString().slice(0, 19).replace("T", " ") + "Z";
}

function setupLayerControls(data) {
  const rampMax = data.ramp_max ?? {};
  const ready = { relief: data.relief, sunlight: data.sunlight, earthvis: data.earthvis, shadowcam: (data.shadowcam ?? []).length > 0 };
  const active = new Set();

  const updateLegend = () => {
    const legend = $("legend");
    legend.hidden = ![...active].some((n) => LEGENDS[n]);
    legend.innerHTML = [...active].filter((n) => LEGENDS[n]).map((n) =>
      `<div>${LEGENDS[n].title}<i style="background:${LEGENDS[n].css}"></i><span><b>0%</b><b>${Math.round((rampMax[n] ?? 1) * 100)}%${(rampMax[n] ?? 1) < 1 ? "+" : ""}</b></span></div>`).join("");
  };

  document.querySelectorAll("#baseSeg button").forEach((btn) => {
    if (btn.dataset.base === "relief" && !ready.relief) { btn.disabled = true; btn.title += " (data still downloading)"; }
    btn.addEventListener("click", () => {
      mapCtl.setBase(btn.dataset.base);
      document.querySelectorAll("#baseSeg button").forEach((b) => b.classList.toggle("on", b === btn));
      $("reliefCtl").hidden = btn.dataset.base !== "relief";
    });
  });

  document.querySelectorAll("[data-overlay]").forEach((box) => {
    const name = box.dataset.overlay;
    const slider = document.querySelector(`[data-ovop="${name}"]`);
    if (!ready[name]) { box.disabled = true; box.closest(".ov").classList.add("off"); box.closest(".ov").title += " (data still downloading)"; }
    slider.disabled = !box.checked;
    box.addEventListener("change", () => {
      slider.disabled = !box.checked;
      mapCtl.setOverlay(name, box.checked);
      box.checked ? active.add(name) : active.delete(name);
      updateLegend();
    });
  });

  document.querySelectorAll("[data-ovop]").forEach((slider) => {
    slider.addEventListener("input", () => mapCtl.setOverlayOpacity(slider.dataset.ovop, +slider.value));
  });

  let timer;
  const light = () => {
    $("lightAzOut").textContent = `${$("lightAz").value}°`;
    $("lightElOut").textContent = `${$("lightEl").value}°`;
    clearTimeout(timer);
    timer = setTimeout(() => mapCtl.setLight(+$("lightAz").value, +$("lightEl").value), 250);
  };
  $("lightAz").addEventListener("input", light);
  $("lightEl").addEventListener("input", light);
}

function showCards(s) {
  const card = (value, label, cls = "") => `<div class="card ${cls}"><b>${value}</b><span>${label}</span></div>`;
  $("cards").innerHTML = [
    card(s.total.toFixed(1), "site score (default weights)", "main"),
    card(`${(s.both_fraction * 100).toFixed(1)}%`, "Sun + Earth together"),
    card(`${(s.sun_fraction * 100).toFixed(1)}%`, "Sun above horizon"),
    card(`${(s.earth_fraction * 100).toFixed(1)}%`, "Earth above horizon"),
    card(fmtH(s.longest_window_h), "longest good window"),
    card(fmtH(s.longest_blackout_h), "longest blackout"),
    card(fmtH(s.longest_dark_h), "longest spell without Sun"),
    card(s.slope_p90_deg == null ? "n/a" : `${s.slope_p90_deg.toFixed(1)}°`, "slope (steepest 10% within 300 m)"),
    card(s.window_count, "windows"),
  ].join("");
}

function showMission(m, stayDays) {
  const note = $("missionNote"), box = $("missionWindows");
  if (!m) {
    note.textContent = `The Period is shorter than a ${stayDays}-day stay, so landing dates cannot be compared. Lengthen the Period.`;
    box.innerHTML = "";
    return;
  }
  const pct = (v) => `${(v * 100).toFixed(0)}%`;
  note.textContent = `Every possible landing time in the Period was tried for a ${m.stay_days}-day stay (${m.starts.toLocaleString()} start times). Averaged over all of them: Sun ${pct(m.mean_sun_cover)}, Sun plus Earth link ${pct(m.mean_both_cover)}. ` +
    `On an unlucky date (worst 10%) Sun plus link covers only ${pct(m.p10_both_cover)} of the stay. The Sun is up for 90%+ of the stay when landing on ${pct(m.fully_lit_share)} of dates (all of it on ${pct(m.continuous_sun_share)}).`;
  if (!m.best.length) {
    box.innerHTML = '<p class="hint fail">No landing time in this Period gives any Sun plus Earth-link time for the stay. Try a longer Period or a different start date.</p>';
    return;
  }
  const rows = m.best.map((b) => `<tr><td>${fmtDate(b.start)}${b.at_period_start ? " *" : ""} → ${fmtDate(b.latest_start)}${b.at_period_end ? " *" : ""}</td>` +
    `<td>${b.slack_days.toFixed(1)} d</td><td>${pct(b.sun_cover)}</td><td>${pct(b.link_cover)}</td><td>${pct(b.both_cover)}</td><td>${fmtDate(b.stay_ends)}</td></tr>`).join("");
  box.innerHTML = `<table><thead><tr><th>Land any time between</th><th>Slack</th><th>Sun</th><th>Earth link</th><th>Both together</th><th>Stay ends (latest landing)</th></tr></thead><tbody>${rows}</tbody></table>` +
    `<p class="hint">Landing anywhere inside a range gives the coverage shown for the whole stay. Slack is how many days the landing can slip within the range. Each range sits inside one of the continuous windows listed under "Windows" (a window of L days leaves L minus ${m.stay_days} days of slack). ` +
    `* The range touches the edge of the Period, so it may really extend further: lengthen the Period to find out.</p>`;
}

function showWindows(list) {
  if (!list.length) { $("windows").textContent = "No window with both Sun and Earth in this period."; return; }
  const rows = list.map((w) => `<tr><td>${fmtDate(w.start)}</td><td>${fmtDate(w.end)}</td><td>${fmtH(w.hours)}</td></tr>`).join("");
  $("windows").innerHTML = `<table><thead><tr><th>Start</th><th>End</th><th>Length</th></tr></thead><tbody>${rows}</tbody></table>`;
}

let boardSites = [];

const WEIGHT_KEYS = ["power", "comms", "reliability", "terrain"];

// Re-rank in the browser: the server sends each site's four 0-1 sub-scores, the sliders weigh them.
function renderBoard() {
  const w = Object.fromEntries(WEIGHT_KEYS.map((k) => [k, +$(`w_${k}`).value]));
  WEIGHT_KEYS.forEach((k) => { $(`wo_${k}`).textContent = w[k]; });
  if (!boardSites.length) {                       // nothing ranked yet: just show the slider values
    $("board").textContent = "Not ranked yet. Set the requirements and weights above, then press Rank candidate sites.";
    return;
  }
  const total = WEIGHT_KEYS.reduce((a, k) => a + w[k], 0) || 1;
  const rows = boardSites.map((s) => ({ s, pts: (100 * WEIGHT_KEYS.reduce((a, k) => a + w[k] * s.score.subscores[k], 0)) / total }));
  const passes = (s) => s.score.safe && (!s.requirements || s.requirements.pass);
  rows.sort((a, b) => (passes(b.s) - passes(a.s)) || (b.pts - a.pts));
  const best = rows.find((r) => passes(r.s));
  const rec = $("recommend");
  rec.hidden = false;
  if (best) {
    const sc = best.s.score, m = best.s.mission;
    rec.textContent = `Recommended: ${best.s.name} at latitude ${best.s.lat.toFixed(4)}°, longitude ${best.s.lon.toFixed(4)}° (${best.s.near}) — ` +
      (m ? `a ${m.stay_days}-day stay has the Sun for at least 90% of it on ${(m.fully_lit_share * 100).toFixed(0)}% of landing dates, ` +
        `and Sun plus an Earth link for ${(m.mean_both_cover * 100).toFixed(0)}% of a typical stay` : `Sun and Earth together ${(sc.both_fraction * 100).toFixed(0)}% of the period`) +
      `; slope ${sc.slope_p90_deg == null ? "n/a" : sc.slope_p90_deg.toFixed(1) + "°"}.`;
  } else {
    const closest = rows[0];
    rec.textContent = "No site meets every requirement." + (closest ? ` Closest by score: ${closest.s.name} — ${(closest.s.requirements?.failures ?? ["too steep"]).join("; ")}.` : "");
  }
  const body = rows.map(({ s, pts }, i) => {
    const sc = s.score;
    const ok = passes(s), why = s.requirements?.failures ?? [];
    return `<tr class="pick ${ok ? "" : "unsafe"}" data-i="${boardSites.indexOf(s)}"><td>${ok ? i + 1 : "–"}</td><td>${s.name}${s.kind === "published" ? ' <span class="tag" title="A site researchers proposed inside one of the 13 NASA Artemis III candidate regions">published</span>' : ""}<br><span class="hint">${s.near}</span>${ok ? "" : `<br><span class="hint fail">ruled out: ${(why.length ? why : ["slope is too steep"]).join("; ")}</span>`}</td>` +
      `<td>${s.lat.toFixed(4)}°<br>${s.lon.toFixed(4)}°</td>` +
      `<td class="score"><span class="bar" style="width:${Math.round(pts * 0.5)}px"></span>${pts.toFixed(0)}</td>` +
      `<td>${(sc.both_fraction * 100).toFixed(0)}%</td><td>${(sc.sun_visible_mean * 100).toFixed(0)}%</td>` +
      `<td title="NASA's long-term map (18.6-year average), for comparison">${s.nasa_sun == null ? "n/a" : (s.nasa_sun * 100).toFixed(0) + "%"} / ${s.nasa_earth == null ? "n/a" : (s.nasa_earth * 100).toFixed(0) + "%"}</td>` +
      `<td>${s.mission ? (s.mission.fully_lit_share * 100).toFixed(0) + "%" : "n/a"}</td>` +
      `<td>${s.mission && s.mission.best.length ? fmtDate(s.mission.best[0].start).slice(0, 10) + " → " + fmtDate(s.mission.best[0].latest_start).slice(0, 10) : "none"}</td>` +
      `<td>${sc.slope_p90_deg == null ? "n/a" : sc.slope_p90_deg.toFixed(1) + "°"}${sc.safe ? "" : " (too steep)"}</td></tr>`;
  }).join("");
  $("board").innerHTML = `<table><thead><tr><th>#</th><th>Site</th><th title="Specific point: latitude and longitude, accurate to about 10 m">Latitude / longitude</th><th>Score</th><th>Sun+Earth</th><th>Sun</th><th title="NASA long-term averages: sunlit / Earth visible">NASA map sun / Earth</th><th title="Share of possible landing dates where the Sun covers at least 90% of the stay">Landing dates with Sun</th><th title="Best range of landing dates for the stay">Best landing range</th><th>Slope</th></tr></thead><tbody>${body}</tbody></table>`;
  $("board").querySelectorAll("tr.pick").forEach((tr) => tr.addEventListener("click", () => {
    const s = boardSites[+tr.dataset.i];
    pick(s.lat, s.lon, null);
    run();
  }));
}

const pts = (v) => (v == null ? "n/a" : (v * 100).toFixed(1));
const delta = (sim, nasa, tol) => {
  if (nasa == null) return `<td>n/a</td>`;
  const d = (sim - nasa) * 100, cls = Math.abs(d) <= 2 ? "ok" : Math.abs(d) <= tol ? "warn" : "bad";
  return `<td class="${cls}">${d > 0 ? "+" : ""}${d.toFixed(1)}</td>`;
};

async function runValidation() {
  const status = $("validationStatus");
  $("runValidation").disabled = true;
  status.className = "hint";
  status.textContent = "Simulating 18.6 years at each site (the first run takes a few minutes; after that it is saved)...";
  try {
    const v = await getValidation();
    const tol = v.tolerance_pts;
    const rows = v.published.map((r) => {
      const claims = r.claims ? Object.entries({ sun: [r.nasa_sun, r.claims.sun_min], earth: [r.nasa_earth, r.claims.earth_min] })
        .filter(([, [val]]) => val != null).map(([k, [val, min]]) => `${k} ${val >= min ? "meets" : "misses"} ${(min * 100).toFixed(0)}%`).join(", ") : "";
      return `<tr><td>${r.name}</td><td>${pts(r.nasa_sun)}</td><td>${pts(r.sim_sun)}</td>${delta(r.sim_sun, r.nasa_sun, tol.sun)}` +
        `<td>${pts(r.nasa_earth)}</td><td>${pts(r.sim_earth)}</td>${delta(r.sim_earth, r.nasa_earth, tol.earth)}` +
        `<td>${r.slope_p90 == null ? "n/a" : r.slope_p90.toFixed(1) + "°"}</td><td class="hint">${claims ? "published claim: " + claims : ""}</td></tr>`;
    }).join("");
    $("validationTable").innerHTML = `<table><thead><tr><th>Site</th><th>NASA sun %</th><th>Ours %</th><th>Δ pts</th><th>NASA Earth %</th><th>Ours %</th><th>Δ pts</th><th>Slope</th><th>Authors' own claim vs NASA map</th></tr></thead><tbody>${rows}</tbody></table>`;
    const r = v.random, line = (k, n) => (r ? `${n}: average error ${r[k].bias > 0 ? "+" : ""}${r[k].bias.toFixed(1)} pts, typical error (RMSE) ${r[k].rmse.toFixed(1)} pts, ${(r[k].within_3 * 100).toFixed(0)}% of points within 3 pts, correlation ${r[k].r.toFixed(2)}` : "");
    $("validationSummary").hidden = !r;
    if (r) $("validationSummary").innerHTML = `${r.points} random points near the pole — ${line("sun", "Sunlight")}. ${line("earth", "Earth visibility")}.`;
    status.textContent = `Simulation is the full 18.6-year cycle averaged over the same 240 m patch as NASA's map value. Terrain: ${v.fine_terrain ? "20 m near the site, 80 m far" : "80 m"}. Green within 2 pts, cyan within tolerance, red beyond.`;
  } catch (err) {
    status.className = "hint error";
    status.textContent = err.message;
  } finally {
    $("runValidation").disabled = false;
  }
}

async function runBoard() {
  const p = params(), status = $("boardStatus");
  $("runBoard").disabled = true;
  status.className = "hint";
  status.textContent = "Simulating every candidate (the first run takes about a minute; later runs are fast)...";
  try {
    boardSites = (await getLeaderboard(p)).sites;
    renderBoard();
    status.textContent = `${boardSites.length} candidates ranked. Click a row to inspect that site.`;
  } catch (err) {
    status.className = "hint error";
    status.textContent = err.message;
  } finally {
    $("runBoard").disabled = false;
  }
}

async function run() {
  const p = params();
  const status = $("status");
  $("go").disabled = true;
  status.className = "hint";
  status.textContent = "Computing... (the very first run downloads ~20 MB of NASA ephemeris data)";
  try {
    const r = await evaluate(p);
    showCards(r.score);
    drawElevation($("chartElev"), r, p.sunMin, p.earthMin);
    drawTimeline($("chartTimeline"), r);
    drawSkyline($("chartSky"), r);
    showWindows(r.windows);
    showMission(r.mission, p.stayDays);
    $("terrainNote").textContent = `Terrain: ${r.terrain}.`;
    status.textContent = `${r.sun_el.length.toLocaleString()} samples.`;
  } catch (err) {
    status.className = "hint error";
    status.textContent = err.message;
  } finally {
    $("go").disabled = false;
  }
}

async function init() {
  tickClock();
  setInterval(tickClock, 1000);
  sites = await getSites();
  const data = await getDatasets().catch(() => ({ native_zoom: {}, shadowcam: [] }));
  mapCtl = createMap($("map"), { onPick: pick, native: data.native_zoom });
  mapCtl.setSites(sites);
  mapCtl.setShadowcam(data.shadowcam ?? []);
  setupLayerControls(data);
  $("resetView").addEventListener("click", () => mapCtl.resetView());
  $("zoomSite").addEventListener("click", () => { const p = params(); mapCtl.zoomToSite(p.lat, p.lon); });
  $("site").innerHTML = `<option value="custom">Custom</option>` +
    sites.map((s, i) => `<option value="${i}">${s.name} (${s.lat}°, ${s.lon}°)</option>`).join("");
  $("site").addEventListener("change", () => {
    const s = sites[$("site").value];
    if (s) { $("lat").value = s.lat; $("lon").value = s.lon; refreshMap(); }
  });
  for (const id of ["lat", "lon"]) $(id).addEventListener("input", () => { $("site").value = "custom"; refreshMap(); });
  $("runBoard").addEventListener("click", runBoard);
  $("runValidation").addEventListener("click", runValidation);
  WEIGHT_KEYS.forEach((k) => $(`w_${k}`).addEventListener("input", renderBoard));
  renderBoard();
  $("go").addEventListener("click", run);
  $("site").value = "0";
  $("site").dispatchEvent(new Event("change"));
  run();
}

init().catch((err) => { $("status").className = "hint error"; $("status").textContent = err.message; });
