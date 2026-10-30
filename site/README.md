# Live dashboard (`site/`)

A dependency-free static site (ES modules, no build step) that runs the paper's
implementation-shortfall engine on the live Hyperliquid order book, for every
native and HIP-3 perpetual market. Deployed to GitHub Pages by
`.github/workflows/pages.yml`; methodology in `../docs/LIVE_DASHBOARD.md`.

## Local preview

```bash
# from the repository root, so the page can read ../results/summary_stats.csv
python3 -m http.server 8000
# open http://localhost:8000/site/
```

The page talks to `api.hyperliquid.xyz` directly from the browser (the API
sends `Access-Control-Allow-Origin: *`). Nothing is proxied or stored server-side.

## Tests

```bash
node --test tests/web/*.test.mjs
```

They run in CI on every push and pull request that touches `site/`,
`tests/web/`, `code/simulator.py` or the fee schedule
(`.github/workflows/web-tests.yml`), and again before every deployment.

`fees.test.mjs` re-derives every row of `results/hip3_fee_schedule.csv` from
`js/fees.js`; `book.test.mjs` runs the JavaScript book walker against the Python
functions extracted from `code/simulator.py` (needs `python3`).

## Layout

| File | Role |
|---|---|
| `js/config.js` | Every tunable: API budget, sweep pacing, aggregation thresholds, size presets |
| `js/api.js` | REST client with a sliding weight meter; WebSocket client with reconnect and a subscription registry |
| `js/fees.js` | Hyperliquid fee formula: tiers, staking, referral, maker rebates, HIP-3 scaling, growth mode, aligned quote |
| `js/book.js` | `walk_book` port, strategies, spread/impact/fee decomposition, depth metrics, aggregated-book stitching |
| `js/ordertypes.js` | Maps every Hyperliquid order type onto the cost engine |
| `js/universe.js` | Loads deployers and markets with live fee parameters; asset classes and the paper's size grids |
| `js/table.js` | All-markets table (keyed rows, in-place updates, sorting) |
| `js/clob.js` | Depth chart (SVG) with hover, keyboard navigation and a ladder twin |
| `js/curve.js` | Cost-vs-size curve with the paper's medians overlaid |
| `js/detail.js` | Selected-market panel: KPIs, order-type costs, paper benchmark, fee derivation |
| `js/paper.js` | Parses `results/summary_stats.csv` |
| `js/state.js` | Store, trader profile and theme in `localStorage` (normalised on load), selected market in the URL hash |
| `js/format.js` | Number, bps, USD and size formatting; size-input parsing |
| `js/app.js` | Orchestration: universe load, leader election across tabs, sweep scheduler, streams, rendering |
