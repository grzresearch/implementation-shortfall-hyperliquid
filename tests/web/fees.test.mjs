// The fee engine must reproduce every row of the committed fee schedule.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { feeRates, hip3Scales, TIERS } from '../../site/js/fees.js';

const here = path.dirname(fileURLToPath(import.meta.url));
const csvPath = path.join(here, '..', '..', 'results', 'hip3_fee_schedule.csv');

function readCsv(p) {
  const text = readFileSync(p, 'utf8').replace(/^﻿/, '');
  const [head, ...lines] = text.trim().split(/\r?\n/);
  const cols = head.split(',');
  return lines.map((l) => Object.fromEntries(l.split(',').map((v, i) => [cols[i], v])));
}

const near = (a, b, tol = 2e-4) => Math.abs(a - b) <= tol;

test('hip3Scales', () => {
  assert.deepEqual(hip3Scales(1), { scaleIfHip3: 2, deployerShare: 0.5 });
  assert.deepEqual(hip3Scales(2), { scaleIfHip3: 4, deployerShare: 0.5 });
  const s = hip3Scales(0.1111);
  assert.ok(near(s.scaleIfHip3, 1.1111) && near(s.deployerShare, 0.1111 / 1.1111));
  assert.deepEqual(hip3Scales('nope'), { scaleIfHip3: 1, deployerShare: 0 });
});

test('native Hyperliquid tiers and discounts', () => {
  const native = { isHip3: false };
  const t2 = feeRates(native, { tier: 2, staking: 'none', referral: false, rebateTier: 0, builderBps: 0 });
  assert.equal(t2.takerBps, 3.5);
  assert.equal(t2.makerBps, 0.8);
  const diamond = feeRates(native, { tier: 0, staking: 'diamond', referral: false, rebateTier: 0, builderBps: 0 });
  assert.ok(near(diamond.takerBps, 2.7) && near(diamond.makerBps, 0.9));
  const silver1 = feeRates(native, { tier: 1, staking: 'silver', referral: true, rebateTier: 0, builderBps: 0 });
  assert.ok(near(silver1.takerBps, 4.0 * 0.85 * 0.96));
  const rebate = feeRates(native, { tier: 2, staking: 'none', referral: false, rebateTier: 2, builderBps: 1 });
  assert.ok(near(rebate.makerBps, -0.2 + 1) && near(rebate.takerBps, 4.5));
  assert.equal(TIERS.length, 7);
});

test('every row of results/hip3_fee_schedule.csv is reproduced', () => {
  const rows = readCsv(csvPath);
  assert.ok(rows.length > 100);
  const base = { staking: 'none', referral: false, rebateTier: 0, builderBps: 0 };
  for (const r of rows) {
    const gm = r.growth_mode === 'yes';
    const aligned = r.is_aligned_quote === 'yes';
    const mk = (growthMode, al) => ({ isHip3: true, deployerFeeScale: Number(r.deployer_fee_scale), growthMode, aligned: al });
    const label = `${r.deployer_handle}:${r.coin}`;
    assert.ok(near(hip3Scales(Number(r.deployer_fee_scale)).scaleIfHip3, Number(r.scale_if_hip3)), `${label} scale_if_hip3`);
    assert.ok(near(feeRates(mk(false, false), { ...base, tier: 0 }).takerBps, Number(r.taker_tier0_base_bps)), `${label} tier0 base`);
    assert.ok(near(feeRates(mk(gm, false), { ...base, tier: 0 }).takerBps, Number(r.taker_tier0_growth_bps)), `${label} tier0 growth`);
    assert.ok(near(feeRates(mk(gm, aligned), { ...base, tier: 0 }).takerBps, Number(r.taker_tier0_growth_aligned)), `${label} tier0 growth aligned`);
    assert.ok(near(feeRates(mk(false, false), { ...base, tier: 2 }).takerBps, Number(r.taker_tier2_base_bps)), `${label} tier2 base`);
    assert.ok(near(feeRates(mk(gm, aligned), { ...base, tier: 2 }).takerBps, Number(r.taker_tier2_growth_aligned)), `${label} tier2 growth aligned`);
  }
});

test('HIP-3 maker side: growth mode scales positive fees, rebates only get the aligned boost', () => {
  const xyz = { isHip3: true, deployerFeeScale: 1, growthMode: true, aligned: false };
  const f = feeRates(xyz, { tier: 2, staking: 'none', referral: false, rebateTier: 0, builderBps: 0 });
  assert.ok(near(f.makerBps, 0.8 * 0.1 * 2));
  const flx = { isHip3: true, deployerFeeScale: 1, growthMode: true, aligned: true };
  const reb = feeRates(flx, { tier: 2, staking: 'none', referral: false, rebateTier: 3, builderBps: 0 });
  // −0.3 × 0.1 (growth) × ((1 − 0.5) × 1.5 + 0.5) = −0.0375
  assert.ok(near(reb.makerBps, -0.3 * 0.1 * 1.25));
  assert.ok(near(reb.takerBps, 3.5 * 0.1 * 2 * 0.9));
});
