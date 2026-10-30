// Formatting and input-normalisation edge cases surfaced by the review.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fmtUsd, fmtQty, fmtBps, fmtPct, fmtSizeLabel, parseSizeInput, fmtPx } from '../../site/js/format.js';
import { normalizeProfile } from '../../site/js/state.js';
import { ctxFromRaw } from '../../site/js/universe.js';
import { costMarker } from '../../site/js/table.js';

test('negative residuals never print as −0', () => {
  assert.equal(fmtBps(-1e-13), '0.00');
  assert.equal(fmtBps(-0.001), '0.00');
  assert.equal(fmtBps(-0.006), '\u22120.01');
  assert.equal(fmtPct(-0.004, 0), '0%');
  assert.equal(fmtUsd(-0.001), '$0');
});

test('compaction rounds before choosing the unit', () => {
  assert.equal(fmtUsd(999950), '$1.00M');
  assert.equal(fmtUsd(999994999), '$999.99M');
  assert.equal(fmtUsd(999995001), '$1.00B');
  assert.equal(fmtUsd(9999), '$9,999');
  assert.equal(fmtUsd(10000), '$10.0K');
  assert.equal(fmtUsd(-45000), '\u2212$45.0K');
  assert.equal(fmtQty(999999), '1.00M');
  assert.equal(fmtQty(12345), '12.3K');
  assert.equal(fmtSizeLabel(1e10), '$10B');
  assert.equal(fmtSizeLabel(250000), '$250K');
  assert.equal(fmtSizeLabel(NaN), '\u2013');
  assert.equal(fmtPx(0.00001234), '0.00001234');
  assert.equal(fmtPx(80347.5), '80,347.5');
  assert.equal(parseSizeInput('10b'), 1e10);
  assert.ok(Number.isNaN(parseSizeInput('abc')));
  assert.ok(Number.isNaN(parseSizeInput('-5')));
});

test('a stored profile is normalised, never trusted', () => {
  const d = normalizeProfile(null);
  assert.deepEqual(d, { tier: 2, staking: 'none', referral: false, rebateTier: 0, builderBps: 0 });
  assert.equal(normalizeProfile({ tier: 'length' }).tier, 2);
  assert.equal(normalizeProfile({ tier: 99 }).tier, 2);
  assert.equal(normalizeProfile({ tier: 6 }).tier, 6);
  assert.equal(normalizeProfile({ staking: 'x' }).staking, 'none');
  assert.equal(normalizeProfile({ staking: 'diamond' }).staking, 'diamond');
  assert.equal(normalizeProfile({ builderBps: -5 }).builderBps, 0);
  assert.equal(normalizeProfile({ builderBps: '1e400' }).builderBps, 0);
  assert.equal(normalizeProfile({ builderBps: 1000 }).builderBps, 100);
  assert.equal(normalizeProfile({ referral: 'yes' }).referral, false);
  assert.equal(normalizeProfile({ rebateTier: 7 }).rebateTier, 0);
  assert.equal(normalizeProfile([1, 2]).tier, 2);
});

test('null exchange fields become NaN, not 0', () => {
  const c = ctxFromRaw({ midPx: null, markPx: null, openInterest: '0', dayNtlVlm: '0', funding: null, premium: null }, 1);
  assert.ok(Number.isNaN(c.mid) && Number.isNaN(c.mark) && Number.isNaN(c.funding) && Number.isNaN(c.premium));
  assert.equal(c.oi, 0);
  assert.equal(ctxFromRaw(null, 1), null);
});

test('cost markers: ≥ only for a partial fill inside the fine levels, ≈ whenever aggregated buckets were used', () => {
  assert.equal(costMarker(1, false), '');
  assert.equal(costMarker(1, true), '\u2248 ');
  assert.equal(costMarker(0.5, false), '\u2265 ');
  assert.equal(costMarker(0.5, true), '\u2248 ');
  assert.equal(costMarker(NaN, false), '');
});
