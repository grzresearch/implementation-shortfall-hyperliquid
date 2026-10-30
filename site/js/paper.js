// Loads results/summary_stats.csv (the paper's master output) for the benchmark panel.
export const STRATEGY_ORDER = ['NAIVE_MARKET', 'MARKET_CAP5', 'TWAP_5MIN', 'TWAP_30MIN', 'VWAP_30MIN'];

function indexCsv(text, source) {
  const lines = text.replace(/^﻿/, '').trim().split(/\r?\n/);
  const cols = lines[0].split(',');
  const idx = Object.fromEntries(cols.map((c, i) => [c, i]));
  const num = (v, name) => Number(v[idx[name]]);
  const byKey = new Map();
  for (let i = 1; i < lines.length; i++) {
    const v = lines[i].split(',');
    if (v.length < cols.length) continue;
    const coin = v[idx.coin], strategy = v[idx.strategy], side = v[idx.side];
    const size = num(v, 'order_size_usd');
    const row = {
      nObs: num(v, 'n_obs'), median: num(v, 'median_total_is_bps'), mean: num(v, 'mean_total_is_bps'),
      p05: num(v, 'p05_total_is_bps'), p95: num(v, 'p95_total_is_bps'),
      spread: num(v, 'median_spread_bps'), impact: num(v, 'median_impact_bps'), fee: num(v, 'median_fee_bps'),
      opp: num(v, 'median_opp_bps'), completion: num(v, 'median_completion_pct'),
    };
    let m = byKey.get(coin);
    if (!m) { m = { strategies: new Set(), sizes: new Set(), cells: new Map() }; byKey.set(coin, m); }
    m.strategies.add(strategy); m.sizes.add(size);
    m.cells.set(`${strategy}|${side}|${size}`, row);
  }
  return { source, byKey, marketCount: byKey.size };
}

export async function loadPaper(paths) {
  for (const p of paths) {
    try {
      const res = await fetch(p); // default cache mode: revalidates, so a redeployed CSV is picked up
      if (!res.ok) continue;
      return indexCsv(await res.text(), p);
    } catch { /* try the next location */ }
  }
  return null;
}

export function paperFor(paper, key) {
  const m = paper?.byKey.get(key);
  if (!m) return null;
  const strategies = STRATEGY_ORDER.filter((s) => m.strategies.has(s));
  const sizes = [...m.sizes].sort((a, b) => a - b);
  return { key, strategies, sizes, cell: (s, side, size) => m.cells.get(`${s}|${side}|${size}`) ?? null };
}
