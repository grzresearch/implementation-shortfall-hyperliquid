# Implementation Shortfall — Hyperliquid HIP-3 Multi-Asset TCA

**Empirical transaction-cost analysis of 101 perpetual futures markets across the Hyperliquid ecosystem, December 15, 2025 — May 12, 2026.**

[![Status](https://img.shields.io/badge/status-working_paper-yellow)]()
[![Python](https://img.shields.io/badge/python-3.13-blue)]()
[![License](https://img.shields.io/badge/license-MIT-green)]()

---

## At a Glance

This repository contains the **code and aggregated results** of a working paper analyzing transaction costs (Implementation Shortfall, decomposed into spread, market impact, fees, and opportunity cost) across the Hyperliquid HIP-3 ecosystem. The paper itself is maintained separately.

| Metric | Value |
|---|---|
| Markets analyzed | **101** |
| Deployers covered | **7** (6 HIP-3 + native Hyperliquid) |
| Sample period | 149 days (Dec 15, 2025 — May 12, 2026) |
| Order book snapshots | ~1.1 million |
| Trades observed | ~1.5 billion |
| IS simulation runs | **55,069,575** |
| Strategies tested | 5 (NAIVE_MARKET, MARKET_CAP5, TWAP_5MIN, TWAP_30MIN, VWAP_30MIN) |
| Order size grid | 5 sizes × 2 sides per market (calibrated per liquidity tier) |

---

## Repository Layout

```
.
├── code/                            Python pipeline source
│   ├── ob_fetcher/                  Core library
│   │   ├── config.py                Configuration dataclasses
│   │   ├── extract.py               Parquet → records extractor
│   │   ├── pipeline.py              Memory-bounded orchestrator
│   │   ├── reservoir.py             S3 client (hydromancer-reservoir)
│   │   └── store.py                 Output schemas + writers
│   ├── discover_hip3.py             HIP-3 universe discovery via API
│   ├── fetch_hip3.py                Phase 2: HIP-3 markets fetch (92 markets)
│   ├── fetch_native_hl.py           Phase 1+: native Hyperliquid (9 markets)
│   ├── simulator.py                 Modul 4 — order book walker
│   ├── is_decomp.py                 Modul 5 — IS decomposition
│   ├── aggregator.py                Modul 6 — paper tables/figures
│   ├── aggregator_by_class.py       Modul 6.5 — by-asset-class summaries
│   ├── run_is_pipeline.py           End-to-end orchestrator
│   ├── diagnose_parquet.py          Data integrity verification
│   ├── data_quality_check.py        QC across all assets
│   └── run_fetch.py                 Phase 1 entry point
├── results/                         Aggregated outputs (committed)
│   ├── summary_stats.csv            5,050 rows × 14 cols (master output)
│   ├── hip3_fee_schedule.csv        196 (deployer, coin) effective taker fees
│   ├── tables/                      4 LaTeX tables ready for paper inclusion
│   │   ├── main_results.tex
│   │   ├── decomposition.tex
│   │   ├── strategy_winners.tex
│   │   └── fee_sensitivity.tex
│   └── plots/                       8 PNG figures (boxplots, decompositions, etc.)
├── docs/                            Methodology deep-dives
│   ├── ARCHITECTURE.md              Pipeline architecture & design decisions
│   ├── DATA_INTEGRITY.md            Cross-deployer namespace bug & fix
│   └── FEE_CALIBRATION.md           Hyperliquid fee formula & verification
├── requirements.txt                 Python dependencies
├── .gitignore                       Excludes data/, .venv/, secrets
├── LICENSE                          MIT
└── README.md                        This file
```

**Note:** The raw data (~15 GB Reservoir cache, ~3 GB intermediate Parquet files) is **not committed**. The pipeline is fully reproducible from the S3 source — see `docs/ARCHITECTURE.md` for details.

---

## Pipeline Architecture

The pipeline is a **6-module sequential workflow**:

```
                  ┌─────────────────────────────────────────┐
                  │  Hyperliquid (mainnet)                  │
                  │  - HIP-3 deployer registry              │
                  └─────────────────┬───────────────────────┘
                                    │ JSON-RPC
                                    ▼
       ┌────────────────────────────────────────────────────┐
       │  Modul 1: discover_hip3.py                         │
       │    Discovers all HIP-3 deployers + market metadata │
       │    Output: universe.csv (8 deployers, 178 markets) │
       └────────────────────┬───────────────────────────────┘
                            │
                            ▼
       ┌────────────────────────────────────────────────────┐
       │  Modul 2: fetch_hip3.py + fetch_native_hl.py       │
       │    S3 download from hydromancer-reservoir          │
       │    Memory-bounded streaming extract:               │
       │      orderbook (1-min cadence, subsample 15-min)   │
       │      fills (windowed [-5min, +30min] per snapshot) │
       │    Output: data/parquet/                           │
       │      book_snapshots/coin=dex__COIN/                │
       │      trade_window/coin=dex__COIN/                  │
       │      snapshot_meta/coin=dex__COIN/                 │
       └────────────────────┬───────────────────────────────┘
                            │
                            ▼
       ┌────────────────────────────────────────────────────┐
       │  Modul 3: build_fee_schedule.py [external]         │
       │    Calls Hyperliquid API for live fee parameters   │
       │    Applies: scaleIfHip3, growthMode, AQA discount  │
       │    Output: hip3_fee_schedule.csv (196 entries)     │
       └────────────────────┬───────────────────────────────┘
                            │
                            ▼
       ┌────────────────────────────────────────────────────┐
       │  Modul 4: simulator.py                             │
       │    For each (snapshot, strategy, size, side):      │
       │      Walk order book → produce fill record         │
       │      55M total runs                                │
       │    Output: data/simulator/runs.parquet (920 MB)    │
       └────────────────────┬───────────────────────────────┘
                            │
                            ▼
       ┌────────────────────────────────────────────────────┐
       │  Modul 5: is_decomp.py                             │
       │    Perold (1988) IS decomposition:                 │
       │      Total = Spread + Impact + Fee + Opportunity   │
       │    Per-(deployer, coin) on-chain fees from CSV     │
       │    Output: data/is_results/is_decomp.parquet (2GB) │
       └────────────────────┬───────────────────────────────┘
                            │
                            ▼
       ┌────────────────────────────────────────────────────┐
       │  Modul 6: aggregator.py + aggregator_by_class.py   │
       │    Per-coin × strategy × size statistics           │
       │    LaTeX tables + matplotlib figures               │
       │    Output: results/summary_stats.csv + tables/ +   │
       │            plots/                                  │
       └────────────────────────────────────────────────────┘
```

**Key design decisions** are documented in `docs/ARCHITECTURE.md`.

---

## Methodology Summary

### Implementation Shortfall Decomposition

Following Perold (1988), total IS is decomposed into four orthogonal components:

```
IS_total = Spread + Impact + Fee + Opportunity
```

Where:
- **Spread** = half-spread × fill fraction (cost of crossing the touch)
- **Impact** = (VWAP − arrival_mid) × fill fraction (cost of walking the book)
- **Fee** = effective taker rate × fill fraction (exchange fee with deployer-specific scaling)
- **Opportunity** = max(0, adverse post-window mid drift) × unfilled fraction

All components in basis points, signed consistently (positive = cost).

### Fee Calibration

Per-deployer effective taker fees are computed via Hyperliquid's published formula:

```
takerPct = takerRate × scaleIfHip3 × growthModeScale × (1 − referralDiscount)
```

where:
- `scaleIfHip3 = δ + 1` (or `2δ` if `δ ≥ 1`); δ = deployer fee scale
- `growthModeScale = 0.1` if growth mode enabled, else `1`
- For USDH-aligned markets, AQA discount applies: `aqaDiscount = (1 − deployerShare) × 0.8 + deployerShare`

Trader profile: **Tier 2 institutional** (volume ≥ $5M, ≤ $25M / 14-day rolling), no HYPE staking, no referral. This biases against HIP-3 (gives no fee advantage from lower tiers). Robustness checks sweep Tier 0–3 with/without staking.

Full details in `docs/FEE_CALIBRATION.md`.

### Strategies Tested

| Strategy | Description |
|---|---|
| `NAIVE_MARKET` | Single sweep through entire order book at arrival |
| `MARKET_CAP5` | Cap at 5% of each level's depth (depth-aware throttling) |
| `TWAP_5MIN` | 5 equal-size slices over 5 minutes |
| `TWAP_30MIN` | 30 equal-size slices over 30 minutes |
| `VWAP_30MIN` | 30 slices weighted by observed post-arrival volume profile |

### Order Sizes (Liquidity-Tier Calibrated)

| Tier | Markets | Size grid (USD) |
|---|---|---|
| `SIZE_CRYPTO_MAJOR` | BTC, ETH | 10k, 100k, 1M, 5M, 10M |
| `SIZE_CRYPTO_NATIVE` | HYPE | 10k, 50k, 100k, 250k, 500k |
| `SIZE_EQUITY_LARGE` | Most equities, indices, commodities | 1k, 10k, 50k, 100k, 250k |
| `SIZE_THEMATIC` | Thematic indices (MAG7, SEMIS, etc.) | 1k, 5k, 25k, 50k, 100k |
| `SIZE_PREIPO` | Ventuals pre-IPO (ANTHROPIC, SPACEX, OPENAI) | 1k, 5k, 10k, 25k, 50k |

---

## Data Universe

### HIP-3 Deployers (92 markets)

| Deployer | Handle | Markets in Sample | δ (Fee Scale) | Growth Mode % | Quote | USDH-Aligned? |
|---|---|---:|---:|---:|---|---|
| Trade.xyz | `xyz` | 14 | 1.0 | 96% | USDC | No |
| Markets by Kinetiq | `km` | 21 | 1.0 | 87% | USDC | No |
| dreamcash | `cash` | 14 | 1.0 | 82% | USDT0 | No |
| Felix Exchange | `flx` | 15 | 1.0 | 75% | USDH | **Yes** |
| Ventuals | `vntl` | 6 | 1.0 | 67% | USDH | **Yes** |
| HyENA | `hyna` | 22 | **0.1111** | 4% | USDe | No |

### Native Hyperliquid (9 markets)

BTC, ETH, HYPE, SOL, XRP, DOGE, BNB, LINK, XMR

### Cross-Listings (Identification Goldmine)

The dataset includes **6 four-way cross-listings** (TSLA, NVDA, GOLD, SILVER, OIL, SP500) on xyz/flx/km/cash, plus **9 native-vs-HyENA cross-listings** (BTC, ETH, HYPE, SOL, XRP, DOGE, BNB, LINK, XMR), plus **1 three-way** (XMR on native+hyna+flx). These enable clean fee-delta and venue-effect identification.

---

## Reproducing the Results

### Prerequisites

- Python 3.13+
- AWS account with billing enabled (for requester-pays Reservoir S3 bucket)
- ~20 GB free disk space
- ~6 hours of compute (1.5h fetch + 1h simulator + 15min IS decomp + 10min aggregator)

### Setup

```bash
git clone https://github.com/gelatotrade/implementation-shortfall-hyperliquid.git
cd implementation-shortfall-hyperliquid
python -m venv .venv
source .venv/bin/activate          # Linux/macOS
# OR
.\.venv\Scripts\Activate.ps1       # Windows PowerShell
pip install -r requirements.txt
```

### Configure AWS

```bash
aws configure
# Enter access key, secret, region (recommend ap-northeast-1)
```

### Full Pipeline Run

```bash
# 1. Discover HIP-3 universe (5 min)
python code/discover_hip3.py

# 2. Fetch HIP-3 markets (2-3 hours, depending on bandwidth)
python code/fetch_hip3.py

# 3. Fetch native Hyperliquid (30-60 min)
python code/fetch_native_hl.py

# 4. Diagnose data integrity
python code/diagnose_parquet.py

# 5. Run IS pipeline (Modul 4-6, ~90 min)
python code/run_is_pipeline.py
```

Final output in `data/aggregator/`:
- `summary_stats.csv` (5,050 rows)
- `tables/*.tex`
- `plots/*.png`

### Quick Inspection (no recompute)

The `results/` directory contains the **final outputs** from the full run. No setup needed to inspect them.

```bash
# Master summary
head results/summary_stats.csv

# View any figure
open results/plots/01_is_boxplot_by_size.png
```

---

## Key Implementation Details (For Code Reviewers)

### Memory-Bounded Streaming Extract

The pipeline handles 15 GB of compressed Parquet without ever loading it all into RAM. Key technique: **two-pass per asset**, where order book parquets (small, ~10–50 MB) are loaded fully, but fills (large, multi-GB per day across all assets) are streamed file-by-file and sliced into per-snapshot trade windows via `numpy.searchsorted`. Peak RAM scales with one day's fills, not the full study period.

See `code/ob_fetcher/pipeline.py::_process_asset`.

### Composite Key Namespace

Cross-listed underlyings (e.g., TSLA on xyz/flx/km/cash) require explicit deployer disambiguation. We use `dex__COIN` (e.g., `xyz__TSLA`) as the universal partition key. The `__` separator is filesystem-safe on Windows (which reserves `:` for drive letters).

A subtle bug was discovered mid-project where missing this disambiguation caused cross-deployer data to overwrite each other. The diagnostic and fix are documented in `docs/DATA_INTEGRITY.md`.

### Fee Schedule Loading

Per-(deployer, coin) fees are loaded from `results/hip3_fee_schedule.csv`, generated externally by calling Hyperliquid's `/info` API for current deployer configurations and applying the published fee formula. This avoids hardcoding fees that change as deployers update their growth-mode and scale parameters.

The IS decomposer (`is_decomp.py`) builds a runtime dict `{dex__COIN: bps}` from the CSV, with native HL fallbacks at Tier 2 baseline (3.5 bps).

### DuckDB for Aggregation

DuckDB is used for the heavy joins in `is_decomp.py` and `aggregator.py` because it (a) handles Parquet predicate pushdown natively, (b) parallelizes across cores, and (c) requires no server setup. The 55M-row IS decomposition completes in ~3 minutes on a laptop.

---

## License

MIT — see `LICENSE`. Free to use, modify, redistribute. Citation appreciated.

---

## Contact

- **Author:** Gregor Albiez ([@gelatotrade](https://github.com/gelatotrade))
- **Issues:** Use GitHub Issues for code questions or bug reports

For academic collaboration inquiries, contact via GitHub.
