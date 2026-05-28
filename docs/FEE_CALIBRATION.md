# Fee Calibration: Hyperliquid Effective Taker Fees

## Source

All effective fees are computed from Hyperliquids published formula
(see hyperliquid.gitbook.io/hyperliquid-docs/trading/fees) using live
deployer configurations queried via the info API.

## Formula

    takerPct = takerRate * scaleIfHip3 * growthModeScale * (1 - referralDiscount)

Where:
- scaleIfHip3 = delta + 1     if delta less than 1
              = 2 * delta     if delta greater than or equal to 1
- growthModeScale = 0.1       if growth mode enabled
                  = 1.0       otherwise
- referralDiscount = 0        (no referral assumed for baseline)

## AQA Discount (USDH-Aligned Deployers)

For USDH-aligned markets (Felix, Ventuals), an additional Aligned Quote
Asset (AQA) discount applies:

    aqaDiscount = (1 - deployerShare) * 0.8 + deployerShare

With deployerShare = 0.05 for the Tier 2 trader profile, this gives a
roughly 24% fee reduction on USDH markets.

## Per-Deployer Effective Fees (Tier 2 institutional, GM active)

| Deployer | Fee (bps) | Notes |
|---|---|---|
| Trade.xyz | 0.70 | USDC, GM active, no AQA |
| Markets by Kinetiq | 0.70 | USDC, GM active |
| dreamcash | 0.70 | USDT0, GM active |
| Felix Exchange | 0.63 | USDH + GM + AQA discount |
| Ventuals | 0.63 | USDH + GM + AQA discount |
| HyENA | 3.89 | delta=0.1111, no GM (96% of markets) |
| Native Hyperliquid | 3.50 | Standard Tier 2 |

Note: Some markets within a deployer have GM disabled, leading to higher
fees (e.g., xyz__GOLD at 7.0 bps). See results/hip3_fee_schedule.csv for
per-market values.

## Trader Profile Assumption

Baseline: Tier 2 institutional (14-day volume between 5M and 25M USD), no
HYPE staking, no referral discount.

This is intentionally conservative - it biases against HIP-3 by giving
them no fee advantage from lower tiers. Robustness checks sweep:

- Tier 0 (retail), Tier 1, Tier 3
- With and without HYPE staking (5/10/15% discounts)
- With and without referral discount (4% additional)

Results are robust across all trader profiles.

## CSV Format

results/hip3_fee_schedule.csv contains 17 columns including:
- deployer, deployer_handle, coin, full_symbol
- deployer_fee_scale, growth_mode, quote_asset, is_aligned_quote
- scale_if_hip3, growth_mode_scale
- Tier-specific: taker_tier0_base_bps, taker_tier0_growth_bps,
  taker_tier0_growth_aligned, taker_tier2_base_bps,
  taker_tier2_growth_aligned
- Metadata: deployer_last_change, growth_mode_last_change

The IS decomposer (code/is_decomp.py) reads the taker_tier2_growth_aligned
column for baseline analysis.
