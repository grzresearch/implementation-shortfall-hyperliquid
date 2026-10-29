// Number formatting. Every formatter returns a plain string; callers set textContent.

const MINUS = '−';

function withSign(s, v) {
  return v < 0 ? MINUS + s.replace(/^-/, '') : s;
}

export function fmtUsd(v, { compact = true, digits } = {}) {
  if (!Number.isFinite(v)) return '–';
  const a = Math.abs(v);
  let s;
  if (compact && a >= 1e9) s = (a / 1e9).toFixed(digits ?? 2) + 'B';
  else if (compact && a >= 1e6) s = (a / 1e6).toFixed(digits ?? 2) + 'M';
  else if (compact && a >= 1e4) s = (a / 1e3).toFixed(digits ?? 1) + 'K';
  else s = a.toLocaleString('en-US', { maximumFractionDigits: digits ?? (a >= 100 ? 0 : 2) });
  return withSign('$' + s, v);
}

export function fmtBps(v, digits = 2) {
  if (!Number.isFinite(v)) return '–';
  return withSign(Math.abs(v).toFixed(digits), v);
}

export function fmtPct(v, digits = 2) {
  if (!Number.isFinite(v)) return '–';
  return withSign(Math.abs(v).toFixed(digits) + '%', v);
}

export function fmtPx(v) {
  if (!Number.isFinite(v)) return '–';
  const a = Math.abs(v);
  const opts = a >= 1000
    ? { maximumFractionDigits: 2 }
    : { maximumSignificantDigits: 6 };
  return withSign(a.toLocaleString('en-US', opts), v);
}

export function fmtQty(v) {
  if (!Number.isFinite(v)) return '–';
  const a = Math.abs(v);
  const s = a >= 1e6 ? (a / 1e6).toFixed(2) + 'M'
    : a >= 1e4 ? (a / 1e3).toFixed(1) + 'K'
    : a.toLocaleString('en-US', { maximumSignificantDigits: 5 });
  return withSign(s, v);
}

export function fmtInt(v) {
  if (!Number.isFinite(v)) return '–';
  return v.toLocaleString('en-US', { maximumFractionDigits: 0 });
}

export function fmtAge(ms) {
  if (!Number.isFinite(ms)) return '–';
  const s = Math.max(0, Math.round(ms / 1000));
  if (s < 60) return s + 's';
  const m = Math.floor(s / 60);
  return m < 60 ? `${m}m ${s % 60}s` : `${Math.floor(m / 60)}h ${m % 60}m`;
}

export function fmtSizeLabel(usd) {
  if (usd >= 1e6) return '$' + (usd / 1e6).toLocaleString('en-US', { maximumFractionDigits: 2 }) + 'M';
  if (usd >= 1e3) return '$' + (usd / 1e3).toLocaleString('en-US', { maximumFractionDigits: 1 }) + 'K';
  return '$' + usd.toLocaleString('en-US');
}

export function parseSizeInput(text) {
  if (text == null) return NaN;
  const t = String(text).trim().replace(/[$,\s_]/g, '').toLowerCase();
  const m = t.match(/^(\d+(?:\.\d+)?)([kmb])?$/);
  if (!m) return NaN;
  const mult = { k: 1e3, m: 1e6, b: 1e9 }[m[2]] ?? 1;
  return parseFloat(m[1]) * mult;
}
