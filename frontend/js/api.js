async function getJson(url) {
  const res = await fetch(url);
  if (!res.ok) {
    let detail = res.statusText;
    try { const body = await res.json(); detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail); } catch {}
    throw new Error(detail);
  }
  return res.json();
}

export const getSites = () => getJson("/api/sites");
export const getDatasets = () => getJson("/api/datasets");

// Mission + requirement settings shared by both endpoints (requirement values arrive in percent).
const missionParams = (p) => ({
  stay_days: p.stayDays, dsn: p.useDsn, observer_height_m: p.obsHeight,
  slope_max_deg: p.reqSlope, sun_cover_min: p.reqSun / 100, earth_cover_min: p.reqEarth / 100, dates_min: p.reqDates / 100,
});

export const getLeaderboard = (p) =>
  getJson("/api/leaderboard?" + new URLSearchParams({
    start: p.start, days: p.days, step_min: Math.max(p.step, 30),
    sun_min_deg: p.sunMin, earth_min_deg: p.earthMin, ...missionParams(p),
  }));

export const evaluate = (p) =>
  getJson("/api/evaluate?" + new URLSearchParams({
    lat: p.lat, lon: p.lon, start: p.start, days: p.days, step_min: p.step,
    sun_min_deg: p.sunMin, earth_min_deg: p.earthMin, ...missionParams(p),
  }));

export const getValidation = (refresh = false) => getJson("/api/validation" + (refresh ? "?refresh=true" : ""));
