"""
Phase 1 Data Quality Validation
================================

Reads the parquet outputs from the Reservoir fetcher and runs a battery of
structural, statistical, and integrity checks.

Schema (from store.py):
  meta:    snapshot_id, coin, target_ts_ms, actual_ts_ms, skew_ms, mid,
           microprice, spread_bps, top_bid_sz, top_ask_sz, book_levels,
           n_trades_window
  book:    snapshot_id, target_ts_ms, actual_ts_ms, coin, side, level, px, sz, n
  trades:  snapshot_id, coin, ts_ms, ts_offset_ms, side, px, sz, tid,
           builder, builder_fee, fee

Outputs (data/validation/):
  - metrics.json          all numeric results
  - plots/*.png           5 visualization plots
  - summary.tex           LaTeX table for paper appendix

Usage:
  python data_quality_check.py
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("dq_check")

COLOR = {
    "PASS": "\033[92m", "WARN": "\033[93m", "FAIL": "\033[91m",
    "INFO": "\033[94m", "END":  "\033[0m",  "BOLD": "\033[1m",
}

# Canonical column names (per store.py _META_SCHEMA / _BOOK_SCHEMA / _TRADE_SCHEMA)
META_TS_COL = "target_ts_ms"
META_MID_COL = "mid"
META_SPREAD_COL = "spread_bps"
META_TRADES_COL = "n_trades_window"


@dataclass
class CheckResult:
    name: str
    status: str
    headline: str
    details: dict = field(default_factory=dict)


@dataclass
class ValidationReport:
    results: list[CheckResult] = field(default_factory=list)

    def add(self, r: CheckResult) -> None:
        self.results.append(r)
        c = COLOR.get(r.status, "")
        end = COLOR["END"]
        log.info(f"{c}[{r.status:4s}]{end}  {r.name:35s}  {r.headline}")
        for k, v in r.details.items():
            if not isinstance(v, dict):
                log.info(f"           - {k}: {v}")

    def summary_counts(self) -> dict:
        return {s: sum(1 for r in self.results if r.status == s)
                for s in ("PASS", "WARN", "FAIL", "INFO")}

    def overall_verdict(self) -> str:
        c = self.summary_counts()
        if c["FAIL"] > 0:
            return "FAIL"
        if c["WARN"] > 0:
            return "WARN"
        return "PASS"


def print_schemas(con: duckdb.DuckDBPyConnection) -> None:
    for view in ("meta", "book", "trades"):
        cols = con.sql(f"DESCRIBE {view}").df()["column_name"].tolist()
        log.info(f"  {view} columns: {cols}")


def check_files_exist(parquet_root: Path, coins: list[str],
                       rep: ValidationReport) -> dict:
    layers = {
        "book_snapshots":  "snapshots.parquet",
        "trade_window":    "trades.parquet",
        "snapshot_meta":   "meta.parquet",
    }
    sizes = {}
    missing = []
    for coin in coins:
        for layer, fname in layers.items():
            p = parquet_root / layer / f"coin={coin}" / fname
            if p.exists():
                sizes[f"{coin}_{layer}"] = round(p.stat().st_size / 1_048_576, 2)
            else:
                missing.append(str(p))
    if missing:
        rep.add(CheckResult("files_exist", "FAIL",
                            f"{len(missing)} expected files missing",
                            {"missing": missing[:5]}))
    else:
        rep.add(CheckResult("files_exist", "PASS",
                            f"all {len(layers)*len(coins)} parquets present",
                            sizes))
    return sizes


def check_snapshot_continuity(con, coins, rep, step_min=15):
    out = {}
    for coin in coins:
        df = con.sql(f"""
            SELECT {META_TS_COL} AS ts,
                   {META_TS_COL} - LAG({META_TS_COL}) OVER (ORDER BY {META_TS_COL})
                       AS gap_ms
            FROM meta WHERE coin = '{coin}'
        """).df()
        df["gap_min"] = df["gap_ms"] / 60_000
        gaps = df[df["gap_min"] > 1.5 * step_min]
        n_snaps = len(df)
        n_gaps = len(gaps)
        biggest = float(gaps["gap_min"].max()) if n_gaps else 0.0
        status = "PASS"
        if n_gaps > n_snaps * 0.05:
            status = "FAIL"
        elif n_gaps > 0:
            status = "WARN"
        rep.add(CheckResult(
            f"snapshot_continuity[{coin}]", status,
            f"{n_snaps} snaps, {n_gaps} gaps > {1.5*step_min:.0f} min "
            f"(biggest: {biggest:.1f} min)",
            {"n_snaps": n_snaps, "n_gaps": n_gaps,
             "biggest_gap_min": round(biggest, 2)},
        ))
        out[coin] = {"n_snaps": n_snaps, "n_gaps": n_gaps,
                     "biggest_gap_min": biggest}
    return out


def check_mid_price_sanity(con, coins, rep):
    out = {}
    for coin in coins:
        s = con.sql(f"""
            SELECT COUNT(*) AS n,
                   SUM(CASE WHEN {META_MID_COL} <= 0 THEN 1 ELSE 0 END) AS n_bad,
                   SUM(CASE WHEN {META_MID_COL} IS NULL THEN 1 ELSE 0 END) AS n_null,
                   MIN({META_MID_COL}) AS min_px,
                   MAX({META_MID_COL}) AS max_px,
                   AVG({META_MID_COL}) AS mean_px
            FROM meta WHERE coin = '{coin}'
        """).df().iloc[0].to_dict()
        df = con.sql(f"""
            SELECT ABS(LN({META_MID_COL}) -
                       LN(LAG({META_MID_COL}) OVER (ORDER BY {META_TS_COL})))
                       AS log_jump
            FROM meta WHERE coin = '{coin}' AND {META_MID_COL} > 0
        """).df().dropna()
        max_jump_pct = float(df["log_jump"].max() * 100) if len(df) else 0.0
        extreme = int((df["log_jump"] > 0.20).sum())
        status = "PASS"
        if int(s["n_bad"]) > 0 or int(s["n_null"]) > 0:
            status = "FAIL"
        elif extreme > int(s["n"]) * 0.001:
            status = "WARN"
        rep.add(CheckResult(
            f"mid_price_sanity[{coin}]", status,
            f"px range [${float(s['min_px']):.2f}, ${float(s['max_px']):.2f}], "
            f"{int(s['n_bad'])} bad, {extreme} >20% jumps",
            {"n": int(s["n"]),
             "min_px": round(float(s["min_px"]), 4),
             "max_px": round(float(s["max_px"]), 4),
             "mean_px": round(float(s["mean_px"]), 4),
             "max_jump_pct": round(max_jump_pct, 4),
             "extreme_jumps_gt_20pct": extreme},
        ))
        out[coin] = {"min_px": float(s["min_px"]),
                     "max_px": float(s["max_px"]),
                     "mean_px": float(s["mean_px"]),
                     "max_jump_pct": max_jump_pct}
    return out


def check_spread_distribution(con, coins, rep):
    out = {}
    for coin in coins:
        s = con.sql(f"""
            SELECT COUNT(*) AS n,
                   SUM(CASE WHEN {META_SPREAD_COL} <= 0 THEN 1 ELSE 0 END) AS n_zero,
                   SUM(CASE WHEN {META_SPREAD_COL} > 100 THEN 1 ELSE 0 END) AS n_wide,
                   QUANTILE_CONT({META_SPREAD_COL}, 0.05) AS p05,
                   QUANTILE_CONT({META_SPREAD_COL}, 0.50) AS p50,
                   QUANTILE_CONT({META_SPREAD_COL}, 0.95) AS p95,
                   AVG({META_SPREAD_COL}) AS mean
            FROM meta WHERE coin = '{coin}'
        """).df().iloc[0].to_dict()
        status = "PASS"
        if int(s["n_zero"]) > int(s["n"]) * 0.01:
            status = "FAIL"
        elif int(s["n_wide"]) > int(s["n"]) * 0.01:
            status = "WARN"
        rep.add(CheckResult(
            f"spread_distribution[{coin}]", status,
            f"median={float(s['p50']):.2f}bps  p95={float(s['p95']):.2f}bps  "
            f"({int(s['n_zero'])} zero, {int(s['n_wide'])} >100bps)",
            {"median_bps": round(float(s["p50"]), 4),
             "mean_bps": round(float(s["mean"]), 4),
             "p05_bps": round(float(s["p05"]), 4),
             "p95_bps": round(float(s["p95"]), 4),
             "n_zero_or_neg": int(s["n_zero"]),
             "n_wide_gt_100bps": int(s["n_wide"])},
        ))
        out[coin] = {k: (float(v) if isinstance(v, (int, float, np.number)) else v)
                     for k, v in s.items()}
    return out


def check_book_depth(con, coins, rep):
    """px and sz are already float64 in the schema — no CAST needed."""
    out = {}
    sizes_m = [0.01, 0.1, 1.0, 5.0, 10.0]
    for coin in coins:
        df = con.sql(f"""
            SELECT snapshot_id, side, SUM(px * sz) AS notional
            FROM book WHERE coin = '{coin}'
            GROUP BY snapshot_id, side
        """).df()
        bids = df[df["side"].str.upper().str.startswith("B")]["notional"].values
        asks = df[df["side"].str.upper().str.startswith("A")]["notional"].values
        bid_p = np.percentile(bids, [5, 25, 50, 75, 95]) if len(bids) else [0]*5
        ask_p = np.percentile(asks, [5, 25, 50, 75, 95]) if len(asks) else [0]*5
        feas = {}
        for sz_m in sizes_m:
            sz_usd = sz_m * 1_000_000
            n_fe = min(int((bids >= sz_usd).sum()), int((asks >= sz_usd).sum()))
            total = min(len(bids), len(asks))
            feas[f"${sz_m}M"] = round(100*n_fe/total, 2) if total else 0
        worst_1M = feas.get("$1.0M", 100)
        status = "PASS"
        if worst_1M < 50:
            status = "FAIL"
        elif worst_1M < 90:
            status = "WARN"
        rep.add(CheckResult(
            f"book_depth[{coin}]", status,
            f"median bid ${bid_p[2]/1e6:.2f}M, ask ${ask_p[2]/1e6:.2f}M  "
            f"|  $1M feasible: {worst_1M}% of snaps",
            {"median_bid_notional_usd": int(bid_p[2]),
             "median_ask_notional_usd": int(ask_p[2]),
             "p05_bid_notional_usd": int(bid_p[0]),
             "p95_bid_notional_usd": int(bid_p[4]),
             "feasibility_by_order_size_pct": feas},
        ))
        out[coin] = {"bid_percentiles": bid_p.tolist(),
                     "ask_percentiles": ask_p.tolist(),
                     "feasibility": feas}
    return out


def check_trade_window_integrity(con, coins, rep,
                                   window_before=5, window_after=30):
    out = {}
    lo, hi = -window_before * 60_000, window_after * 60_000
    for coin in coins:
        s = con.sql(f"""
            SELECT COUNT(*) AS n,
                   SUM(CASE WHEN ts_offset_ms < {lo} THEN 1 ELSE 0 END) AS too_early,
                   SUM(CASE WHEN ts_offset_ms > {hi} THEN 1 ELSE 0 END) AS too_late,
                   MIN(ts_offset_ms) AS min_offset,
                   MAX(ts_offset_ms) AS max_offset
            FROM trades WHERE coin = '{coin}'
        """).df().iloc[0].to_dict()
        bad = int(s["too_early"]) + int(s["too_late"])
        status = "PASS" if bad == 0 else "FAIL"
        rep.add(CheckResult(
            f"trade_window_integrity[{coin}]", status,
            f"{int(s['n']):,} trades, {bad} out-of-window  "
            f"(min={float(s['min_offset'])/60_000:.1f}min, "
            f"max={float(s['max_offset'])/60_000:.1f}min)",
            {"n_trades": int(s["n"]),
             "n_too_early": int(s["too_early"]),
             "n_too_late": int(s["too_late"]),
             "min_offset_min": round(float(s["min_offset"])/60_000, 2),
             "max_offset_min": round(float(s["max_offset"])/60_000, 2)},
        ))
        out[coin] = {"n_trades": int(s["n"]), "n_out_of_window": bad}
    return out


def check_builder_fee_population(con, coins, rep):
    out = {}
    cols = con.sql("DESCRIBE trades").df()["column_name"].tolist()
    has_builder = "builder" in cols
    has_builder_fee = "builder_fee" in cols
    has_fee = "fee" in cols
    for coin in coins:
        parts = ["COUNT(*) AS n"]
        if has_builder:
            parts.append("SUM(CASE WHEN builder IS NOT NULL AND builder != '' "
                         "THEN 1 ELSE 0 END) AS n_with_builder")
        if has_builder_fee:
            parts.append("SUM(CASE WHEN builder_fee IS NOT NULL AND builder_fee != 0 "
                         "THEN 1 ELSE 0 END) AS n_with_builder_fee")
            parts.append("AVG(NULLIF(builder_fee, 0)) AS mean_builder_fee")
        if has_fee:
            parts.append("SUM(CASE WHEN fee IS NOT NULL AND fee != 0 "
                         "THEN 1 ELSE 0 END) AS n_with_fee")
            parts.append("AVG(NULLIF(fee, 0)) AS mean_fee")
        s = con.sql(f"SELECT {', '.join(parts)} FROM trades WHERE coin = '{coin}'"
                    ).df().iloc[0].to_dict()
        n = int(s["n"])
        pct_b = 100*int(s.get("n_with_builder", 0))/n if n and has_builder else 0
        pct_bf = 100*int(s.get("n_with_builder_fee", 0))/n if n and has_builder_fee else 0
        pct_f = 100*int(s.get("n_with_fee", 0))/n if n and has_fee else 0
        head = []
        if has_builder: head.append(f"builder: {pct_b:.1f}%")
        if has_builder_fee: head.append(f"builder_fee>0: {pct_bf:.1f}%")
        if has_fee: head.append(f"fee>0: {pct_f:.1f}%")
        if not head: head = ["no builder/fee columns"]
        rep.add(CheckResult(
            f"builder_fee_population[{coin}]", "INFO", "  ".join(head),
            {"n_trades": n,
             "pct_with_builder": round(pct_b, 3),
             "pct_with_builder_fee": round(pct_bf, 3),
             "pct_with_fee": round(pct_f, 3),
             "mean_builder_fee": float(s.get("mean_builder_fee") or 0),
             "mean_fee": float(s.get("mean_fee") or 0)},
        ))
        out[coin] = {"pct_with_builder": pct_b,
                     "pct_with_builder_fee": pct_bf,
                     "pct_with_fee": pct_f}
    return out


def generate_plots(con, coins, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    plt.style.use("default")
    plt.rcParams.update({"figure.dpi": 110, "axes.grid": True,
                          "grid.alpha": 0.3, "font.size": 10})

    # 1. Mid-price evolution
    fig, axes = plt.subplots(len(coins), 1, figsize=(11, 3*len(coins)), sharex=False)
    if len(coins) == 1:
        axes = [axes]
    for ax, coin in zip(axes, coins):
        df = con.sql(f"""
            SELECT {META_TS_COL} AS ts_ms, {META_MID_COL} AS mid
            FROM meta WHERE coin = '{coin}' ORDER BY {META_TS_COL}
        """).df()
        df["ts"] = pd.to_datetime(df["ts_ms"], unit="ms", utc=True)
        ax.plot(df["ts"], df["mid"], linewidth=0.6)
        ax.set_title(f"{coin} — mid-price evolution ({len(df):,} snapshots)")
        ax.set_ylabel("Mid Price (USD)")
    plt.tight_layout()
    plt.savefig(out_dir / "01_mid_price_evolution.png", bbox_inches="tight")
    plt.close()

    # 2. Spread ECDF
    fig, ax = plt.subplots(figsize=(9, 5))
    for coin in coins:
        s = con.sql(f"SELECT {META_SPREAD_COL} AS s FROM meta WHERE coin = '{coin}' "
                    f"AND {META_SPREAD_COL} > 0 ORDER BY {META_SPREAD_COL}"
                    ).df()["s"].values
        x = np.sort(s)
        y = np.linspace(0, 1, len(x))
        ax.plot(x, y, label=coin, linewidth=1.4)
    ax.set_xscale("log")
    ax.set_xlabel("Spread (bps, log scale)")
    ax.set_ylabel("ECDF")
    ax.set_title("Spread distribution by coin (Phase 1)")
    ax.legend()
    plt.tight_layout()
    plt.savefig(out_dir / "02_spread_ecdf.png", bbox_inches="tight")
    plt.close()

    # 3. Book depth by level
    fig, ax = plt.subplots(figsize=(9, 5))
    for coin in coins:
        df = con.sql(f"""
            SELECT level, QUANTILE_CONT(cum_notional, 0.5) AS p50
            FROM (
                SELECT snapshot_id, level,
                       SUM(px * sz) OVER (
                           PARTITION BY snapshot_id, side
                           ORDER BY level
                           ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                       ) AS cum_notional
                FROM book WHERE coin = '{coin}'
            )
            GROUP BY level ORDER BY level
        """).df()
        ax.plot(df["level"] + 1, df["p50"] / 1_000_000, marker="o",
                markersize=3, linewidth=1.2, label=coin)
    ax.set_xlabel("Level (depth from top of book)")
    ax.set_ylabel("Median cumulative notional ($M)")
    ax.set_title("Cumulative depth by level — median across all snapshots")
    ax.legend()
    plt.tight_layout()
    plt.savefig(out_dir / "03_depth_by_level.png", bbox_inches="tight")
    plt.close()

    # 4. Trades-per-window
    fig, ax = plt.subplots(figsize=(9, 5))
    for coin in coins:
        n = con.sql(f"SELECT {META_TRADES_COL} AS n FROM meta WHERE coin = '{coin}'"
                    ).df()["n"].values
        ax.hist(n, bins=60, alpha=0.5, label=coin, density=True)
    ax.set_xlabel("Trades per 35-min window")
    ax.set_ylabel("Density")
    ax.set_title("Trade-count distribution per snapshot window")
    ax.legend()
    plt.tight_layout()
    plt.savefig(out_dir / "04_trades_per_window.png", bbox_inches="tight")
    plt.close()

    # 5. Daily volume
    fig, ax = plt.subplots(figsize=(11, 5))
    for coin in coins:
        df = con.sql(f"""
            SELECT date_trunc('day', to_timestamp(ts_ms/1000)) AS day,
                   SUM(sz * px) AS vol_usd
            FROM trades WHERE coin = '{coin}'
            GROUP BY day ORDER BY day
        """).df()
        ax.plot(df["day"], df["vol_usd"] / 1e6, linewidth=1.0, label=coin)
    ax.set_xlabel("Date")
    ax.set_ylabel("Daily trade-window volume ($M, log)")
    ax.set_yscale("log")
    ax.set_title("Daily trading volume (aggregated across snapshot windows)")
    ax.legend()
    plt.tight_layout()
    plt.savefig(out_dir / "05_daily_volume.png", bbox_inches="tight")
    plt.close()

    log.info(f"Plots saved to {out_dir}")


def generate_latex_table(rep, out_path: Path, coins: list[str]):
    rows = []
    for coin in coins:
        spread = next((r for r in rep.results
                       if r.name == f"spread_distribution[{coin}]"), None)
        depth = next((r for r in rep.results
                      if r.name == f"book_depth[{coin}]"), None)
        cont = next((r for r in rep.results
                     if r.name == f"snapshot_continuity[{coin}]"), None)
        twi = next((r for r in rep.results
                    if r.name == f"trade_window_integrity[{coin}]"), None)
        if all((spread, depth, cont, twi)):
            rows.append({
                "coin": coin,
                "n_snaps": cont.details["n_snaps"],
                "n_trades": twi.details["n_trades"],
                "median_spread_bps": spread.details["median_bps"],
                "p95_spread_bps": spread.details["p95_bps"],
                "median_bid_notional_M": round(
                    depth.details["median_bid_notional_usd"]/1e6, 2),
                "median_ask_notional_M": round(
                    depth.details["median_ask_notional_usd"]/1e6, 2),
                "feas_1M": depth.details["feasibility_by_order_size_pct"]["$1.0M"],
            })
    if not rows:
        log.warning("Could not assemble LaTeX summary — some checks missing.")
        return
    tex = [
        r"\begin{table}[h]",
        r"\centering",
        r"\caption{Phase 1 dataset summary statistics. Median figures across "
        r"all snapshots in the study window (Dec 15, 2025 -- May 12, 2026).}",
        r"\label{tab:phase1_data_summary}",
        r"\begin{tabular}{lrrrrrrr}",
        r"\toprule",
        r"Coin & N snaps & N trades & Median & p95 & Bid d. & Ask d. & \$1M \\",
        r" & & & spread & spread & top-20 & top-20 & feas. \\",
        r" & & & (bps) & (bps) & (\$M) & (\$M) & (\%) \\",
        r"\midrule",
    ]
    for r in rows:
        tex.append(
            f"{r['coin']} & {r['n_snaps']:,} & {r['n_trades']:,} & "
            f"{r['median_spread_bps']:.2f} & {r['p95_spread_bps']:.2f} & "
            f"{r['median_bid_notional_M']:.2f} & {r['median_ask_notional_M']:.2f} & "
            f"{r['feas_1M']:.1f} \\\\"
        )
    tex += [r"\bottomrule", r"\end{tabular}", r"\end{table}"]
    out_path.write_text("\n".join(tex), encoding="utf-8")
    log.info(f"LaTeX summary written to {out_path}")


def main():
    p = argparse.ArgumentParser(description="Phase 1 Data Quality Check")
    p.add_argument("--data-dir",
                   default=r"C:\Users\grego\Downloads\fetcher\data")
    p.add_argument("--no-plots", action="store_true")
    args = p.parse_args()

    data_root = Path(args.data_dir)
    parquet_root = data_root / "parquet"
    validation_dir = data_root / "validation"
    validation_dir.mkdir(parents=True, exist_ok=True)

    if not parquet_root.exists():
        log.error(f"Parquet directory not found: {parquet_root}")
        sys.exit(2)

    coins = sorted([
        p.name.split("=", 1)[1]
        for p in (parquet_root / "book_snapshots").glob("coin=*")
        if p.is_dir()
    ])
    if not coins:
        log.error("No coin partitions found under book_snapshots/")
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

    rep = ValidationReport()
    print()
    log.info(f"{COLOR['BOLD']}=== Phase 1 Data Quality Check ==={COLOR['END']}")
    log.info(f"Data root: {data_root}")
    log.info(f"Coins:     {coins}")
    log.info("Schemas:")
    print_schemas(con)
    print()

    all_metrics = {
        "run_at_utc": datetime.now(timezone.utc).isoformat(),
        "coins": coins,
        "data_root": str(data_root),
    }
    all_metrics["files"] = check_files_exist(parquet_root, coins, rep)
    all_metrics["snapshot_continuity"] = check_snapshot_continuity(con, coins, rep)
    all_metrics["mid_price_sanity"] = check_mid_price_sanity(con, coins, rep)
    all_metrics["spread_distribution"] = check_spread_distribution(con, coins, rep)
    all_metrics["book_depth"] = check_book_depth(con, coins, rep)
    all_metrics["trade_window_integrity"] = check_trade_window_integrity(con, coins, rep)
    all_metrics["builder_fee_population"] = check_builder_fee_population(con, coins, rep)

    print()
    counts = rep.summary_counts()
    verdict = rep.overall_verdict()
    c = COLOR.get(verdict, "")
    log.info(f"{COLOR['BOLD']}=== SUMMARY ==={COLOR['END']}")
    log.info(f"  PASS: {counts['PASS']}   WARN: {counts['WARN']}   "
             f"FAIL: {counts['FAIL']}   INFO: {counts['INFO']}")
    log.info(f"  {c}OVERALL: {verdict}{COLOR['END']}")
    print()

    def _convert(v):
        if isinstance(v, (np.integer,)): return int(v)
        if isinstance(v, (np.floating,)): return float(v)
        if isinstance(v, dict): return {k: _convert(val) for k, val in v.items()}
        if isinstance(v, (list, tuple)): return [_convert(x) for x in v]
        return v

    (validation_dir / "metrics.json").write_text(
        json.dumps(_convert(all_metrics), indent=2, default=str),
        encoding="utf-8",
    )
    log.info(f"Metrics JSON: {validation_dir / 'metrics.json'}")

    if not args.no_plots:
        generate_plots(con, coins, validation_dir / "plots")

    generate_latex_table(rep, validation_dir / "summary.tex", coins)

    if verdict == "FAIL":
        sys.exit(1)


if __name__ == "__main__":
    main()
