// Central configuration for the live execution-cost dashboard.
// Every number that encodes an assumption lives here so it can be audited in one place.

export const API = {
  info: 'https://api.hyperliquid.xyz/info',
  ws: 'wss://api.hyperliquid.xyz/ws',
};

// REST budget: 1200 weight per minute per IP (Hyperliquid docs, "Rate limits").
// l2Book and allMids cost 2, every other documented info request costs 20. Every request
// of this page goes through one sliding meter capped at `softCap`; the sweep additionally
// keeps `sweepReserve` free for the selected market's polls and the context refresh.
export const RATE = {
  windowMs: 60_000,
  budget: 1200,
  softCap: 800,
  sweepReserve: 120,
  weights: { l2Book: 2, allMids: 2, default: 20 },
  backoffOn429Ms: 15_000,
};

// The sweep refreshes one order book per tick, stalest-first (visible rows first, every
// fourth tick the hidden ones). 4 books/s × weight 2 = 480 weight/min at most; with the
// context refresh (≈110/min) and the selected-market polls (≤ 80/min) one tab stays
// under ~700 of the 800 soft cap. Only one tab per browser profile runs at this pace
// (Web Locks leader election); other tabs follow at `followerTickMs` and skip the bbo feed.
export const SWEEP = {
  tickMs: 250,
  followerTickMs: 1_000,
  hiddenShareEvery: 4,
  minRefreshMs: 12_000,
  ctxRefreshMs: 120_000,
  followerCtxRefreshMs: 240_000,
  hiddenTabTickMs: 2_000,
  selectedPollMs: 3_000,
  freshMs: 180_000,
  agePulseMs: 5_000,
};

// WebSocket keep-alive: ping every 50 s, treat 90 s of silence as a dead connection.
export const WS = { pingMs: 50_000, staleMs: 90_000, stableAfterMs: 10_000, lockName: 'hl-cost-monitor.sweeper' };

// The 20 full-precision levels of a tight-tick market span only a few bps. Every market
// therefore also gets an aggregated book (nSigFigs) when its full book reaches less than
// COARSE_NEEDED_BELOW_BPS; the aggregation is the finest one whose 20 buckets span at least
// COARSE_MIN_SPAN_BPS. A 4-significant-figure book always spans 20–200 bps, so 20 selects
// nSigFigs = 4 for every price (buckets of 1–10 bps).
export const COARSE_MIN_SPAN_BPS = 20;
export const COARSE_NEEDED_BELOW_BPS = 100;

// l2Book returns at most 20 levels per side; the cost figures are bounded by that.
export const BOOK_LEVELS = 20;

// Quote assets that qualify for the aligned-quote-asset discount: 20 % lower taker fees and
// 50 % better maker rebates (Hyperliquid docs → Trading → Fees, "Aligned quote assets";
// the taker side is also derived in docs/FEE_CALIBRATION.md).
export const ALIGNED_QUOTE_TOKENS = new Set(['USDH']);

export const SIZE_PRESETS = [1_000, 10_000, 50_000, 100_000, 250_000, 1_000_000, 5_000_000, 10_000_000];
export const DEPTH_BANDS_BPS = [10, 25, 50, 100];

// Trader profile defaults mirror the paper's baseline: Tier 2, no staking, no referral.
export const DEFAULT_PROFILE = { tier: 2, staking: 'none', referral: false, rebateTier: 0, builderBps: 0 };

// Order-type parameters.
export const LIMIT_OFFSET_BPS = 10;     // "Limit IOC": cross up to touch ± 10 bps
export const SCALE_RANGE_BPS = 50;      // "Scale": ladder from the touch 50 bps into the book
export const TWAP_SUBORDER_SEC = 30;    // native TWAP sends a sub-order every 30 s
export const TWAP_VARIANTS = [
  { id: 'twap5', label: 'TWAP 5 min', minutes: 5 },
  { id: 'twap30', label: 'TWAP 30 min', minutes: 30 },
  { id: 'twap60', label: 'TWAP 1 h', minutes: 60 },
];

// Grid for the cost-vs-size curve.
export const CURVE_SIZES = [1e3, 2.5e3, 5e3, 1e4, 2.5e4, 5e4, 1e5, 2.5e5, 5e5, 1e6, 2.5e6, 5e6, 1e7];

export const PAPER = {
  period: '15 Dec 2025 – 12 May 2026',
  repo: 'https://github.com/grzresearch/implementation-shortfall-hyperliquid',
  summaryCsv: ['data/summary_stats.csv', '../results/summary_stats.csv'],
};
