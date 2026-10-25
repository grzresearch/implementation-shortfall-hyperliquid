"""
Modul 4 — Implementation Shortfall Simulator (Tier 1+2 = 23 markets)
======================================================================

For each (snapshot, order_size, strategy, side) tuple, simulates execution
through the order book and produces a fill record.

Per-coin SIZE_GRID is calibrated to each market's liquidity profile:
  - Crypto majors (BTC/ETH): up to $10M
  - Crypto native (HYPE):    up to $500k
  - Mega-cap Equities:       up to $250k (deep books)
  - Crypto-adjacent Equity:  up to $250k
  - Indices/ETFs:            up to $250k (XYZ100 deepest)
  - Commodities:             up to $250k (GOLD deepest)
  - Forex:                   up to $250k
  - Pre-IPO (Ventuals):      up to $50k (small markets)
  - Thematic Indices:        up to $100k

Strategies: NAIVE_MARKET, MARKET_CAP5, TWAP_5MIN, TWAP_30MIN, VWAP_30MIN
Both sides (BUY + SELL) simulated.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from dataclasses import dataclass
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
log = logging.getLogger("simulator")


# ---------------------------------------------------------------------------
# Per-coin SIZE_GRID (calibrated by liquidity tier)
# ---------------------------------------------------------------------------

SIZE_CRYPTO_MAJOR    = [10_000, 100_000, 1_000_000, 5_000_000, 10_000_000]
SIZE_CRYPTO_NATIVE   = [10_000, 50_000, 100_000, 250_000, 500_000]
SIZE_EQUITY_LARGE    = [1_000, 10_000, 50_000, 100_000, 250_000]
SIZE_INDEX_ETF       = [1_000, 10_000, 50_000, 100_000, 250_000]
SIZE_COMMODITY       = [1_000, 10_000, 50_000, 100_000, 250_000]
SIZE_FOREX           = [1_000, 10_000, 50_000, 100_000, 250_000]
SIZE_PREIPO          = [1_000, 5_000, 10_000, 25_000, 50_000]
SIZE_THEMATIC        = [1_000, 5_000, 25_000, 50_000, 100_000]

SIZE_GRID: dict[str, list[int]] = {
    # ─── Crypto perps (Hyperliquid native) ───
    "BTC":  SIZE_CRYPTO_MAJOR,
    "ETH":  SIZE_CRYPTO_MAJOR,
    "HYPE": SIZE_CRYPTO_NATIVE,

    # ─── Equities Mega-Cap (Trade.xyz) ───
    "NVDA": SIZE_EQUITY_LARGE,
    "TSLA": SIZE_EQUITY_LARGE,
    "AAPL": SIZE_EQUITY_LARGE,

    # ─── Crypto-adjacent Equities (Trade.xyz, Tier 2) ───
    "MSTR": SIZE_EQUITY_LARGE,
    "COIN": SIZE_EQUITY_LARGE,
    "HOOD": SIZE_EQUITY_LARGE,

    # ─── Indices / ETFs (Trade.xyz) ───
    "XYZ100": SIZE_INDEX_ETF,
    "SP500":  SIZE_INDEX_ETF,
    "EWY":    SIZE_INDEX_ETF,

    # ─── Commodities (Trade.xyz) ───
    "GOLD":   SIZE_COMMODITY,
    "SILVER": SIZE_COMMODITY,
    "CL":     SIZE_COMMODITY,

    # ─── Forex (Trade.xyz) ───
    "EUR": SIZE_FOREX,
    "JPY": SIZE_FOREX,

    # ─── Pre-IPO (Ventuals) ───
    "ANTHROPIC": SIZE_PREIPO,
    "SPACEX":    SIZE_PREIPO,
    "OPENAI":    SIZE_PREIPO,

    # ─── Thematic Indices (Ventuals, Tier 2) ───
    "MAG7":     SIZE_THEMATIC,
    "SEMIS":    SIZE_THEMATIC,
    "INFOTECH": SIZE_THEMATIC,
}

SIZE_GRID_DEFAULT = SIZE_EQUITY_LARGE  # fallback for any unlisted coin


STRATEGIES = [
    ("NAIVE_MARKET", None),
    ("MARKET_CAP5",  None),
    ("TWAP_5MIN",    {"n_slices": 5,  "duration_min": 5}),
    ("TWAP_30MIN",   {"n_slices": 30, "duration_min": 30}),
    ("VWAP_30MIN",   {"n_slices": 30, "duration_min": 30}),
]
SIDES = ["BUY", "SELL"]


# ---------------------------------------------------------------------------
# Output schema
# ---------------------------------------------------------------------------

_RUN_SCHEMA = pa.schema([
    ("snapshot_id",         pa.string()),
    ("coin",                pa.string()),
    ("arrival_ts_ms",       pa.int64()),
    ("arrival_mid",         pa.float64()),
    ("order_size_usd",      pa.float64()),
    ("side",                pa.string()),
    ("strategy",            pa.string()),
    ("filled_usd",          pa.float64()),
    ("filled_qty_base",     pa.float64()),
    ("unfilled_usd",        pa.float64()),
    ("fill_completion_pct", pa.float64()),
    ("execution_vwap",      pa.float64()),
    ("n_levels_consumed",   pa.int32()),
    ("n_slices",            pa.int32()),
])


@dataclass
class SimRun:
    snapshot_id: str
    coin: str
    arrival_ts_ms: int
    arrival_mid: float
    order_size_usd: float
    side: str
    strategy: str
    filled_usd: float
    filled_qty_base: float
    unfilled_usd: float
    fill_completion_pct: float
    execution_vwap: float
    n_levels_consumed: int
    n_slices: int


# ---------------------------------------------------------------------------
# Book walker
# ---------------------------------------------------------------------------


def walk_book(levels, target_usd):
    remaining = target_usd
    fills = []
    for i, (px, sz) in enumerate(levels):
        if remaining <= 0:
            return fills, 0.0, i
        level_notional = px * sz
        if level_notional >= remaining:
            qty = remaining / px
            fills.append((px, qty))
            return fills, 0.0, i + 1
        fills.append((px, sz))
        remaining -= level_notional
    return fills, remaining, len(levels)


def strat_naive(levels, size_usd):
    fills, remaining, n_lvl = walk_book(levels, size_usd)
    return fills, remaining, n_lvl, 1


def strat_cap5(levels, size_usd):
    capped = [(px, sz * 0.05) for px, sz in levels]
    fills, remaining, n_lvl = walk_book(capped, size_usd)
    return fills, remaining, n_lvl, 1


def strat_twap_static(levels, size_usd, n_slices):
    slice_size = size_usd / n_slices
    all_fills = []
    total_remaining = 0.0
    max_levels = 0
    for _ in range(n_slices):
        fills, remaining, n_lvl = walk_book(levels, slice_size)
        all_fills.extend(fills)
        total_remaining += remaining
        max_levels = max(max_levels, n_lvl)
    return all_fills, total_remaining, max_levels, n_slices


def strat_vwap_static(levels, size_usd, vol_profile, n_slices):
    if len(vol_profile) < n_slices:
        vol_profile = np.concatenate([
            vol_profile, np.zeros(n_slices - len(vol_profile))])
    weights = vol_profile[:n_slices]
    total_w = weights.sum()
    if total_w <= 0:
        return strat_twap_static(levels, size_usd, n_slices)
    slice_sizes = size_usd * (weights / total_w)
    all_fills = []
    total_remaining = 0.0
    max_levels = 0
    n_effective_slices = 0
    for s in slice_sizes:
        if s <= 0:
            continue
        n_effective_slices += 1
        fills, remaining, n_lvl = walk_book(levels, s)
        all_fills.extend(fills)
        total_remaining += remaining
        max_levels = max(max_levels, n_lvl)
    return all_fills, total_remaining, max_levels, n_effective_slices


# ---------------------------------------------------------------------------
# Aggregate fills into SimRun
# ---------------------------------------------------------------------------


def fills_to_run(snapshot_id, coin, arrival_ts_ms, arrival_mid,
                  size_usd, side, strategy_name,
                  fills, remaining_usd, n_levels, n_slices):
    filled_usd = sum(px * qty for px, qty in fills)
    filled_qty_base = sum(qty for _, qty in fills)
    vwap = filled_usd / filled_qty_base if filled_qty_base > 0 else 0.0
    return SimRun(
        snapshot_id=snapshot_id, coin=coin,
        arrival_ts_ms=arrival_ts_ms, arrival_mid=arrival_mid,
        order_size_usd=size_usd, side=side, strategy=strategy_name,
        filled_usd=filled_usd, filled_qty_base=filled_qty_base,
        unfilled_usd=remaining_usd,
        fill_completion_pct=100.0 * filled_usd / size_usd if size_usd > 0 else 0.0,
        execution_vwap=vwap,
        n_levels_consumed=n_levels,
        n_slices=n_slices,
    )


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def _load_books(con: duckdb.DuckDBPyConnection, coin: str) -> dict:
    df = con.sql(f"""
        SELECT snapshot_id, side, level, px, sz
        FROM book WHERE coin = '{coin}'
        ORDER BY snapshot_id, side, level
    """).df()
    books = {}
    for snap_id, grp in df.groupby("snapshot_id"):
        asks, bids = [], []
        for _, row in grp.iterrows():
            tup = (float(row["px"]), float(row["sz"]))
            if str(row["side"]).upper().startswith("A"):
                asks.append(tup)
            else:
                bids.append(tup)
        asks.sort(key=lambda t: t[0])
        bids.sort(key=lambda t: -t[0])
        books[snap_id] = {"asks": asks, "bids": bids}
    return books


def _load_vwap_profiles(con: duckdb.DuckDBPyConnection, coin: str,
                        n_slices_max: int = 30) -> dict:
    df = con.sql(f"""
        SELECT snapshot_id,
               CAST(ts_offset_ms / 60000 AS INTEGER) AS min_off,
               SUM(px * sz) AS vol_usd
        FROM trades
        WHERE coin = '{coin}'
          AND ts_offset_ms >= 0
          AND ts_offset_ms < {n_slices_max * 60_000}
        GROUP BY snapshot_id, min_off
    """).df()
    profiles = {}
    for snap_id, grp in df.groupby("snapshot_id"):
        arr = np.zeros(n_slices_max)
        for _, row in grp.iterrows():
            m = int(row["min_off"])
            if 0 <= m < n_slices_max:
                arr[m] = float(row["vol_usd"])
        profiles[snap_id] = arr
    return profiles


def _load_meta(con: duckdb.DuckDBPyConnection, coin: str) -> pd.DataFrame:
    return con.sql(f"""
        SELECT snapshot_id, coin, target_ts_ms, actual_ts_ms, mid, spread_bps
        FROM meta WHERE coin = '{coin}'
        ORDER BY target_ts_ms
    """).df()


def _records_to_table(records):
    cols = {f.name: [] for f in _RUN_SCHEMA}
    for r in records:
        d = r.__dict__
        for name in cols:
            cols[name].append(d[name])
    return pa.Table.from_pydict(cols, schema=_RUN_SCHEMA)


# ---------------------------------------------------------------------------
# Per-coin runner
# ---------------------------------------------------------------------------


def run_simulator_for_coin(con, coin: str, sizes: list, out_writer):
    log.info("=== coin=%s ===", coin)
    t0 = time.perf_counter()

    log.info("  loading books ...")
    books = _load_books(con, coin)
    if not books:
        log.warning("  no books found for %s — skipping", coin)
        return 0
    log.info("    %d snapshots", len(books))

    log.info("  loading VWAP volume profiles ...")
    vwap_profiles = _load_vwap_profiles(con, coin)
    log.info("    %d profiles", len(vwap_profiles))

    log.info("  loading meta ...")
    meta_df = _load_meta(con, coin)
    log.info("    %d meta rows", len(meta_df))

    log.info("  sizes for %s: %s", coin, sizes)
    log.info("  simulating %d snapshots × %d sizes × %d strategies × %d sides = %d runs",
             len(meta_df), len(sizes), len(STRATEGIES), len(SIDES),
             len(meta_df) * len(sizes) * len(STRATEGIES) * len(SIDES))

    batch = []
    batch_size = 100_000
    total_runs = 0
    t_sim = time.perf_counter()

    for _, m in meta_df.iterrows():
        snap_id = m["snapshot_id"]
        if snap_id not in books:
            continue
        book = books[snap_id]
        arrival_mid = float(m["mid"])
        arrival_ts_ms = int(m["target_ts_ms"])
        vol_profile = vwap_profiles.get(snap_id, np.zeros(30))

        for side in SIDES:
            levels = book["asks"] if side == "BUY" else book["bids"]
            if not levels:
                continue
            for size_usd in sizes:
                for strat_name, strat_params in STRATEGIES:
                    if strat_name == "NAIVE_MARKET":
                        fills, rem, nl, ns = strat_naive(levels, size_usd)
                    elif strat_name == "MARKET_CAP5":
                        fills, rem, nl, ns = strat_cap5(levels, size_usd)
                    elif strat_name == "TWAP_5MIN":
                        fills, rem, nl, ns = strat_twap_static(
                            levels, size_usd, strat_params["n_slices"])
                    elif strat_name == "TWAP_30MIN":
                        fills, rem, nl, ns = strat_twap_static(
                            levels, size_usd, strat_params["n_slices"])
                    elif strat_name == "VWAP_30MIN":
                        fills, rem, nl, ns = strat_vwap_static(
                            levels, size_usd, vol_profile,
                            strat_params["n_slices"])
                    else:
                        continue
                    batch.append(fills_to_run(
                        snap_id, coin, arrival_ts_ms, arrival_mid,
                        size_usd, side, strat_name, fills, rem, nl, ns,
                    ))
                    total_runs += 1

        if len(batch) >= batch_size:
            out_writer.write_table(_records_to_table(batch))
            batch.clear()
            log.info("    [%s] %s runs (%.0fs, %.0f runs/sec)",
                     coin, f"{total_runs:,}",
                     time.perf_counter() - t_sim,
                     total_runs / max(time.perf_counter() - t_sim, 1e-6))

    if batch:
        out_writer.write_table(_records_to_table(batch))
        batch.clear()

    log.info("  done %s: %d runs in %.1fs", coin, total_runs,
             time.perf_counter() - t0)
    return total_runs


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--data-dir",
                   default=str(Path(__file__).resolve().parents[1] / "data"))
    p.add_argument("--coins", nargs="*", default=None,
                   help="Only run these coins (default: all detected)")
    args = p.parse_args()

    data_root = Path(args.data_dir)
    parquet_root = data_root / "parquet"
    sim_dir = data_root / "simulator"
    sim_dir.mkdir(parents=True, exist_ok=True)
    out_path = sim_dir / "runs.parquet"

    coins = sorted([
        p.name.split("=", 1)[1]
        for p in (parquet_root / "book_snapshots").glob("coin=*")
        if p.is_dir()
    ])
    if args.coins:
        coins = [c for c in coins if c in args.coins]
    if not coins:
        log.error("No coin partitions found.")
        sys.exit(2)

    con = duckdb.connect()
    book_glob = str(parquet_root / "book_snapshots" / "coin=*" / "snapshots.parquet")
    trades_glob = str(parquet_root / "trade_window" / "coin=*" / "trades.parquet")
    meta_glob = str(parquet_root / "snapshot_meta" / "coin=*" / "meta.parquet")
    con.execute(f"CREATE VIEW book AS SELECT * FROM read_parquet('{book_glob}', "
                f"hive_partitioning=true)")
    con.execute(f"CREATE VIEW trades AS SELECT * FROM read_parquet('{trades_glob}', "
                f"hive_partitioning=true)")
    con.execute(f"CREATE VIEW meta AS SELECT * FROM read_parquet('{meta_glob}', "
                f"hive_partitioning=true)")

    log.info("=" * 60)
    log.info("IS Simulator — Modul 4 (v3, 101 markets, composite keys)")
    log.info("=" * 60)
    log.info("Coins detected: %d", len(coins))

    def _base_coin(c: str) -> str:
        """Parse base coin from composite key. 'xyz__TSLA' -> 'TSLA', 'BTC' -> 'BTC'."""
        return c.split("__", 1)[1] if "__" in c else c

    for c in coins:
        sz = SIZE_GRID.get(_base_coin(c), SIZE_GRID_DEFAULT)
        log.info("  %-25s sizes: %s", c, sz)

    writer = pq.ParquetWriter(out_path, _RUN_SCHEMA, compression="zstd")
    total = 0
    t0 = time.perf_counter()
    for coin in coins:
        sizes = SIZE_GRID.get(_base_coin(coin), SIZE_GRID_DEFAULT)
        n = run_simulator_for_coin(con, coin, sizes, writer)
        total += n
    writer.close()

    log.info("=" * 60)
    log.info("DONE: %d total simulator runs in %.1fs (%.1f min)",
             total, time.perf_counter() - t0, (time.perf_counter() - t0) / 60)
    log.info("Output: %s (%.2f MB)",
             out_path, out_path.stat().st_size / 1_048_576)


if __name__ == "__main__":
    main()
