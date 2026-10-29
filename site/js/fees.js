// Hyperliquid fee engine.
//
// Reproduces the published fee formula (hyperliquid docs → Trading → Fees,
// "Fee formula for developers") and the per-market calibration documented in
// docs/FEE_CALIBRATION.md. The committed results/hip3_fee_schedule.csv is the
// regression oracle: tests/web/fees.test.mjs re-derives every row from this file.
//
// All rates are expressed in basis points of notional.

// VIP tiers by 14-day weighted volume (perps). Base = no staking discount.
export const TIERS = [
  { tier: 0, minVolume: 0,       takerBps: 4.5, makerBps: 1.5, label: 'Tier 0 · ≤ $5M' },
  { tier: 1, minVolume: 5e6,     takerBps: 4.0, makerBps: 1.2, label: 'Tier 1 · > $5M' },
  { tier: 2, minVolume: 25e6,    takerBps: 3.5, makerBps: 0.8, label: 'Tier 2 · > $25M' },
  { tier: 3, minVolume: 100e6,   takerBps: 3.0, makerBps: 0.4, label: 'Tier 3 · > $100M' },
  { tier: 4, minVolume: 500e6,   takerBps: 2.8, makerBps: 0.0, label: 'Tier 4 · > $500M' },
  { tier: 5, minVolume: 2e9,     takerBps: 2.6, makerBps: 0.0, label: 'Tier 5 · > $2B' },
  { tier: 6, minVolume: 7e9,     takerBps: 2.4, makerBps: 0.0, label: 'Tier 6 · > $7B' },
];

// HYPE staking discount tiers (multiply both maker and taker rates by 1 − discount).
export const STAKING = [
  { id: 'none',     discount: 0.00, label: 'No staking' },
  { id: 'wood',     discount: 0.05, label: 'Wood · > 10 HYPE · 5 %' },
  { id: 'bronze',   discount: 0.10, label: 'Bronze · > 100 HYPE · 10 %' },
  { id: 'silver',   discount: 0.15, label: 'Silver · > 1,000 HYPE · 15 %' },
  { id: 'gold',     discount: 0.20, label: 'Gold · > 10,000 HYPE · 20 %' },
  { id: 'platinum', discount: 0.30, label: 'Platinum · > 100,000 HYPE · 30 %' },
  { id: 'diamond',  discount: 0.40, label: 'Diamond · > 500,000 HYPE · 40 %' },
];

// Maker rebate tiers by 14-day maker-volume share. A rebate replaces the maker rate.
export const MAKER_REBATE_TIERS = [
  { tier: 0, makerBps: null, label: 'No maker rebate' },
  { tier: 1, makerBps: -0.1, label: 'Rebate 1 · > 0.5 % share · −0.1 bps' },
  { tier: 2, makerBps: -0.2, label: 'Rebate 2 · > 1.5 % share · −0.2 bps' },
  { tier: 3, makerBps: -0.3, label: 'Rebate 3 · > 3.0 % share · −0.3 bps' },
];

// Referred users receive a 4 % discount on their first $25M of volume.
export const REFERRAL_DISCOUNT = 0.04;

export const GROWTH_MODE_SCALE = 0.1;

/**
 * HIP-3 scaling factors derived from a deployer's fee scale δ.
 *   δ < 1 : scaleIfHip3 = δ + 1, deployerShare = δ / (1 + δ)
 *   δ ≥ 1 : scaleIfHip3 = 2δ,    deployerShare = 0.5
 */
export function hip3Scales(deployerFeeScale) {
  const d = Number(deployerFeeScale);
  if (!Number.isFinite(d)) return { scaleIfHip3: 1, deployerShare: 0 };
  return d < 1
    ? { scaleIfHip3: d + 1, deployerShare: d / (1 + d) }
    : { scaleIfHip3: d * 2, deployerShare: 0.5 };
}

export function stakingById(id) {
  return STAKING.find((s) => s.id === id) ?? STAKING[0];
}

/**
 * Effective maker/taker fee for one market under one trader profile.
 *
 * @param {object} market  { isHip3, deployerFeeScale, growthMode, aligned }
 * @param {object} profile { tier, staking, referral, rebateTier, builderBps }
 * @returns {{ takerBps:number, makerBps:number, steps:object }}
 */
export function feeRates(market, profile) {
  const tier = TIERS[Math.min(Math.max(profile.tier | 0, 0), TIERS.length - 1)];
  const staking = stakingById(profile.staking);
  const rebate = MAKER_REBATE_TIERS[Math.min(Math.max(profile.rebateTier | 0, 0), 3)];
  const referral = profile.referral ? REFERRAL_DISCOUNT : 0;
  const builder = Number(profile.builderBps) || 0;

  const baseTaker = tier.takerBps * (1 - staking.discount);
  const baseMaker = rebate.makerBps != null ? rebate.makerBps : tier.makerBps * (1 - staking.discount);

  const isHip3 = Boolean(market.isHip3);
  const { scaleIfHip3, deployerShare } = isHip3 ? hip3Scales(market.deployerFeeScale) : { scaleIfHip3: 1, deployerShare: 0 };
  const growthModeScale = isHip3 && market.growthMode ? GROWTH_MODE_SCALE : 1;
  const aligned = isHip3 && Boolean(market.aligned);
  const alignedTakerScale = aligned ? (1 - deployerShare) * 0.8 + deployerShare : 1;
  const alignedRebateScale = aligned ? (1 - deployerShare) * 1.5 + deployerShare : 1;

  // Maker: positive fees scale like taker fees; rebates only get the aligned-quote boost.
  let maker = baseMaker * growthModeScale;
  if (maker > 0) maker *= scaleIfHip3 * (1 - referral);
  else maker *= alignedRebateScale;

  let taker = baseTaker * growthModeScale * scaleIfHip3 * (1 - referral);
  taker *= alignedTakerScale;

  return {
    takerBps: taker + builder,
    makerBps: maker + builder,
    steps: {
      tier, staking, rebate, referral, builder,
      baseTaker, baseMaker, isHip3, scaleIfHip3, deployerShare,
      growthModeScale, aligned, alignedTakerScale, alignedRebateScale,
      takerBeforeBuilder: taker, makerBeforeBuilder: maker,
    },
  };
}
