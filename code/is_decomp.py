"""
Modul 5 — Implementation Shortfall Decomposer (v2, Tier 1+2 = 23 markets)
==========================================================================

Per-coin Taker fee structures verified against docs.hydromancer.xyz and
Hyperliquid HIP-3 deployer documentation:

  Hyperliquid native (BTC, ETH, HYPE):              3.5 bps Tier 0
  Trade.xyz (deployerFeeScale=1, scaleIfHip3=2):    7.0 bps Tier 0
  Ventuals (Standard mode, no Growth Mode):         8.1 bps Tier 0

Builder-fee sensitivity sweep: {0, 1, 2, 5, 10} bps applied on top.

Decomposition:
    Total IS = Spread + Impact + Fee + Opportunity
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("is_decomp")

# ---------------------------------------------------------------------------
# Per-(dex, coin) Taker fees (bps) — loaded from on-chain-verified ground truth
# ---------------------------------------------------------------------------

import pandas as pd
import os

# Native HL Tier 2 baseline
FEE_HL_NATIVE = 3.5

# Look for the fee schedule CSV in a few candidate locations
_FEE_CSV_CANDIDATES = [
    Path(__file__).parent / "hip3_fee_schedule.csv",
    Path(r"C:\Users\grego\Downloads\paper\hl-hip3-tca\hip3_fee_schedule.csv"),
    Path(r"C:\Users\grego\Downloads\fetcher\hip3_fee_schedule.csv"),
]
_FEE_CSV = None
for _cand in _FEE_CSV_CANDIDATES:
    if _cand.exists():
        _FEE_CSV = _cand
        break

if _FEE_CSV is None:
    raise FileNotFoundError(
        f"hip3_fee_schedule.csv not found in any of: {_FEE_CSV_CANDIDATES}. "
        "Copy it from the paper directory to the fetcher directory."
    )

log.info("Loading per-(dex,coin) fee schedule from: %s", _FEE_CSV)

# Load and build composite-key dict
_FEE_DF = pd.read_csv(_FEE_CSV)

# Detect which column has the Tier 2 with GM rate
# (column names may vary slightly between versions of the schedule)
_FEE_COL_CANDIDATES = [
    "taker_tier2_growth_aligned",  # Tier 2 with GM + AQA discount (USDH-aligned)
    "taker_tier2_base_bps",         # Tier 2 base (fallback)
]
_fee_col = None
for c in _FEE_COL_CANDIDATES:
    if c in _FEE_DF.columns:
        _fee_col = c
        break

if _fee_col is None:
    log.warning("No Tier 2 GM fee column found. Available: %s",
                _FEE_DF.columns.tolist())
    log.warning("Falling back to first numeric fee-like column")
    for c in _FEE_DF.columns:
        if "taker" in c.lower() and "bps" in c.lower():
            _fee_col = c
            break

log.info("Using fee column: %s", _fee_col)

# Build composite-key dictionary: "xyz__TSLA" → fee_bps
COIN_TAKER_FEE_BPS: dict[str, float] = {}
for _, row in _FEE_DF.iterrows():
    key = f"{row['deployer_handle']}__{row['coin']}"
    COIN_TAKER_FEE_BPS[key] = float(row[_fee_col])

# Native HL markets (not in HIP-3 schedule, hardcoded at Tier 2 rate)
NATIVE_HL_COINS = ["BTC", "ETH", "HYPE", "SOL", "XRP", "DOGE", "BNB", "LINK", "XMR"]
for c in NATIVE_HL_COINS:
    COIN_TAKER_FEE_BPS[f"hyperliquid__{c}"] = FEE_HL_NATIVE

# Legacy fallback for unprefixed coins (in case some old data is still around)
for c in NATIVE_HL_COINS:
    COIN_TAKER_FEE_BPS[c] = FEE_HL_NATIVE

log.info("Loaded %d (dex, coin) fee entries", len(COIN_TAKER_FEE_BPS))

DEFAULT_TAKER_FEE_BPS = FEE_HL_NATIVE

BUILDER_FEE_SCENARIOS_BPS = [0, 1, 2, 5, 10]
POST_WINDOW_MIN = 30


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def _build_schema() -> pa.Schema:
    fields = [
        ("snapshot_id",          pa.string()),
        ("coin",                 pa.string()),
        ("arrival_ts_ms",        pa.int64()),
        ("order_size_usd",       pa.float64()),
        ("side",                 pa.string()),
        ("strategy",             pa.string()),
        ("taker_fee_bps_used",   pa.float64()),
        ("spread_bps",           pa.float64()),
        ("impact_bps",           pa.float64()),
        ("fee_base_bps",         pa.float64()),
    ]
    for bf in BUILDER_FEE_SCENARIOS_BPS:
        fields.append((f"fee_builder_{bf}bp", pa.float64()))
    fields += [
        ("opportunity_bps",       pa.float64()),
        ("total_is_baseline_bps", pa.float64()),
        ("total_is_with_5bp_bps", pa.float64()),
        ("arrival_mid",           pa.float64()),
        ("execution_vwap",        pa.float64()),
        ("post_window_mid",       pa.float64()),
        ("fill_completion_pct",   pa.float64()),
        ("n_levels_consumed",     pa.int32()),
    ]
    return pa.schema(fields)


_IS_SCHEMA = _build_schema()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def load_top_of_book(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    return con.sql("""
        WITH ranked AS (
            SELECT snapshot_id, side, px
            FROM book WHERE level = 0
        )
        SELECT snapshot_id,
               MAX(CASE WHEN UPPER(side) LIKE 'A%' THEN px END) AS top_ask,
               MAX(CASE WHEN UPPER(side) LIKE 'B%' THEN px END) AS top_bid
        FROM ranked GROUP BY snapshot_id
    """).df()


def build_post_window_lookup(con: duckdb.DuckDBPyConnection) -> dict:
    df = con.sql("""
        SELECT coin, target_ts_ms AS ts, mid
        FROM meta ORDER BY coin, ts
    """).df()
    by_coin = {}
    for coin, grp in df.groupby("coin"):
        by_coin[coin] = (grp["ts"].values, grp["mid"].values)
    return by_coin


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--data-dir",
                   default=r"C:\Users\grego\Downloads\fetcher\data")
    args = p.parse_args()

    data_root = Path(args.data_dir)
    parquet_root = data_root / "parquet"
    sim_path = data_root / "simulator" / "runs.parquet"
    out_dir = data_root / "is_results"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "is_decomp.parquet"

    if not sim_path.exists():
        log.error("Simulator output not found: %s", sim_path)
        sys.exit(2)

    con = duckdb.connect()
    book_glob = str(parquet_root / "book_snapshots" / "coin=*" / "snapshots.parquet")
    meta_glob = str(parquet_root / "snapshot_meta" / "coin=*" / "meta.parquet")
    con.execute(f"CREATE VIEW book AS SELECT * FROM read_parquet('{book_glob}', "
                f"hive_partitioning=true)")
    con.execute(f"CREATE VIEW meta AS SELECT * FROM read_parquet('{meta_glob}', "
                f"hive_partitioning=true)")

    log.info("=" * 60)
    log.info("IS Decomposer — Modul 5 (v2, Tier 1+2)")
    log.info("=" * 60)

    log.info("Loading simulator runs ...")
    t0 = time.perf_counter()
    sim_df = pq.read_table(sim_path).to_pandas()
    log.info("  %s runs (%.2f MB) loaded in %.1fs",
             f"{len(sim_df):,}", sim_path.stat().st_size / 1e6,
             time.perf_counter() - t0)

    log.info("Per-coin Taker fees in use:")
    unique_coins = sorted(sim_df["coin"].unique())
    for c in unique_coins:
        fee = COIN_TAKER_FEE_BPS.get(c, DEFAULT_TAKER_FEE_BPS)
        log.info("  %-15s  %.2f bps", c, fee)

    log.info("Loading top-of-book per snapshot ...")
    top_df = load_top_of_book(con)
    log.info("  %s snapshots", f"{len(top_df):,}")

    log.info("Building post-window mid lookup ...")
    post_by_coin = build_post_window_lookup(con)
    log.info("  %d coins", len(post_by_coin))

    log.info("Joining sim runs with top-of-book ...")
    merged = sim_df.merge(top_df, on="snapshot_id", how="left")

    log.info("Looking up post-window mids ...")
    post_mids = np.full(len(merged), np.nan, dtype=float)
    post_ts = merged["arrival_ts_ms"].values + POST_WINDOW_MIN * 60_000
    for coin, idx in merged.groupby("coin").groups.items():
        ts_arr_c, mid_arr_c = post_by_coin.get(coin, (None, None))
        if ts_arr_c is None:
            continue
        idx_arr = np.asarray(list(idx))
        post_ts_local = post_ts[idx_arr]
        positions = np.searchsorted(ts_arr_c, post_ts_local, side="left")
        positions = np.clip(positions, 0, len(ts_arr_c) - 1)
        post_mids[idx_arr] = mid_arr_c[positions]
    merged["post_window_mid"] = post_mids

    log.info("Computing IS components for %s runs ...", f"{len(merged):,}")
    t_comp = time.perf_counter()

    arr_mid = merged["arrival_mid"].values
    arr_ask = merged["top_ask"].values
    arr_bid = merged["top_bid"].values
    exec_vwap = merged["execution_vwap"].values
    post_mid = merged["post_window_mid"].values
    size_usd = merged["order_size_usd"].values
    filled_usd = merged["filled_usd"].values
    unfilled_usd = merged["unfilled_usd"].values
    side_arr = merged["side"].values
    coin_arr = merged["coin"].values

    # Per-row taker fee from the coin
    taker_fee_bps_used = np.array(
        [COIN_TAKER_FEE_BPS.get(c, DEFAULT_TAKER_FEE_BPS) for c in coin_arr]
    )

    fill_frac = np.where(size_usd > 0, filled_usd / size_usd, 0.0)
    unfill_frac = np.where(size_usd > 0, unfilled_usd / size_usd, 0.0)
    is_buy = side_arr == "BUY"

    spread_bps = np.where(
        arr_mid > 0,
        np.where(is_buy,
                 10_000 * (arr_ask - arr_mid) / arr_mid,
                 10_000 * (arr_mid - arr_bid) / arr_mid),
        np.nan,
    ) * fill_frac

    has_fill = (filled_usd > 0) & (exec_vwap > 0)
    impact_bps = np.zeros_like(arr_mid)
    sel = has_fill & is_buy
    impact_bps[sel] = (
        10_000 * (exec_vwap[sel] - arr_ask[sel])
        / arr_mid[sel] * fill_frac[sel]
    )
    sel = has_fill & ~is_buy
    impact_bps[sel] = (
        10_000 * (arr_bid[sel] - exec_vwap[sel])
        / arr_mid[sel] * fill_frac[sel]
    )

    opp_bps = np.zeros_like(arr_mid)
    has_unfill = (unfilled_usd > 0) & (post_mid > 0) & (arr_mid > 0)
    adverse_buy = np.maximum(0.0, post_mid - arr_mid) / arr_mid * 10_000
    adverse_sell = np.maximum(0.0, arr_mid - post_mid) / arr_mid * 10_000
    sel = has_unfill & is_buy
    opp_bps[sel] = adverse_buy[sel] * unfill_frac[sel]
    sel = has_unfill & ~is_buy
    opp_bps[sel] = adverse_sell[sel] * unfill_frac[sel]

    fee_base_bps = taker_fee_bps_used * fill_frac
    fee_bf = {
        f"fee_builder_{bf}bp": fee_base_bps + bf * fill_frac
        for bf in BUILDER_FEE_SCENARIOS_BPS
    }

    total_baseline = spread_bps + impact_bps + fee_base_bps + opp_bps
    total_with_5bp = spread_bps + impact_bps + fee_bf["fee_builder_5bp"] + opp_bps

    log.info("  computed in %.1fs", time.perf_counter() - t_comp)

    out = pd.DataFrame({
        "snapshot_id":          merged["snapshot_id"],
        "coin":                 merged["coin"],
        "arrival_ts_ms":        merged["arrival_ts_ms"],
        "order_size_usd":       merged["order_size_usd"],
        "side":                 merged["side"],
        "strategy":             merged["strategy"],
        "taker_fee_bps_used":   taker_fee_bps_used,
        "spread_bps":           spread_bps,
        "impact_bps":           impact_bps,
        "fee_base_bps":         fee_base_bps,
    })
    for bf in BUILDER_FEE_SCENARIOS_BPS:
        out[f"fee_builder_{bf}bp"] = fee_bf[f"fee_builder_{bf}bp"]
    out["opportunity_bps"]       = opp_bps
    out["total_is_baseline_bps"] = total_baseline
    out["total_is_with_5bp_bps"] = total_with_5bp
    out["arrival_mid"]           = arr_mid
    out["execution_vwap"]        = exec_vwap
    out["post_window_mid"]       = post_mid
    out["fill_completion_pct"]   = merged["fill_completion_pct"]
    out["n_levels_consumed"]     = merged["n_levels_consumed"].astype("int32")
    out = out[[f.name for f in _IS_SCHEMA]]

    log.info("Writing IS results ...")
    tbl = pa.Table.from_pandas(out, schema=_IS_SCHEMA, preserve_index=False)
    pq.write_table(tbl, out_path, compression="zstd")
    log.info("=" * 60)
    log.info("DONE: %s rows", f"{len(out):,}")
    log.info("Output: %s (%.2f MB)",
             out_path, out_path.stat().st_size / 1_048_576)

    log.info("\nQuick sanity: median total IS by coin × strategy (baseline fees)")
    summary = out.groupby(["coin", "strategy"])["total_is_baseline_bps"].median()
    for k, v in summary.items():
        log.info(f"  {k[0]:14s} | {k[1]:14s} | median total IS: {v:7.2f} bps")


if __name__ == "__main__":
    main()
