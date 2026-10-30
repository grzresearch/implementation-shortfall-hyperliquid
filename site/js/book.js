// Order-book mathematics.
//
// walkBook / cap5 / twapStatic are line-by-line ports of code/simulator.py;
// decompose() mirrors the spread/impact/fee split in code/is_decomp.py.
// The live decomposition is expressed per filled dollar: when the visible book
// covers the whole order it is identical to the paper's definition, and when it
// does not it is a lower bound for the full order (deeper levels are worse).
// Opportunity cost needs the post-trade price path and is therefore not shown live.

/** Parse a raw l2Book payload: levels[0] = bids (desc), levels[1] = asks (asc). */
export function parseBook(raw) {
  const toLevels = (arr) => (arr || []).map((l) => ({ px: Number(l.px), sz: Number(l.sz), n: Number(l.n) || 0 }))
    .filter((l) => Number.isFinite(l.px) && Number.isFinite(l.sz) && l.px > 0 && l.sz > 0);
  const bids = toLevels(raw?.levels?.[0]).sort((a, b) => b.px - a.px);
  const asks = toLevels(raw?.levels?.[1]).sort((a, b) => a.px - b.px);
  return { coin: raw?.coin ?? null, time: Number(raw?.time) || Date.now(), bids, asks };
}

export function bestBid(book) { return book?.bids?.[0]?.px ?? NaN; }
export function bestAsk(book) { return book?.asks?.[0]?.px ?? NaN; }

export function midOf(book) {
  const b = bestBid(book), a = bestAsk(book);
  if (Number.isFinite(b) && Number.isFinite(a)) return (a + b) / 2;
  return Number.isFinite(b) ? b : Number.isFinite(a) ? a : NaN;
}

export function spreadBpsOf(book) {
  const b = bestBid(book), a = bestAsk(book);
  if (!Number.isFinite(b) || !Number.isFinite(a) || a <= 0) return NaN;
  return 1e4 * (a - b) / ((a + b) / 2);
}

/** simulator.walk_book */
export function walkBook(levels, targetUsd) {
  let remaining = targetUsd;
  const fills = [];
  for (let i = 0; i < levels.length; i++) {
    if (remaining <= 0) return { fills, remainingUsd: 0, nLevels: i };
    const { px, sz } = levels[i];
    const notional = px * sz;
    if (notional >= remaining) {
      fills.push({ px, qty: remaining / px });
      return { fills, remainingUsd: 0, nLevels: i + 1 };
    }
    fills.push({ px, qty: sz });
    remaining -= notional;
  }
  return { fills, remainingUsd: remaining, nLevels: levels.length };
}

/** simulator.strat_naive */
export function stratNaive(levels, sizeUsd) {
  const r = walkBook(levels, sizeUsd);
  return { ...r, nSlices: 1 };
}

/** simulator.strat_cap5 — take at most 5 % of every level. */
export function stratCap5(levels, sizeUsd) {
  const capped = levels.map((l) => ({ px: l.px, sz: l.sz * 0.05 }));
  const r = walkBook(capped, sizeUsd);
  return { ...r, nSlices: 1 };
}

/** simulator.strat_twap_static — n equal slices, each walking the same static book. */
export function stratTwapStatic(levels, sizeUsd, nSlices) {
  const slice = sizeUsd / nSlices;
  const fills = [];
  let remainingUsd = 0;
  let nLevels = 0;
  for (let i = 0; i < nSlices; i++) {
    const r = walkBook(levels, slice);
    fills.push(...r.fills);
    remainingUsd += r.remainingUsd;
    nLevels = Math.max(nLevels, r.nLevels);
  }
  return { fills, remainingUsd, nLevels, nSlices };
}

/** Walk only the levels at or better than a limit price (IOC / crossing limit). */
export function stratLimitIoc(levels, sizeUsd, limitPx, side) {
  const eligible = side === 'BUY' ? levels.filter((l) => l.px <= limitPx) : levels.filter((l) => l.px >= limitPx);
  const r = walkBook(eligible, sizeUsd);
  return { ...r, nSlices: 1 };
}

/** simulator.fills_to_run (the aggregate part). */
export function summarizeFills(fills) {
  let filledUsd = 0, filledQty = 0;
  for (const f of fills) { filledUsd += f.px * f.qty; filledQty += f.qty; }
  return { filledUsd, filledQty, vwap: filledQty > 0 ? filledUsd / filledQty : NaN };
}

/**
 * is_decomp.py decomposition, per filled dollar.
 *   spread  = (touch − mid) / mid            (cost of crossing the touch)
 *   impact  = (vwap − touch) / mid           (cost of walking the book)
 *   fee     = taker rate
 * Multiply each by fillFrac to recover the paper's definition exactly.
 */
export function decompose({ side, mid, bestBid: bid, bestAsk: ask, vwap, filledUsd, sizeUsd, takerBps }) {
  const isBuy = side === 'BUY';
  const touch = isBuy ? ask : bid;
  const fillFrac = sizeUsd > 0 ? filledUsd / sizeUsd : 0;
  if (!(mid > 0) || !Number.isFinite(touch) || !(filledUsd > 0) || !Number.isFinite(vwap)) {
    return { spreadBps: NaN, impactBps: NaN, feeBps: takerBps, totalBps: NaN, fillFrac };
  }
  const spreadBps = 1e4 * (isBuy ? ask - mid : mid - bid) / mid;
  const impactBps = 1e4 * (isBuy ? vwap - ask : bid - vwap) / mid;
  const feeBps = takerBps;
  return { spreadBps, impactBps, feeBps, totalBps: spreadBps + impactBps + feeBps, fillFrac };
}

/**
 * Full taker execution-cost estimate for one side / size / strategy.
 * strategy: 'market' | 'cap5' | 'twap' (nSlices) | 'limit' (limitBps from the touch)
 */
export function execCost({ side, book, sizeUsd, takerBps, strategy = 'market', nSlices = 1, limitBps = 0 }) {
  const isBuy = side === 'BUY';
  const levels = isBuy ? book?.asks : book?.bids;
  const mid = midOf(book);
  if (!levels || levels.length === 0 || !(mid > 0) || !(sizeUsd > 0)) return null;
  let run;
  switch (strategy) {
    case 'cap5': run = stratCap5(levels, sizeUsd); break;
    case 'twap': run = stratTwapStatic(levels, sizeUsd, Math.max(1, nSlices | 0)); break;
    case 'limit': {
      const touch = levels[0].px;
      const limitPx = isBuy ? touch * (1 + limitBps / 1e4) : touch * (1 - limitBps / 1e4);
      run = stratLimitIoc(levels, sizeUsd, limitPx, side);
      break;
    }
    default: run = stratNaive(levels, sizeUsd);
  }
  const agg = summarizeFills(run.fills);
  const d = decompose({ side, mid, bestBid: bestBid(book), bestAsk: bestAsk(book), vwap: agg.vwap, filledUsd: agg.filledUsd, sizeUsd, takerBps });
  const coverage = sizeUsd > 0 ? agg.filledUsd / sizeUsd : 0;
  return {
    side, strategy, sizeUsd, mid,
    filledUsd: agg.filledUsd, filledQty: agg.filledQty, vwap: agg.vwap,
    unfilledUsd: run.remainingUsd, coverage, nLevels: run.nLevels, nSlices: run.nSlices,
    lastPx: run.fills.length ? run.fills[run.fills.length - 1].px : NaN,
    deepestAgg: run.fills.length ? Boolean(levels[Math.min(run.nLevels, levels.length) - 1]?.agg) : false,
    spreadBps: d.spreadBps, impactBps: d.impactBps, feeBps: d.feeBps, totalBps: d.totalBps,
    totalUsd: Number.isFinite(d.totalBps) ? d.totalBps / 1e4 * agg.filledUsd : NaN,
    liquidity: 'taker',
  };
}

/**
 * Passive (maker) execution: resting at the touch, improved by improveBps into the
 * spread, or averaged extraBps deeper into the book (scale orders). Relative to the
 * arrival mid the resting order *captures* the half-spread, so the spread component is
 * negative. Conditional on being filled; adverse selection is not modelled.
 */
export function makerCost({ side, book, sizeUsd, makerBps, improveBps = 0, extraBps = 0 }) {
  const isBuy = side === 'BUY';
  const mid = midOf(book);
  const bid = bestBid(book), ask = bestAsk(book);
  if (!(mid > 0) || !Number.isFinite(bid) || !Number.isFinite(ask) || !(sizeUsd > 0)) return null;
  const halfSpreadBps = 1e4 * (isBuy ? mid - bid : ask - mid) / mid;
  const spreadBps = -(halfSpreadBps - improveBps);
  const impactBps = -extraBps;
  const totalBps = spreadBps + impactBps + makerBps;
  const touchLevel = isBuy ? book.bids[0] : book.asks[0];
  return {
    side, sizeUsd, mid, coverage: 1, liquidity: 'maker',
    spreadBps, impactBps, feeBps: makerBps, totalBps,
    totalUsd: totalBps / 1e4 * sizeUsd,
    queueAheadUsd: touchLevel ? touchLevel.px * touchLevel.sz : 0,
    restingPx: isBuy ? bid * (1 + improveBps / 1e4) : ask * (1 - improveBps / 1e4),
  };
}

/** Cumulative depth ladder with distance from mid in bps (negative on the bid side). */
export function cumulativeLevels(levels, mid) {
  let cumSz = 0, cumUsd = 0;
  return levels.map((l) => {
    cumSz += l.sz; cumUsd += l.px * l.sz;
    return { ...l, cumSz, cumUsd, bps: mid > 0 ? 1e4 * (l.px - mid) / mid : NaN };
  });
}

/** Notional resting within ±bandBps of the mid on one side. */
export function depthWithin(levels, mid, bandBps) {
  if (!(mid > 0)) return NaN;
  let usd = 0;
  for (const l of levels) {
    if (Math.abs(1e4 * (l.px - mid) / mid) > bandBps) break;
    usd += l.px * l.sz;
  }
  return usd;
}

export function visibleDepth(levels) {
  let usd = 0;
  for (const l of levels) usd += l.px * l.sz;
  return usd;
}

/** Summary statistics used by the market table. */
export function bookStats(book, bands) {
  const mid = midOf(book);
  const out = {
    mid, bestBid: bestBid(book), bestAsk: bestAsk(book), spreadBps: spreadBpsOf(book),
    bidDepth: {}, askDepth: {},
    bidVisible: visibleDepth(book?.bids ?? []), askVisible: visibleDepth(book?.asks ?? []),
    bidLevels: book?.bids?.length ?? 0, askLevels: book?.asks?.length ?? 0,
  };
  for (const b of bands) {
    out.bidDepth[b] = depthWithin(book?.bids ?? [], mid, b);
    out.askDepth[b] = depthWithin(book?.asks ?? [], mid, b);
  }
  return out;
}

function validLevel(l) {
  if (!l || typeof l !== 'object') return null;
  const px = Number(l.px), sz = Number(l.sz), n = Number(l.n) || 0;
  return Number.isFinite(px) && px > 0 && Number.isFinite(sz) && sz > 0 ? { px, sz, n } : null;
}

/**
 * Fold a bbo update into a stored book: replaces the top-of-book, trims crossed levels.
 * Malformed levels are ignored; a crossed pair (bid ≥ ask) is ignored as a whole.
 */
export function applyBbo(book, bbo, time = 0) {
  if (!book || !Array.isArray(bbo)) return book;
  if (time && book.time && time < book.time) return book;
  const bid = validLevel(bbo[0]), ask = validLevel(bbo[1]);
  if (!bid && !ask) return book;
  if (bid && ask && bid.px >= ask.px) return book;
  const next = { ...book, bids: book.bids.slice(), asks: book.asks.slice(), time: Math.max(book.time || 0, time || 0) };
  if (bid) {
    next.bids = next.bids.filter((l) => l.px < bid.px);
    next.bids.unshift(bid);
    next.asks = next.asks.filter((l) => l.px > bid.px);
  }
  if (ask) {
    next.asks = next.asks.filter((l) => l.px > ask.px);
    next.asks.unshift(ask);
    next.bids = next.bids.filter((l) => l.px < ask.px);
  }
  return next;
}

// ---------------------------------------------------------------------------
// Aggregated books (nSigFigs) and stitching
// ---------------------------------------------------------------------------

/** Price bucket width of an nSigFigs-aggregated level at this price. */
export function bucketSize(px, nSigFigs) {
  return 10 ** (Math.floor(Math.log10(px)) - (nSigFigs - 1));
}

/**
 * Finest aggregation whose 20 levels span more than minSpanBps from the touch.
 * 4 significant figures span 20–200 bps for every price (5 span 2–20), so the default
 * threshold of 20 bps selects nSigFigs = 4 everywhere, with buckets of 1–10 bps.
 */
export function chooseSigFigs(px, minSpanBps = 20) {
  if (!(px > 0)) return 4;
  for (const n of [5, 4, 3, 2]) {
    if ((20 * bucketSize(px, n)) / px * 1e4 > minSpanBps) return n;
  }
  return 2;
}

/** Distance (bps) from the mid to the deepest level of one side. */
export function reachBps(levels, mid) {
  if (!levels?.length || !(mid > 0)) return 0;
  return Math.abs(1e4 * (levels[levels.length - 1].px - mid) / mid);
}

function stitchSide(fine, coarse, nSigFigs, isAsk) {
  if (!coarse?.length) return fine ?? [];
  if (!fine?.length) return coarse.map((l) => ({ ...l, agg: true }));
  const lastFine = fine[fine.length - 1].px;
  const out = fine.slice();
  for (const c of coarse) {
    // Asks are rounded up, so bucket P holds (P − b, P]; bids are rounded down, so P holds [P, P + b).
    const b = bucketSize(isAsk ? c.px * (1 - 1e-9) : c.px, nSigFigs);
    const eps = b * 1e-6;
    const lo = isAsk ? c.px - b : c.px;
    const hi = isAsk ? c.px : c.px + b;
    const fullyInsideFine = isAsk ? hi <= lastFine + eps : lo >= lastFine - eps;
    if (fullyInsideFine) continue;
    let seen = 0;
    for (const f of fine) {
      const inside = isAsk ? (f.px > lo + eps && f.px <= hi + eps) : (f.px >= lo - eps && f.px < hi - eps);
      if (inside) seen += f.sz;
    }
    const sz = c.sz - seen;
    if (sz > eps) out.push({ px: c.px, sz, n: c.n, agg: true });
  }
  return out;
}

/**
 * Combine a full-precision book with an aggregated one: the fine levels as they are,
 * then every aggregated bucket beyond them, net of the fine quantity it already contains.
 * Aggregated prices are the conservative bucket edge, so costs from the stitched book are
 * exact inside the fine range and an upper bound beyond it.
 */
export function stitchBooks(fine, coarse) {
  if (!coarse) return fine;
  if (!fine) return { ...coarse, bids: coarse.bids.map((l) => ({ ...l, agg: true })), asks: coarse.asks.map((l) => ({ ...l, agg: true })) };
  const n = coarse.nSigFigs ?? 3;
  return {
    coin: fine.coin, time: Math.max(fine.time, coarse.time), nSigFigs: null, stitched: true,
    bids: stitchSide(fine.bids, coarse.bids, n, false),
    asks: stitchSide(fine.asks, coarse.asks, n, true),
  };
}
