// Polar stereographic projection centred on the lunar south pole.
// Must match backend/basemap.py (same radius, same extent, same orientation).
export const MOON_RADIUS_KM = 1737.4;
export const HALF_EXTENT_KM = 320;
export const KM_PER_DEG = (2 * Math.PI * MOON_RADIUS_KM) / 360;

const UNITS_PER_KM = 128 / HALF_EXTENT_KM; // map units: the whole map is 256 wide
const rad = (d) => (d * Math.PI) / 180;
const deg = (r) => (r * 180) / Math.PI;

// Map units are what Leaflet calls "lat/lng" in its flat (Simple) coordinate system.
export function toMap(lat, lon) {
  const r = 2 * MOON_RADIUS_KM * Math.tan(rad(90 + lat) / 2);
  return [r * Math.cos(rad(lon)) * UNITS_PER_KM, r * Math.sin(rad(lon)) * UNITS_PER_KM]; // [y, x]
}

export function fromMap(y, x) {
  const yKm = y / UNITS_PER_KM, xKm = x / UNITS_PER_KM;
  const r = Math.hypot(xKm, yKm);
  const lat = -90 + deg(2 * Math.atan(r / (2 * MOON_RADIUS_KM)));
  const lon = r < 1e-9 ? 0 : deg(Math.atan2(xKm, yKm));
  return { lat, lon, rKm: r };
}

export const kmPerPixel = (zoom) => HALF_EXTENT_KM / (128 * 2 ** zoom);
export const unitsFromKm = (km) => km * UNITS_PER_KM;
export const kmFromUnits = (u) => u / UNITS_PER_KM;
