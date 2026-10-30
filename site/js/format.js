// Number formatting. Every formatter returns a plain string; callers set textContent.

const MINUS = '−';

// The sign is decided on the rounded text, so a −1e-13 residual prints as 0.00, not −0.00.
function withSign(s, v) {
  return v < 0 && /[1-9]/.test(s) ? MINUS + s.replace(/^-/, '') : s.replace(/^-/, '');
}

/** Compact magnitude: rounds first, then picks the unit, so 999,950 becomes 1.00M rather than 1000.0K. */
function compactAbs(a, digits, { kDigits = 1, mDigits = 2, bDigits = 2 } = {}) {
  const units = [[1e9, 'B', digits ?? bDigits], [1e6, 'M', digits ?? mDigits], [1e3, 'K', digits ?? kDigits]];
  for (let i = 0; i < units.length; i++) {
    const [div, suffix, d] = units[i];
    if (a >= div || (i < units.length - 1 && Number((a / units[i + 1][0]).toFixed(units[i + 1][2])) >= 1000)) {
      const r = Number((a / div).toFixed(d));
      if (r >= 1000 && i > 0) continue;
      return r.toFixed(d) + suffix;
    }
  }
  return null;
}

export function fmtUsd(v, { compact = true, digits } = {}) {
  if (!Number.isFinite(v)) return '–';
  const a = Math.abs(v);
  let s = compact && a >= 1e4 ? compactAbs(a, digits) : null;
  if (s == null) s = a.toLocaleString('en-US', { maximumFractionDigits: digits ?? (a >= 100 ? 0 : 2) });
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
  const s = (a >= 1e4 ? compactAbs(a, undefined) : null) ?? a.toLocaleString('en-US', { maximumSignificantDigits: 5 });
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
  if (!Number.isFinite(usd)) return '–';
  if (usd >= 1e9) return '$' + (usd / 1e9).toLocaleString('en-US', { maximumFractionDigits: 2 }) + 'B';
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
