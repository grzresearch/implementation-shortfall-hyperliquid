// Market universe: native Hyperliquid perps plus every HIP-3 deployer, with the
// live fee parameters each market carries (growth mode, deployer fee scale,
// collateral token) and the paper's asset-class / order-size calibration.
import { ALIGNED_QUOTE_TOKENS } from './config.js';
import { stitchBooks } from './book.js';

export const NATIVE_DEX = { name: '', label: 'HL', fullName: 'Hyperliquid (validator-operated)' };

// simulator.py size grids (USD), keyed by the paper's liquidity tiers.
export const SIZE_GRIDS = {
  CRYPTO_MAJOR:  [10_000, 100_000, 1_000_000, 5_000_000, 10_000_000],
  CRYPTO_NATIVE: [10_000, 50_000, 100_000, 250_000, 500_000],
  EQUITY_LARGE:  [1_000, 10_000, 50_000, 100_000, 250_000],
  PREIPO:        [1_000, 5_000, 10_000, 25_000, 50_000],
  THEMATIC:      [1_000, 5_000, 25_000, 50_000, 100_000],
};

// simulator.SIZE_GRID: explicit per-coin entries; everything else falls back to EQUITY_LARGE.
const PAPER_SIZE_TIER = {
  BTC: 'CRYPTO_MAJOR', ETH: 'CRYPTO_MAJOR', HYPE: 'CRYPTO_NATIVE',
  ANTHROPIC: 'PREIPO', SPACEX: 'PREIPO', OPENAI: 'PREIPO',
  MAG7: 'THEMATIC', SEMIS: 'THEMATIC', INFOTECH: 'THEMATIC',
};

export const ASSET_CLASSES = {
  crypto:    { label: 'Crypto' },
  equity:    { label: 'Equity' },
  index:     { label: 'Index / ETF' },
  commodity: { label: 'Commodity' },
  fx:        { label: 'FX' },
  rates:     { label: 'Rates' },
  preipo:    { label: 'Pre-IPO' },
  thematic:  { label: 'Thematic' },
};

const COMMODITIES = new Set(['GOLD', 'SILVER', 'CL', 'OIL', 'WTI', 'BRENTOIL', 'USOIL', 'NATGAS', 'GAS', 'TTF', 'HO', 'COPPER',
  'PLATINUM', 'PALLADIUM', 'URANIUM', 'ALUMINIUM', 'ALUMINUM', 'WHEAT', 'CORN', 'SOY', 'SOYBEAN', 'GOLDJM', 'SILVERJM',
  'COCOA', 'COFFEE', 'SUGAR', 'NICKEL', 'ZINC', 'LITHIUM']);
const FX = new Set(['EUR', 'JPY', 'GBP', 'KRW', 'DXY', 'CHF', 'AUD', 'CAD', 'CNH', 'CNY', 'MXN', 'BRL', 'INR', 'NZD', 'SGD', 'TRY']);
const RATES = new Set(['10Y', '2Y', '30Y', '5Y', 'USBOND', 'TLT', 'SOFR']);
const INDICES = new Set(['SP500', 'XYZ100', 'USA500', 'USA100', 'US500', 'USTECH', 'KR200', 'JP225', 'NIFTY', 'IBOV', 'SMALL2000',
  'VIX', 'VOL', 'BVIV', 'TOTAL2', 'OTHERS', 'BTCD', 'MAGS', 'SMH', 'SOXL', 'XBI', 'XLE', 'GDX', 'IGV', 'EWY', 'EWJ', 'EWT', 'EWZ',
  'URNM', 'KWEB', 'H100', 'DRAM', 'DAX', 'FTSE', 'CAC', 'HSI', 'NDX', 'RUT', 'DJIA', 'QQQ', 'SPY', 'IWM', 'ARKK', 'EEM', 'TQQQ']);
const PRIVATE = new Set(['ANTHROPIC', 'ANTH', 'OPENAI', 'OAI', 'SPACEX', 'SPCX', 'SHEIN', 'CXMT', 'YMTC', 'ZHIPU', 'MINIMAX', 'UNITREE',
  'ANT', 'DATABRICKS', 'STRIPE', 'XAI', 'PERPLEXITY', 'BYTEDANCE', 'REVOLUT', 'KRAKEN', 'DISCORD', 'CANVA', 'ANDURIL', 'SCALE',
  'RIPPLE', 'CONSENSYS', 'CURSOR', 'MISTRAL', 'GIGADEV', 'SNXX', 'KSTR', 'SHAZ', 'MUU', 'LYTE', 'NCLD', 'PURRDAT']);
const THEMATIC = new Set(['MAG7', 'SEMIS', 'INFOTECH', 'BIOTECH', 'DEFENSE', 'ENERGY', 'NUCLEAR', 'ROBOT', 'SEMI', 'USENERGY',
  'GLDMINE', 'AI', 'QUANTUM', 'SPACE']);

function stripCryptoPrefix(base) {
  return base.replace(/^k(?=[A-Z])/, '').replace(/^\d+(?=[A-Z])/, '');
}

export function classify(dexName, base, nativeNames) {
  if (!dexName) return 'crypto';
  const b = base.toUpperCase();
  if (COMMODITIES.has(b)) return 'commodity';
  if (FX.has(b)) return 'fx';
  if (RATES.has(b)) return 'rates';
  if (INDICES.has(b)) return 'index';
  if (THEMATIC.has(b)) return 'thematic';
  if (PRIVATE.has(b)) return 'preipo';
  if (nativeNames.has(b) || nativeNames.has(stripCryptoPrefix(b)) || /^(BTC|ETH|SOL|XRP|DOGE|HYPE|XMR|ZEC|BNB|LTC|ADA|SUI|LINK|BCH)$/.test(stripCryptoPrefix(b))) return 'crypto';
  return 'equity';
}

export function sizeGridFor(base, cls) {
  const explicit = PAPER_SIZE_TIER[base.toUpperCase()];
  if (explicit) return SIZE_GRIDS[explicit];
  if (cls === 'preipo') return SIZE_GRIDS.PREIPO;
  if (cls === 'thematic') return SIZE_GRIDS.THEMATIC;
  return SIZE_GRIDS.EQUITY_LARGE;
}

export function paperKeyFor(dexName, base) {
  return `${dexName || 'hyperliquid'}__${base}`;
}

function toNum(v) { const n = Number(v); return Number.isFinite(n) ? n : NaN; }

export function ctxFromRaw(c, now) {
  if (!c) return null;
  const mark = toNum(c.markPx), oi = toNum(c.openInterest);
  return {
    mark, mid: toNum(c.midPx), oracle: toNum(c.oraclePx), prevDayPx: toNum(c.prevDayPx),
    oi, oiUsd: Number.isFinite(oi) && Number.isFinite(mark) ? oi * mark : NaN,
    vol24h: toNum(c.dayNtlVlm), funding: toNum(c.funding), premium: toNum(c.premium),
    impactPxs: Array.isArray(c.impactPxs) ? c.impactPxs.map(toNum) : null,
    updatedAt: now,
  };
}

function applyMeta(market, u) {
  market.isDelisted = Boolean(u.isDelisted);
  market.growthMode = u.growthMode === 'enabled';
  market.deployerFeeScale = u.deployerFeeScale != null ? toNum(u.deployerFeeScale) : NaN;
  market.feeScaleChangedAt = u.lastFeeScaleChangeTime ?? null;
  market.szDecimals = u.szDecimals;
  market.maxLeverage = u.maxLeverage;
  market.onlyIsolated = Boolean(u.onlyIsolated);
  market.marginMode = u.marginMode ?? null;
}

/**
 * Load every perp dex and its markets.
 * @returns {{ dexes: Array, markets: Map<string, object>, tokens: Map<number,string> }}
 */
export async function loadUniverse(rest) {
  const [dexList, spot] = await Promise.all([rest.perpDexs(), rest.spotMeta()]);
  const tokens = new Map((spot?.tokens ?? []).map((t) => [t.index, t.name]));
  const dexes = [{ ...NATIVE_DEX, raw: null }];
  for (const d of dexList ?? []) {
    if (!d || !d.name) continue;
    dexes.push({ name: d.name, label: d.name, fullName: d.fullName || d.name, raw: d });
  }
  const payloads = await Promise.all(dexes.map((d) => rest.metaAndAssetCtxs(d.name).catch((err) => { console.warn('meta failed', d.name, err); return null; })));
  const markets = new Map();
  const now = Date.now();
  const nativeNames = new Set((payloads[0]?.[0]?.universe ?? []).map((u) => u.name.toUpperCase()));
  dexes.forEach((dex, i) => {
    const payload = payloads[i];
    if (!payload) { dex.liveCount = 0; dex.delistedCount = 0; dex.failed = true; return; }
    const [meta, ctxs] = payload;
    const collateralIdx = meta.collateralToken ?? 0;
    dex.quote = tokens.get(collateralIdx) ?? (dex.name ? `token#${collateralIdx}` : 'USDC');
    dex.aligned = ALIGNED_QUOTE_TOKENS.has(dex.quote);
    let live = 0, delisted = 0;
    (meta.universe ?? []).forEach((u, j) => {
      const name = u.name;
      const base = dex.name && name.startsWith(dex.name + ':') ? name.slice(dex.name.length + 1) : name;
      const cls = classify(dex.name, base, nativeNames);
      const m = {
        name, base, dex: dex.name, dexLabel: dex.label, dexFullName: dex.fullName, isHip3: Boolean(dex.name),
        quote: dex.quote, aligned: dex.aligned, cls, sizeGrid: sizeGridFor(base, cls), paperKey: paperKeyFor(dex.name, base),
        ctx: ctxFromRaw(ctxs?.[j], now),
        book: null, bookAt: 0, bookRequestedAt: 0, bookSource: null,
        bookCoarse: null, bookCoarseAt: 0, bookAgg: null, stitchCache: null,
      };
      applyMeta(m, u);
      if (m.isDelisted) delisted++; else live++;
      markets.set(name, m);
    });
    dex.liveCount = live; dex.delistedCount = delisted;
  });
  return { dexes, markets, tokens };
}

/** Refresh contexts (mark, OI, volume, funding) and the fee parameters for every dex. */
export async function refreshContexts(rest, universe) {
  const now = Date.now();
  await Promise.all(universe.dexes.map(async (dex) => {
    let payload;
    try { payload = await rest.metaAndAssetCtxs(dex.name); } catch (err) { console.warn('ctx refresh failed', dex.name, err); return; }
    const [meta, ctxs] = payload;
    (meta.universe ?? []).forEach((u, j) => {
      const m = universe.markets.get(u.name);
      if (!m) return;
      applyMeta(m, u);
      const ctx = ctxFromRaw(ctxs?.[j], now);
      if (ctx) m.ctx = ctx;
    });
  }));
}

/** The book used for costs and depth: full precision stitched with the aggregated book. */
export function effectiveBook(m) {
  if (!m.book && !m.bookCoarse) return null;
  const c = m.stitchCache;
  if (c && c.fine === m.book && c.coarse === m.bookCoarse) return c.book;
  const book = stitchBooks(m.book, m.bookCoarse);
  m.stitchCache = { fine: m.book, coarse: m.bookCoarse, book };
  return book;
}
