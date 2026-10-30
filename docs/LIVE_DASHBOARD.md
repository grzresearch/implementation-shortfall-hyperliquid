# Live Dashboard: Execution Cost Monitor

**URL:** https://grzresearch.github.io/implementation-shortfall-hyperliquid/

The dashboard applies the paper's cost engine to the *live* order book of every
Hyperliquid perpetual market, native and HIP-3, and shows for each market the
order-book depth, the effective fees and the all-in cost of every order type
Hyperliquid offers. A CLOB visualisation illustrates the depth of the selected
market. Everything runs in the browser against the public API; nothing is
stored or proxied.

## What is computed, and how it maps to the pipeline

| Live dashboard | Pipeline module | Notes |
|---|---|---|
| Book walk (`site/js/book.js` → `walkBook`, `stratCap5`, `stratTwapStatic`) | `code/simulator.py` | Line-by-line port; `tests/web/book.test.mjs` runs both on random books and requires identical fills, VWAP and residuals |
| Spread / impact / fee decomposition (`decompose`) | `code/is_decomp.py` | Spread = (touch − mid)/mid, impact = (VWAP − touch)/mid, fee = effective taker rate |
| Fee engine (`site/js/fees.js`) | `docs/FEE_CALIBRATION.md`, `results/hip3_fee_schedule.csv` | `tests/web/fees.test.mjs` re-derives all 178 rows of the committed schedule; growth mode, deployer fee scale and collateral token are read live per market |
| Order-size grids and asset classes (`site/js/universe.js`) | `simulator.SIZE_GRID` | Used for the cost-vs-size curve and the paper benchmark panel |
| Paper benchmark | `results/summary_stats.csv` | Median total IS by strategy × size × side, copied into the site at deploy time |

### Cost definition

The live figure for a taker order is **bps of filled notional relative to the
arrival mid**:

```
cost = spread + impact + fee
spread = 1e4 · (touch − mid) / mid           (buy: touch = best ask)
impact = 1e4 · (VWAP − touch) / mid
fee    = effective taker rate (bps)
```

When the visible book covers the whole order this equals the paper's
`total_is_baseline_bps` without the opportunity term (multiply each component
by the fill fraction to recover the paper's exact definition). Three markers
qualify a figure:

- `≈` — the walk reached into aggregated buckets (see below): exact inside the
  full-precision levels, an upper bound beyond them;
- `≥` — the order is larger than the whole visible book and only the
  full-precision levels were used: the cost of the fillable part, a lower bound
  for the whole order, shown with the fillable share;
- `≈` with a fillable share — the order exceeds the visible book *and* the
  fillable part runs through aggregated buckets, so the figure is an estimate
  rather than a strict bound.

**Opportunity cost** requires the post-trade price path and is therefore only
available in the paper benchmark.

Maker orders are priced *conditional on being filled*: they earn the
half-spread versus the arrival mid and pay the maker fee (or receive the
rebate). Adverse selection and non-fill risk are not modelled; the queue ahead
at the touch is shown instead.

### Order types

| Hyperliquid order type | Model |
|---|---|
| Market | Sweep of the visible book (= paper `NAIVE_MARKET`) |
| Limit IOC / crossing GTC | Walk only the levels inside touch ± 10 bps; remainder cancelled (GTC: rests as maker) |
| Limit GTC at touch, Post-only (ALO), Chase | Maker at the touch |
| Scale | Maker; average improvement of half the ladder range if every rung fills |
| Stop Market, Take Profit Market, Trailing Stop | Market order at trigger time (mark→book gap excluded) |
| Stop Limit, Take Profit Limit | Shown both resting (maker) and crossing (taker) |
| Native TWAP (5 min, 30 min, 1 h) | One sub-order every 30 s, each walking the current book (static-book assumption, as in the paper) |
| Paper strategies | `NAIVE_MARKET`, `MARKET_CAP5`, `TWAP_5MIN`, `TWAP_30MIN`; `VWAP_30MIN` needs the realised volume profile |

## Depth beyond 20 levels

`l2Book` returns at most 20 levels per side. On tight-tick markets those span
only a few basis points (BTC ≈ 2.5 bps, HYPE ≈ 0.2 bps), which would understate
depth and make large orders look unfillable. Whenever the 20 levels reach less
than 100 bps from the mid, the dashboard therefore also fetches an
**aggregated** book for the market. The aggregation is the finest `nSigFigs`
whose 20 buckets span more than `COARSE_MIN_SPAN_BPS` (20 bps); four
significant figures always span 20–200 bps, so every market uses `nSigFigs = 4`
with buckets of 1–10 bps. The aggregated book is *stitched* onto the
full-precision one: the fine levels are kept as they are, and every aggregated
bucket beyond them is appended net of the fine quantity it already contains.
Hyperliquid rounds bid buckets down and ask buckets up, so costs computed from
the stitched book are exact inside the fine range and an upper bound beyond it;
such figures carry the `≈` marker everywhere (table, order-type panel, curve).
The precision selector on the depth chart switches between the combined view,
the 20 full-precision levels and purely aggregated views (5 to 2 significant
figures).

## Data flow and API budget

- `perpDexs`, `spotMeta`, `metaAndAssetCtxs` per dex at start (universe, fee
  parameters, collateral token, 24h volume, open interest, funding); contexts
  refresh every two minutes.
- One WebSocket connection: `allMids` per dex, `bbo` for every live market (the
  exact top of book per block, folded into the stored books), and `l2Book` +
  `activeAssetCtx` for the selected market. The exchange sends `l2Book`
  snapshots only every few seconds, so the selected market's full book is also
  polled over REST every 3 s. A connection silent for 90 s is closed and
  reopened; the reconnect backoff only resets after a connection has stayed up
  for 10 s.
- A REST sweep refreshes the stalest book first (visible rows first, every
  fourth tick the hidden ones), one or two requests per market (full precision,
  then the aggregated book when the 20 levels span less than 100 bps). A sliding
  weight meter caps the page at 800 of the 1,200 weight-per-minute limit, the
  sweep keeps 120 of that free for the selected market, and HTTP 429 pauses
  everything for 15 s. The tab slows the sweep when hidden.
- Several tabs of the same browser share the per-IP limit, so one tab per
  browser profile is elected leader with the Web Locks API; it runs the full
  sweep and the `bbo` feed, while the others sweep at one request per second,
  skip `bbo` and take over when the leader closes.
- The trader profile (VIP tier, HYPE staking, referral, maker rebate tier,
  builder fee) is kept in `localStorage`; the selected market is in the URL
  hash (`#market=xyz:TSLA`).

## Deployment

`.github/workflows/pages.yml` runs the parity tests, copies `site/` plus
`results/summary_stats.csv` and `results/hip3_fee_schedule.csv` into the Pages
artifact and deploys it. One-time setup: *Settings → Pages → Source: GitHub
Actions*. Pushes to `main` that touch `site/`, the two result files or the tests
redeploy automatically; the workflow can also be started by hand.

Local preview: `python3 -m http.server 8000` from the repository root and open
`http://localhost:8000/site/` (the page falls back to `../results/` for the
benchmark data).

## Limitations

- Live costs exclude opportunity cost and latency; TWAP rows assume the book is
  not replenished between sub-orders (as in the paper).
- Depth is bounded by what the API exposes: 20 full-precision levels plus 20
  aggregated buckets per side.
- Deployers that have delisted all markets (as of October 2026: Felix,
  Ventuals, HyENA, Kinetiq, dreamcash, ABCDEx) appear with their paper
  benchmark only, behind the "show delisted" toggle.
- The page is an analysis tool, not investment advice.
