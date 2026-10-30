// Parity of the JS order-book walker with code/simulator.py.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import {
  walkBook, stratNaive, stratCap5, stratTwapStatic, summarizeFills, decompose,
  execCost, makerCost, depthWithin, applyBbo, parseBook,
} from '../../site/js/book.js';

const here = path.dirname(fileURLToPath(import.meta.url));

// Deterministic pseudo-random books (LCG) so failures are reproducible.
function lcg(seed) { let s = seed >>> 0; return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 2 ** 32); }
function randomLevels(rand, side, mid, n) {
  const levels = [];
  let px = mid;
  for (let i = 0; i < n; i++) {
    px = side === 'ask' ? px * (1 + rand() * 0.002 + 1e-5) : px * (1 - rand() * 0.002 - 1e-5);
    levels.push([Number(px.toFixed(4)), Number((rand() * 50 + 0.01).toFixed(3))]);
  }
  return levels;
}

function buildCases() {
  const rand = lcg(42);
  const cases = [];
  const sizes = [0, 1, 100, 1_000, 10_000, 100_000, 1_000_000, 10_000_000];
  for (let b = 0; b < 12; b++) {
    const mid = [0.01, 1, 100, 400, 80_000][b % 5] * (0.8 + rand() * 0.4);
    const levels = randomLevels(rand, b % 2 ? 'ask' : 'bid', mid, 1 + Math.floor(rand() * 20));
    for (const size of sizes) {
      cases.push({ levels, size, strategy: 'market' });
      cases.push({ levels, size, strategy: 'cap5' });
      cases.push({ levels, size, strategy: 'twap', n: [1, 5, 30][b % 3] });
    }
  }
  return cases;
}

function runJs(c) {
  const levels = c.levels.map(([px, sz]) => ({ px, sz }));
  const r = c.strategy === 'cap5' ? stratCap5(levels, c.size)
    : c.strategy === 'twap' ? stratTwapStatic(levels, c.size, c.n)
    : stratNaive(levels, c.size);
  const agg = summarizeFills(r.fills);
  return { fills: r.fills.map((f) => [f.px, f.qty]), remaining: r.remainingUsd, n_levels: r.nLevels, n_slices: r.nSlices, ...agg };
}

function close(a, b, tol = 1e-9) {
  return Math.abs(a - b) <= tol * Math.max(1, Math.abs(a), Math.abs(b));
}

test('walkBook / cap5 / twap reproduce code/simulator.py', (t) => {
  const cases = buildCases();
  const py = spawnSync('python3', ['-I', path.join(here, 'book_oracle.py')], { input: JSON.stringify({ cases }), encoding: 'utf8' });
  if (py.error || py.status !== 0) {
    t.skip(`python3 oracle unavailable: ${py.error?.message ?? py.stderr}`);
    return;
  }
  const expected = JSON.parse(py.stdout);
  assert.equal(expected.length, cases.length);
  cases.forEach((c, i) => {
    const js = runJs(c);
    const ex = expected[i];
    const where = `case ${i} (${c.strategy}, size ${c.size})`;
    assert.equal(js.fills.length, ex.fills.length, `${where}: fill count`);
    js.fills.forEach(([px, qty], j) => {
      assert.ok(close(px, ex.fills[j][0]) && close(qty, ex.fills[j][1]), `${where}: fill ${j}`);
    });
    assert.ok(close(js.remaining, ex.remaining), `${where}: remaining ${js.remaining} vs ${ex.remaining}`);
    assert.equal(js.n_levels, ex.n_levels, `${where}: n_levels`);
    assert.equal(js.n_slices, ex.n_slices, `${where}: n_slices`);
    assert.ok(close(js.filledUsd, ex.filled_usd), `${where}: filled_usd`);
    assert.ok(close(js.filledQty, ex.filled_qty), `${where}: filled_qty`);
    if (ex.filled_qty > 0) assert.ok(close(js.vwap, ex.vwap), `${where}: vwap`);
  });
});

test('walkBook edge cases', () => {
  assert.deepEqual(walkBook([], 100), { fills: [], remainingUsd: 100, nLevels: 0 });
  assert.deepEqual(walkBook([{ px: 10, sz: 1 }], 0), { fills: [], remainingUsd: 0, nLevels: 0 });
  const r = walkBook([{ px: 10, sz: 1 }, { px: 11, sz: 1 }], 15);
  assert.deepEqual(r.fills, [{ px: 10, qty: 1 }, { px: 11, qty: 5 / 11 }]);
  assert.equal(r.nLevels, 2);
});

test('decompose matches is_decomp.py semantics (× fillFrac recovers the paper definition)', () => {
  const book = parseBook({ coin: 'T', time: 1, levels: [[{ px: '99', sz: '10', n: 1 }], [{ px: '101', sz: '1', n: 1 }, { px: '102', sz: '5', n: 2 }]] });
  const full = execCost({ side: 'BUY', book, sizeUsd: 300, takerBps: 3.5 });
  // fills: 1 @ 101, 199/102 @ 102
  const qty = 1 + 199 / 102;
  const vwap = 300 / qty;
  assert.ok(close(full.vwap, vwap));
  assert.ok(close(full.spreadBps, 1e4 * (101 - 100) / 100));
  assert.ok(close(full.impactBps, 1e4 * (vwap - 101) / 100));
  assert.ok(close(full.feeBps, 3.5));
  assert.ok(close(full.totalBps, full.spreadBps + full.impactBps + 3.5));
  assert.equal(full.coverage, 1);

  const partial = execCost({ side: 'BUY', book, sizeUsd: 2000, takerBps: 3.5 });
  const filled = 101 + 510;
  assert.ok(close(partial.filledUsd, filled));
  assert.ok(close(partial.coverage, filled / 2000));
  // Paper: spread_bps = 1e4 (ask − mid)/mid × fill_frac
  const paperSpread = 1e4 * (101 - 100) / 100 * (filled / 2000);
  assert.ok(close(partial.spreadBps * partial.coverage, paperSpread));
  const paperFee = 3.5 * (filled / 2000);
  assert.ok(close(partial.feeBps * partial.coverage, paperFee));

  const sell = execCost({ side: 'SELL', book, sizeUsd: 500, takerBps: 3.5 });
  assert.ok(close(sell.spreadBps, 1e4 * (100 - 99) / 100));
  assert.ok(close(sell.impactBps, 0));
});

test('makerCost captures the half-spread and charges the maker fee', () => {
  const book = parseBook({ levels: [[{ px: '99', sz: '2' }], [{ px: '101', sz: '3' }]] });
  const m = makerCost({ side: 'BUY', book, sizeUsd: 1000, makerBps: 0.8 });
  assert.ok(close(m.spreadBps, -100));
  assert.ok(close(m.totalBps, -100 + 0.8));
  assert.ok(close(m.queueAheadUsd, 99 * 2));
  const rebate = makerCost({ side: 'SELL', book, sizeUsd: 1000, makerBps: -0.2, extraBps: 25 });
  assert.ok(close(rebate.totalBps, -100 - 25 - 0.2));
});

test('applyBbo ignores malformed, crossed or stale updates', () => {
  const book = parseBook({ time: 100, levels: [[{ px: '99', sz: '1' }, { px: '98', sz: '1' }], [{ px: '101', sz: '1' }, { px: '102', sz: '1' }]] });
  assert.equal(applyBbo(book, [{ px: 'abc', sz: '1' }, { px: '101', sz: '1' }]).bids.length, 2, 'bad bid ignored, good ask applied');
  assert.equal(applyBbo(book, [5, null]), book, 'non-object levels ignored');
  assert.equal(applyBbo(book, [{ px: '0', sz: '0' }, null]), book, 'zero price ignored');
  assert.equal(applyBbo(book, [{ px: '101.5', sz: '1' }, { px: '101', sz: '1' }]), book, 'crossed pair ignored');
  assert.equal(applyBbo(book, [{ px: '99.5', sz: '1' }, null], 50), book, 'older than the book ignored');
  const ok = applyBbo(book, [{ px: '99.5', sz: '2' }, null], 150);
  assert.equal(ok.bids[0].px, 99.5);
  assert.equal(ok.time, 150);
});

test('depthWithin and applyBbo', () => {
  const book = parseBook({ levels: [
    [{ px: '99.9', sz: '1' }, { px: '99.5', sz: '2' }, { px: '98', sz: '3' }],
    [{ px: '100.1', sz: '1' }, { px: '100.5', sz: '2' }, { px: '102', sz: '3' }],
  ] });
  assert.ok(close(depthWithin(book.bids, 100, 10), 99.9));
  assert.ok(close(depthWithin(book.bids, 100, 60), 99.9 + 199));
  assert.ok(close(depthWithin(book.asks, 100, 60), 100.1 + 201));
  const next = applyBbo(book, [{ px: '100.0', sz: '5', n: 2 }, { px: '100.1', sz: '0.5', n: 1 }]);
  assert.equal(next.bids[0].px, 100);
  assert.equal(next.bids.length, 4);
  assert.equal(next.asks[0].sz, 0.5);
  assert.equal(next.asks.length, 3);
  // original untouched
  assert.equal(book.bids.length, 3);
});

test('aggregation helpers: bucketSize and chooseSigFigs', async () => {
  const { bucketSize, chooseSigFigs } = await import('../../site/js/book.js');
  const { COARSE_MIN_SPAN_BPS } = await import('../../site/js/config.js');
  // The configured span must select 4 significant figures for every price (buckets of 1–10 bps).
  for (const px of [0.0123, 0.85, 1.01, 9.9, 90.9, 401.5, 1136, 2326, 8000, 81075, 99999, 100000]) {
    assert.equal(chooseSigFigs(px, COARSE_MIN_SPAN_BPS), 4, `configured span picks 4 sf at ${px}`);
  }
  assert.equal(bucketSize(80451, 4), 10);
  assert.equal(bucketSize(401.5, 3), 1);
  assert.ok(close(bucketSize(0.0123, 3), 0.0001));
  assert.equal(chooseSigFigs(79000, 20), 4);   // $10 buckets → 25 bps span
  assert.equal(chooseSigFigs(2326, 20), 4);    // $1 buckets → 86 bps
  assert.equal(chooseSigFigs(401.5, 20), 4);   // $0.1 buckets → 50 bps
  assert.equal(chooseSigFigs(90.9, 20), 4);    // 0.01 buckets → 22 bps
  assert.equal(chooseSigFigs(1136, 20), 4);    // $1 buckets → 176 bps
  // 4 significant figures always span 20–200 bps, so a wider target falls back to coarser books
  assert.equal(chooseSigFigs(1136, 300), 3);   // $10 buckets → 1,760 bps
  assert.equal(chooseSigFigs(90.9, 300), 2);   // 3 sf would span only 220 bps
  assert.equal(chooseSigFigs(NaN), 4);
});

test('stitchBooks appends aggregated buckets net of the fine quantity they contain', async () => {
  const { stitchBooks } = await import('../../site/js/book.js');
  const fine = parseBook({ coin: 'T', time: 1, levels: [
    [{ px: '99.85', sz: '1' }, { px: '99.82', sz: '2' }],
    [{ px: '101', sz: '1' }, { px: '101.2', sz: '2' }, { px: '101.5', sz: '3' }],
  ] });
  // 3 significant figures: asks around 101 are $1 buckets rounded up, bids around 99.8 are $0.1 buckets rounded down.
  const coarse = parseBook({ coin: 'T', time: 2, levels: [
    [{ px: '99.9', sz: '7' }, { px: '99.8', sz: '10' }, { px: '99.7', sz: '4' }],
    [{ px: '101', sz: '1' }, { px: '102', sz: '9' }, { px: '103', sz: '4' }],
  ] });
  coarse.nSigFigs = 3;
  const s = stitchBooks(fine, coarse);
  assert.ok(s.stitched);
  // asks: fine levels kept; bucket 101 (100,101] is fully inside the fine range → skipped;
  // bucket 102 (101,102] holds fine 101.2 + 101.5 = 5 → net 4; bucket 103 → 4.
  assert.deepEqual(s.asks.map((l) => [l.px, l.sz, Boolean(l.agg)]), [[101, 1, false], [101.2, 2, false], [101.5, 3, false], [102, 4, true], [103, 4, true]]);
  // bids: bucket 99.9 [99.9,100) lies inside the fine range → skipped; 99.8 [99.8,99.9) holds 3 → net 7; 99.7 → 4.
  assert.deepEqual(s.bids.map((l) => [l.px, l.sz, Boolean(l.agg)]), [[99.85, 1, false], [99.82, 2, false], [99.8, 7, true], [99.7, 4, true]]);
  // a coarse bucket whose fine content exceeds its size is dropped
  const stale = parseBook({ levels: [[], [{ px: '102', sz: '2' }]] });
  stale.nSigFigs = 3;
  assert.equal(stitchBooks(fine, stale).asks.length, 3);
  // no coarse book → fine unchanged; no fine book → coarse marked aggregated
  assert.equal(stitchBooks(fine, null), fine);
  assert.ok(stitchBooks(null, coarse).asks.every((l) => l.agg));
});
