// DOM and text helpers shared by every module.

export const $ = id => document.getElementById(id);

export function esc(value) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

export function label(name) {
  return name.replaceAll('_', ' ');
}

export function formatTime(seconds) {
  if (seconds == null || !Number.isFinite(seconds)) return '0:00';
  const total = Math.max(0, Math.floor(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = String(total % 60).padStart(2, '0');
  return h ? `${h}:${String(m).padStart(2, '0')}:${s}` : `${m}:${s}`;
}

// Mixes a #rrggbb colour with black (f < 1).
export function shade(hex, f) {
  const n = parseInt(hex.slice(1), 16);
  const ch = v => Math.max(0, Math.min(255, Math.round(v * f))).toString(16).padStart(2, '0');
  return '#' + ch((n >> 16) & 255) + ch((n >> 8) & 255) + ch(n & 255);
}


// A stable colour per soundboard tile, so the same sound always looks the same.
export function hueOf(text) {
  let hash = 0;
  for (const ch of String(text)) hash = (hash * 31 + ch.charCodeAt(0)) % 360;
  return hash;
}

// Search: case-insensitive match on a name or link.
export function matchesQuery(query, ...fields) {
  if (!query) return true;
  return fields.some(field => String(field ?? '').toLowerCase().includes(query));
}
