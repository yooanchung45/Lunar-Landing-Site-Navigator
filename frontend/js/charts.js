const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

const timesOf = (r) => {
  const t0 = Date.parse(r.t0 + "Z");
  return r.sun_el.map((_, i) => new Date(t0 + i * r.step_s * 1000).toISOString());
};

// Contiguous [start, end) index ranges where mask is 1.
export function ranges(mask) {
  const out = [];
  let start = -1;
  mask.forEach((v, i) => {
    if (v && start < 0) start = i;
    if (!v && start >= 0) { out.push([start, i]); start = -1; }
  });
  if (start >= 0) out.push([start, mask.length]);
  return out;
}

// Oscilloscope look: black screen, full grid, boxed frame, ticks inside, Sun amber / Earth cyan traces on a dim grid.
const MONO = '"Share Tech Mono", "Courier New", monospace';
const axis = (extra = {}) => ({
  showgrid: true, gridcolor: css("--grid"), gridwidth: 1, zeroline: false,
  showline: true, linecolor: "#777", linewidth: 1, mirror: true, ticks: "inside", tickcolor: "#777",
  tickfont: { family: MONO, size: 10, color: css("--label") },
  title: { font: { family: MONO, size: 10, color: css("--muted") } },
  ...extra,
});

function layoutBase(extra = {}) {
  const { xaxis, yaxis, legend, ...rest } = extra;
  const caps = (a) => (a && typeof a.title === "string" ? { ...a, title: { text: a.title.toUpperCase(), font: { family: MONO, size: 10, color: css("--muted") } } } : a);
  return {
    paper_bgcolor: "#000", plot_bgcolor: "#000",
    font: { family: MONO, color: css("--text"), size: 11 },
    margin: { l: 52, r: 10, t: 6, b: 36 },
    xaxis: axis({ ...(xaxis ? {} : { type: "date" }), ...caps(xaxis) }),   // time axis unless the chart sets its own
    yaxis: axis(caps(yaxis)),
    legend: { font: { family: MONO, size: 10, color: css("--label") }, bgcolor: "#000", bordercolor: "#555", borderwidth: 1, ...legend },
    ...rest,
  };
}
const config = { displayModeBar: false, responsive: true };

export function drawElevation(div, r, sunMin, earthMin) {
  const x = timesOf(r), line = css("--line");
  const end = Math.min(r.sun_ok.length, x.length) - 1;
  const shades = ranges(r.both_ok).map(([s, e]) => ({
    type: "rect", xref: "x", yref: "paper", x0: x[s], x1: x[Math.min(e, end)], y0: 0, y1: 1,
    fillcolor: css("--both"), opacity: 0.22, line: { width: 0 }, layer: "below",
  }));
  const guide = (y, color) => ({ type: "line", xref: "paper", x0: 0, x1: 1, y0: y, y1: y, line: { color, width: 1, dash: "dot" } });
  Plotly.react(div, [
    { x, y: r.sun_el, name: "Sun", mode: "lines", line: { color: css("--sun"), width: 1.3 } },
    { x, y: r.earth_el, name: "Earth", mode: "lines", line: { color: css("--earth"), width: 1.3 } },
  ], layoutBase({
    yaxis: { title: "elevation (°)" },
    legend: { orientation: "h", y: 1.12 },
    shapes: [...shades, guide(sunMin, css("--sun")), guide(earthMin, css("--earth"))],
  }), config);
}

export function drawTimeline(div, r) {
  const x = timesOf(r), last = x.length - 1;
  const rows = [
    { key: "dsn_ok", label: "Ground station", color: css("--muted"), y: 0 },
    { key: "earth_ok", label: "Earth visible", color: css("--earth"), y: 1 },
    { key: "sun_ok", label: "Sun up", color: css("--sun"), y: 2 },
    { key: "both_ok", label: "Sun + Earth link", color: css("--both"), y: 3 },
  ];
  const shapes = rows.flatMap((row) =>
    ranges(r[row.key]).map(([s, e]) => ({
      type: "rect", xref: "x", yref: "y", x0: x[s], x1: x[Math.min(e, last)],
      y0: row.y + 0.1, y1: row.y + 0.9, fillcolor: row.color, opacity: 0.85, line: { width: 0 },
    })));
  Plotly.react(div, [
    { x: [x[0], x[last]], y: [0, 0], mode: "markers", marker: { opacity: 0 }, hoverinfo: "skip", showlegend: false },
  ], layoutBase({
    margin: { l: 96, r: 16, t: 8, b: 36 },
    yaxis: { range: [0, 4], tickvals: rows.map((o) => o.y + 0.5), ticktext: rows.map((o) => o.label), showgrid: false, fixedrange: true, ticks: "" },
    shapes,
  }), config);
}

// Sky seen from the site: terrain silhouette (grey) with the Sun and Earth tracks over the period.
export function drawSkyline(div, r) {
  const az = Array.from({ length: r.horizon.length }, (_, i) => (i * 360) / r.horizon.length);
  const line = css("--line");
  const track = (el, azi, name, color) => ({
    x: azi, y: el, name, mode: "markers", type: "scatter",
    marker: { color, size: 3, opacity: 0.55 },
    hovertemplate: `${name}: az %{x:.0f}°, el %{y:.2f}°<extra></extra>`,
  });
  const lo = Math.min(-2, Math.floor(Math.min(...r.horizon))), hi = Math.max(8, Math.ceil(Math.max(...r.horizon, ...r.earth_el, ...r.sun_el)) + 1);
  Plotly.react(div, [
    { x: az, y: r.horizon, name: "Terrain horizon", mode: "lines", fill: "tozeroy", line: { color: css("--muted"), width: 1.2, shape: "hv" }, fillcolor: "rgba(154,134,80,0.35)", hovertemplate: "terrain: az %{x:.0f}°, up to %{y:.2f}°<extra></extra>" },
    track(r.sun_el, r.sun_az, "Sun", css("--sun")),
    track(r.earth_el, r.earth_az, "Earth", css("--earth")),
  ], layoutBase({
    xaxis: { title: "compass direction (° from north, east = 90)", range: [0, 360], dtick: 45 },
    yaxis: { title: "elevation (°)", range: [lo, hi] },
    legend: { orientation: "h", y: 1.14 },
    margin: { l: 52, r: 16, t: 8, b: 48 },
  }), config);
}
