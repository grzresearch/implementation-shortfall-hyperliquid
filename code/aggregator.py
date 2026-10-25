"""
Modul 6 — Aggregator: Paper-Relevant Plots & Tables
====================================================

Reads IS decomposition results and produces:

  Plots (data/aggregator/plots/):
    01_is_boxplot_by_size.png        IS distribution by (coin, size), per strategy
    02_decomposition_stacked.png     Mean IS decomposition: Spread / Impact / Fee / Opp
    03_strategy_comparison.png       Bar chart: which strategy wins per size
    04_fee_sensitivity.png           IS as function of builder fee
    05_temporal_effects.png          IS by hour-of-day and day-of-week
    06_slippage_curves.png           Mean execution VWAP slippage vs order size
    07_completion_by_size.png        Fill-completion % distributions
    08_size_scaling.png              Median IS as function of $size (log-log)

  Tables (data/aggregator/tables/):
    main_results.tex                 Main IS table for paper Section 5.1
    decomposition.tex                Component breakdown table
    strategy_winners.tex             Best-strategy-per-size table
    fee_sensitivity.tex              Fee scenarios as appendix

  Summary (data/aggregator/):
    summary_stats.csv                All aggregated stats for ad-hoc analysis

Usage:
    python aggregator.py
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pyarrow.parquet as pq

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("aggregator")

# Consistent palette across plots
PALETTE = {
    "BTC":  "#F7931A",
    "ETH":  "#627EEA",
    "HYPE": "#50D2C1",
    "NAIVE_MARKET":  "#D62728",
    "MARKET_CAP5":   "#FF7F0E",
    "TWAP_5MIN":     "#2CA02C",
    "TWAP_30MIN":    "#1F77B4",
    "VWAP_30MIN":    "#9467BD",
}
STRATEGY_LABEL = {
    "NAIVE_MARKET":  "Naive Market",
    "MARKET_CAP5":   "Market+5\\%Cap",
    "TWAP_5MIN":     "TWAP-5min",
    "TWAP_30MIN":    "TWAP-30min",
    "VWAP_30MIN":    "VWAP-30min",
}
COMPONENT_COLORS = {
    "spread":      "#4C72B0",
    "impact":      "#DD8452",
    "fee":         "#55A868",
    "opportunity": "#C44E52",
}


def fmt_size(usd: float) -> str:
    if usd >= 1_000_000:
        v = usd / 1_000_000
        return f"${v:.0f}M" if v == int(v) else f"${v:.1f}M"
    if usd >= 1_000:
        return f"${usd/1000:.0f}k"
    return f"${usd:.0f}"


# ---------------------------------------------------------------------------
# Plots
# ---------------------------------------------------------------------------


def plot_is_boxplot_by_size(df: pd.DataFrame, out: Path):
    """One subplot per coin. X = order size. Color = strategy. Y = total IS bps."""
    coins = sorted(df["coin"].unique())
    fig, axes = plt.subplots(1, len(coins), figsize=(5*len(coins), 5),
                               sharey=False)
    if len(coins) == 1:
        axes = [axes]
    strategies = ["NAIVE_MARKET", "MARKET_CAP5", "TWAP_5MIN", "TWAP_30MIN", "VWAP_30MIN"]
    for ax, coin in zip(axes, coins):
        sub = df[(df["coin"] == coin) & (df["side"] == "BUY")]
        sizes = sorted(sub["order_size_usd"].unique())
        size_labels = [fmt_size(s) for s in sizes]
        x_positions = np.arange(len(sizes))
        width = 0.16

        for i, strat in enumerate(strategies):
            data = [
                sub[(sub["order_size_usd"] == sz) & (sub["strategy"] == strat)][
                    "total_is_baseline_bps"].clip(lower=-50, upper=200).values
                for sz in sizes
            ]
            offset = (i - len(strategies)/2 + 0.5) * width
            bp = ax.boxplot(
                data, positions=x_positions + offset, widths=width*0.9,
                patch_artist=True, showfliers=False,
                medianprops=dict(color="white", linewidth=1.2),
            )
            color = PALETTE.get(strat, "#888")
            for patch in bp["boxes"]:
                patch.set_facecolor(color)
                patch.set_alpha(0.85)

        ax.set_xticks(x_positions)
        ax.set_xticklabels(size_labels)
        ax.set_xlabel("Order size")
        ax.set_ylabel("Total IS (bps)")
        ax.set_title(f"{coin} — IS distribution (BUY, baseline fees)")
        ax.grid(True, alpha=0.3, axis="y")
        ax.axhline(0, color="black", linewidth=0.5)

    # legend
    handles = [plt.Rectangle((0, 0), 1, 1,
                              facecolor=PALETTE.get(s, "#888"), alpha=0.85)
               for s in strategies]
    fig.legend(handles, [STRATEGY_LABEL[s].replace("\\%", "%") for s in strategies],
               loc="lower center", ncol=5, bbox_to_anchor=(0.5, -0.02))
    plt.tight_layout(rect=(0, 0.05, 1, 1))
    plt.savefig(out / "01_is_boxplot_by_size.png", dpi=140, bbox_inches="tight")
    plt.close()


def plot_decomposition_stacked(df: pd.DataFrame, out: Path):
    """Per coin, mean IS components stacked across order sizes (BUY, TWAP-30min)."""
    coins = sorted(df["coin"].unique())
    fig, axes = plt.subplots(1, len(coins), figsize=(5*len(coins), 5), sharey=False)
    if len(coins) == 1:
        axes = [axes]
    strat = "TWAP_30MIN"
    for ax, coin in zip(axes, coins):
        sub = df[(df["coin"] == coin) & (df["side"] == "BUY") &
                  (df["strategy"] == strat)]
        agg = (sub.groupby("order_size_usd")
                   [["spread_bps", "impact_bps", "fee_base_bps",
                     "opportunity_bps"]]
                   .mean().sort_index())
        sizes = agg.index.values
        x = np.arange(len(sizes))
        labels = [fmt_size(s) for s in sizes]

        bottom = np.zeros(len(sizes))
        for col, label, color in [
            ("spread_bps",      "Spread",      COMPONENT_COLORS["spread"]),
            ("impact_bps",      "Impact",      COMPONENT_COLORS["impact"]),
            ("fee_base_bps",    "Fee (3.5bp)", COMPONENT_COLORS["fee"]),
            ("opportunity_bps", "Opportunity", COMPONENT_COLORS["opportunity"]),
        ]:
            vals = agg[col].values
            ax.bar(x, vals, bottom=bottom, label=label, color=color, alpha=0.9)
            bottom = bottom + vals
        ax.set_xticks(x)
        ax.set_xticklabels(labels)
        ax.set_xlabel("Order size")
        ax.set_ylabel("Mean IS (bps)")
        ax.set_title(f"{coin} — IS decomposition (BUY, TWAP-30min)")
        ax.grid(True, alpha=0.3, axis="y")
        if ax is axes[-1]:
            ax.legend(loc="upper left", fontsize=9)
    plt.tight_layout()
    plt.savefig(out / "02_decomposition_stacked.png", dpi=140, bbox_inches="tight")
    plt.close()


def plot_strategy_comparison(df: pd.DataFrame, out: Path):
    """Bar chart: median IS for each strategy per coin × size."""
    coins = sorted(df["coin"].unique())
    fig, axes = plt.subplots(len(coins), 1, figsize=(11, 4*len(coins)),
                               sharex=False)
    if len(coins) == 1:
        axes = [axes]
    strategies = ["NAIVE_MARKET", "MARKET_CAP5", "TWAP_5MIN", "TWAP_30MIN", "VWAP_30MIN"]
    for ax, coin in zip(axes, coins):
        sub = df[(df["coin"] == coin) & (df["side"] == "BUY")]
        sizes = sorted(sub["order_size_usd"].unique())
        x = np.arange(len(sizes))
        width = 0.16
        for i, strat in enumerate(strategies):
            vals = [
                sub[(sub["order_size_usd"] == sz) & (sub["strategy"] == strat)][
                    "total_is_baseline_bps"].median()
                for sz in sizes
            ]
            ax.bar(x + (i - 2)*width, vals, width=width*0.95,
                   label=STRATEGY_LABEL[strat].replace("\\%", "%"),
                   color=PALETTE.get(strat, "#888"), alpha=0.9)
        ax.set_xticks(x)
        ax.set_xticklabels([fmt_size(s) for s in sizes])
        ax.set_xlabel("Order size")
        ax.set_ylabel("Median total IS (bps)")
        ax.set_title(f"{coin} — Strategy comparison (BUY)")
        ax.grid(True, alpha=0.3, axis="y")
        ax.legend(fontsize=9, ncol=3)
    plt.tight_layout()
    plt.savefig(out / "03_strategy_comparison.png", dpi=140, bbox_inches="tight")
    plt.close()


def plot_fee_sensitivity(df: pd.DataFrame, out: Path):
    """IS as a function of builder fee — one line per coin for TWAP-30 BUY."""
    fee_cols = [c for c in df.columns if c.startswith("fee_builder_")]
    fee_levels = [int(c.replace("fee_builder_", "").replace("bp", ""))
                  for c in fee_cols]
    fig, ax = plt.subplots(figsize=(8, 5))
    strat = "TWAP_30MIN"
    for coin in sorted(df["coin"].unique()):
        sub = df[(df["coin"] == coin) & (df["side"] == "BUY") &
                  (df["strategy"] == strat)]
        if len(sub) == 0:
            continue
        # Use median of (spread + impact + opp + fee_builder_X) across all sizes
        # Pick the largest order size for max sensitivity
        max_size = sub["order_size_usd"].max()
        sub_top = sub[sub["order_size_usd"] == max_size]
        base = sub_top[["spread_bps", "impact_bps", "opportunity_bps"]].sum(axis=1)
        medians = []
        for bf, col in zip(fee_levels, fee_cols):
            total = base + sub_top[col]
            medians.append(total.median())
        ax.plot(fee_levels, medians, marker="o", color=PALETTE.get(coin, "#888"),
                label=f"{coin} ({fmt_size(max_size)})", linewidth=2)
    ax.set_xlabel("Builder fee (bps)")
    ax.set_ylabel("Median total IS (bps)")
    ax.set_title("Fee sensitivity — TWAP-30min BUY, largest order size")
    ax.grid(True, alpha=0.3)
    ax.legend()
    plt.tight_layout()
    plt.savefig(out / "04_fee_sensitivity.png", dpi=140, bbox_inches="tight")
    plt.close()


def plot_temporal_effects(df: pd.DataFrame, out: Path):
    """IS by hour-of-day and day-of-week."""
    df = df.copy()
    df["ts"] = pd.to_datetime(df["arrival_ts_ms"], unit="ms", utc=True)
    df["hour"] = df["ts"].dt.hour
    df["dow"] = df["ts"].dt.dayofweek  # 0=Mon

    fig, axes = plt.subplots(1, 2, figsize=(13, 4.5))
    strat = "TWAP_30MIN"
    sub = df[(df["strategy"] == strat) & (df["side"] == "BUY")]

    # By hour
    for coin in sorted(sub["coin"].unique()):
        s = sub[sub["coin"] == coin]
        med = s.groupby("hour")["total_is_baseline_bps"].median()
        axes[0].plot(med.index, med.values, marker="o", linewidth=1.5,
                      color=PALETTE.get(coin, "#888"), label=coin)
    axes[0].set_xlabel("Hour of day (UTC)")
    axes[0].set_ylabel("Median total IS (bps)")
    axes[0].set_title("IS by hour-of-day — TWAP-30min BUY")
    axes[0].grid(True, alpha=0.3)
    axes[0].legend()
    axes[0].set_xticks(range(0, 24, 3))

    # By day-of-week
    dow_labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    for coin in sorted(sub["coin"].unique()):
        s = sub[sub["coin"] == coin]
        med = s.groupby("dow")["total_is_baseline_bps"].median()
        axes[1].plot(med.index, med.values, marker="o", linewidth=1.5,
                      color=PALETTE.get(coin, "#888"), label=coin)
    axes[1].set_xticks(range(7))
    axes[1].set_xticklabels(dow_labels)
    axes[1].set_xlabel("Day of week")
    axes[1].set_ylabel("Median total IS (bps)")
    axes[1].set_title("IS by day-of-week — TWAP-30min BUY")
    axes[1].grid(True, alpha=0.3)
    axes[1].legend()

    plt.tight_layout()
    plt.savefig(out / "05_temporal_effects.png", dpi=140, bbox_inches="tight")
    plt.close()


def plot_slippage_curves(df: pd.DataFrame, out: Path):
    """Median VWAP slippage (in bps) as function of size, per coin."""
    fig, ax = plt.subplots(figsize=(8, 5))
    strat = "NAIVE_MARKET"  # cleanest signal: market sweep
    sub = df[(df["strategy"] == strat) & (df["side"] == "BUY")].copy()
    sub["slip_bps"] = (10_000 * (sub["execution_vwap"] - sub["arrival_mid"])
                        / sub["arrival_mid"])
    for coin in sorted(sub["coin"].unique()):
        s = sub[sub["coin"] == coin]
        med = s.groupby("order_size_usd")["slip_bps"].median()
        ax.plot(med.index, med.values, marker="o", linewidth=2,
                color=PALETTE.get(coin, "#888"), label=coin)
    ax.set_xscale("log")
    ax.set_xlabel("Order size (USD, log scale)")
    ax.set_ylabel("Median VWAP slippage vs arrival mid (bps)")
    ax.set_title("Slippage scaling with order size — Naive Market BUY")
    ax.grid(True, alpha=0.3, which="both")
    ax.legend()
    plt.tight_layout()
    plt.savefig(out / "06_slippage_curves.png", dpi=140, bbox_inches="tight")
    plt.close()


def plot_completion_by_size(df: pd.DataFrame, out: Path):
    """Fill completion % distribution per coin × size."""
    coins = sorted(df["coin"].unique())
    fig, axes = plt.subplots(1, len(coins), figsize=(5*len(coins), 4.5),
                               sharey=True)
    if len(coins) == 1:
        axes = [axes]
    strat = "NAIVE_MARKET"
    for ax, coin in zip(axes, coins):
        sub = df[(df["coin"] == coin) & (df["side"] == "BUY") &
                  (df["strategy"] == strat)]
        sizes = sorted(sub["order_size_usd"].unique())
        data = [sub[sub["order_size_usd"] == sz]["fill_completion_pct"].values
                for sz in sizes]
        bp = ax.boxplot(data, tick_labels=[fmt_size(s) for s in sizes],
                          patch_artist=True, showfliers=False)
        for patch in bp["boxes"]:
            patch.set_facecolor(PALETTE.get(coin, "#888"))
            patch.set_alpha(0.85)
        ax.set_xlabel("Order size")
        ax.set_ylabel("Fill completion (%)")
        ax.set_title(f"{coin} — Fill completion (Naive Market BUY)")
        ax.grid(True, alpha=0.3, axis="y")
        ax.set_ylim(0, 105)
    plt.tight_layout()
    plt.savefig(out / "07_completion_by_size.png", dpi=140, bbox_inches="tight")
    plt.close()


def plot_size_scaling(df: pd.DataFrame, out: Path):
    """Median IS as function of size — log-log to test sqrt(size) scaling."""
    fig, ax = plt.subplots(figsize=(8, 5.5))
    strat = "TWAP_30MIN"
    sub = df[(df["strategy"] == strat) & (df["side"] == "BUY")]
    for coin in sorted(sub["coin"].unique()):
        s = sub[sub["coin"] == coin]
        med = s.groupby("order_size_usd")["total_is_baseline_bps"].median()
        ax.plot(med.index, med.values, marker="o", linewidth=2, markersize=7,
                color=PALETTE.get(coin, "#888"), label=coin)
    # Reference line: sqrt(size) scaling, normalized
    ref_sizes = np.array([10_000, 10_000_000])
    if len(sub):
        # Anchor on smallest BTC point if available
        anchor = sub[(sub["coin"] == "BTC") & (sub["order_size_usd"] == 10_000)]
        if len(anchor):
            anchor_y = float(anchor["total_is_baseline_bps"].median())
            ref_y = anchor_y * np.sqrt(ref_sizes / 10_000)
            ax.plot(ref_sizes, ref_y, "k--", alpha=0.4,
                    label="$\\sqrt{size}$ reference")
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("Order size (USD, log)")
    ax.set_ylabel("Median total IS (bps, log)")
    ax.set_title("Size-scaling of execution costs — TWAP-30min BUY")
    ax.grid(True, alpha=0.3, which="both")
    ax.legend()
    plt.tight_layout()
    plt.savefig(out / "08_size_scaling.png", dpi=140, bbox_inches="tight")
    plt.close()


# ---------------------------------------------------------------------------
# LaTeX tables
# ---------------------------------------------------------------------------


def write_main_results_table(df: pd.DataFrame, out: Path):
    """Median total IS per (coin, size) for TWAP-30min BUY — paper Section 5.1."""
    strat = "TWAP_30MIN"
    sub = df[(df["strategy"] == strat) & (df["side"] == "BUY")]
    pivot = (sub.pivot_table(index="coin", columns="order_size_usd",
                              values="total_is_baseline_bps",
                              aggfunc="median")
                 .sort_index())
    lines = [
        r"\begin{table}[h]",
        r"\centering",
        r"\caption{Median total Implementation Shortfall (bps) by coin and order size, "
        r"executed via TWAP-30min strategy on the BUY side. Baseline fee = 3.5 bps "
        r"taker. Sample: Dec 15, 2025 -- May 12, 2026.}",
        r"\label{tab:main_is_results}",
    ]
    sizes = sorted(pivot.columns)
    col_spec = "l" + "r" * len(sizes)
    lines.append(rf"\begin{{tabular}}{{{col_spec}}}")
    lines.append(r"\toprule")
    header = "Coin & " + " & ".join(fmt_size(s) for s in sizes) + r" \\"
    lines.append(header)
    lines.append(r"\midrule")
    for coin in pivot.index:
        row = pivot.loc[coin]
        vals = " & ".join(
            f"{row[s]:.2f}" if pd.notna(row[s]) else "—" for s in sizes
        )
        lines.append(f"{coin} & {vals} \\\\")
    lines.append(r"\bottomrule")
    lines.append(r"\end{tabular}")
    lines.append(r"\end{table}")
    out.write_text("\n".join(lines), encoding="utf-8")


def write_decomposition_table(df: pd.DataFrame, out: Path):
    """IS component breakdown — mean of each component as % of total."""
    strat = "TWAP_30MIN"
    sub = df[(df["strategy"] == strat) & (df["side"] == "BUY")]
    agg = (sub.groupby("coin")
              [["spread_bps", "impact_bps", "fee_base_bps", "opportunity_bps"]]
              .mean())
    agg["total"] = agg.sum(axis=1)
    for c in ["spread_bps", "impact_bps", "fee_base_bps", "opportunity_bps"]:
        agg[f"{c}_pct"] = 100 * agg[c] / agg["total"]

    lines = [
        r"\begin{table}[h]",
        r"\centering",
        r"\caption{IS component decomposition (mean across all snapshots and order "
        r"sizes), TWAP-30min BUY. Baseline fee = 3.5 bps taker, 0 bps builder.}",
        r"\label{tab:is_decomposition}",
        r"\begin{tabular}{lrrrrr}",
        r"\toprule",
        r"Coin & Spread & Impact & Fee & Opportunity & Total \\",
        r" & (bps) & (bps) & (bps) & (bps) & (bps) \\",
        r"\midrule",
    ]
    for coin in agg.index:
        r = agg.loc[coin]
        lines.append(
            f"{coin} & {r['spread_bps']:.2f} & {r['impact_bps']:.2f} & "
            f"{r['fee_base_bps']:.2f} & {r['opportunity_bps']:.2f} & "
            f"{r['total']:.2f} \\\\"
        )
    lines.append(r"\bottomrule")
    lines.append(r"\end{tabular}")
    lines.append(r"\end{table}")
    out.write_text("\n".join(lines), encoding="utf-8")


def write_strategy_winners_table(df: pd.DataFrame, out: Path):
    """For each (coin, size), best strategy by median IS."""
    sub = df[df["side"] == "BUY"]
    medians = (sub.groupby(["coin", "order_size_usd", "strategy"])
                  ["total_is_baseline_bps"].median().reset_index())
    winners = medians.loc[medians.groupby(["coin", "order_size_usd"])
                          ["total_is_baseline_bps"].idxmin()]
    lines = [
        r"\begin{table}[h]",
        r"\centering",
        r"\caption{Best execution strategy (lowest median total IS) per coin and "
        r"order size, on the BUY side. Baseline fees.}",
        r"\label{tab:strategy_winners}",
        r"\begin{tabular}{lrll}",
        r"\toprule",
        r"Coin & Order size & Best strategy & Median IS (bps) \\",
        r"\midrule",
    ]
    for _, row in winners.iterrows():
        lines.append(
            f"{row['coin']} & {fmt_size(row['order_size_usd'])} & "
            f"{STRATEGY_LABEL[row['strategy']]} & "
            f"{row['total_is_baseline_bps']:.2f} \\\\"
        )
    lines.append(r"\bottomrule")
    lines.append(r"\end{tabular}")
    lines.append(r"\end{table}")
    out.write_text("\n".join(lines), encoding="utf-8")


def write_fee_sensitivity_table(df: pd.DataFrame, out: Path):
    """Sensitivity of IS to builder fee — for max order size only."""
    strat = "TWAP_30MIN"
    sub = df[(df["strategy"] == strat) & (df["side"] == "BUY")]
    fee_cols = [c for c in sub.columns if c.startswith("fee_builder_")]
    fee_levels = sorted([int(c.replace("fee_builder_", "").replace("bp", ""))
                          for c in fee_cols])
    lines = [
        r"\begin{table}[h]",
        r"\centering",
        r"\caption{IS sensitivity to builder fee. Median total IS (bps) for the "
        r"largest order size per coin, TWAP-30min BUY.}",
        r"\label{tab:fee_sensitivity}",
        r"\begin{tabular}{l" + "r" * len(fee_levels) + r"}",
        r"\toprule",
        "Coin & " + " & ".join(f"{bf} bp" for bf in fee_levels) + r" \\",
        r"\midrule",
    ]
    for coin in sorted(sub["coin"].unique()):
        ss = sub[sub["coin"] == coin]
        max_size = ss["order_size_usd"].max()
        ss_top = ss[ss["order_size_usd"] == max_size]
        base = ss_top[["spread_bps", "impact_bps", "opportunity_bps"]].sum(axis=1)
        vals = []
        for bf in fee_levels:
            total = (base + ss_top[f"fee_builder_{bf}bp"]).median()
            vals.append(f"{total:.2f}")
        lines.append(f"{coin} ({fmt_size(max_size)}) & " + " & ".join(vals) + r" \\")
    lines.append(r"\bottomrule")
    lines.append(r"\end{tabular}")
    lines.append(r"\end{table}")
    out.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Summary CSV
# ---------------------------------------------------------------------------


def write_summary_csv(df: pd.DataFrame, out: Path):
    summary = (df.groupby(["coin", "side", "strategy", "order_size_usd"])
                  .agg(
                      n_obs=("total_is_baseline_bps", "count"),
                      median_total_is_bps=("total_is_baseline_bps", "median"),
                      mean_total_is_bps=("total_is_baseline_bps", "mean"),
                      p05_total_is_bps=("total_is_baseline_bps",
                                          lambda x: x.quantile(0.05)),
                      p95_total_is_bps=("total_is_baseline_bps",
                                          lambda x: x.quantile(0.95)),
                      median_spread_bps=("spread_bps", "median"),
                      median_impact_bps=("impact_bps", "median"),
                      median_fee_bps=("fee_base_bps", "median"),
                      median_opp_bps=("opportunity_bps", "median"),
                      median_completion_pct=("fill_completion_pct", "median"),
                  ).reset_index())
    summary.to_csv(out, index=False, float_format="%.4f")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--data-dir",
                   default=str(Path(__file__).resolve().parents[1] / "data"))
    args = p.parse_args()

    data_root = Path(args.data_dir)
    is_path = data_root / "is_results" / "is_decomp.parquet"
    out_dir = data_root / "aggregator"
    plots_dir = out_dir / "plots"
    tables_dir = out_dir / "tables"
    plots_dir.mkdir(parents=True, exist_ok=True)
    tables_dir.mkdir(parents=True, exist_ok=True)

    if not is_path.exists():
        log.error("IS results not found: %s", is_path)
        log.error("Run is_decomp.py first.")
        sys.exit(2)

    log.info("=" * 60)
    log.info("Aggregator — Modul 6")
    log.info("=" * 60)

    log.info("Loading IS results ...")
    t0 = time.perf_counter()
    df = pq.read_table(is_path).to_pandas()
    log.info("  %s rows loaded in %.1fs", f"{len(df):,}",
             time.perf_counter() - t0)

    # Filter out any NaN-IS rows (shouldn't be many)
    n_before = len(df)
    df = df.dropna(subset=["total_is_baseline_bps", "arrival_mid"])
    if len(df) < n_before:
        log.info("  dropped %d rows with NaN IS",
                 n_before - len(df))

    # Style
    plt.style.use("default")
    plt.rcParams.update({"figure.dpi": 110, "font.size": 10,
                          "axes.grid": True, "grid.alpha": 0.3})

    log.info("Generating plots ...")
    plot_is_boxplot_by_size(df, plots_dir)
    log.info("  01_is_boxplot_by_size.png")
    plot_decomposition_stacked(df, plots_dir)
    log.info("  02_decomposition_stacked.png")
    plot_strategy_comparison(df, plots_dir)
    log.info("  03_strategy_comparison.png")
    plot_fee_sensitivity(df, plots_dir)
    log.info("  04_fee_sensitivity.png")
    plot_temporal_effects(df, plots_dir)
    log.info("  05_temporal_effects.png")
    plot_slippage_curves(df, plots_dir)
    log.info("  06_slippage_curves.png")
    plot_completion_by_size(df, plots_dir)
    log.info("  07_completion_by_size.png")
    plot_size_scaling(df, plots_dir)
    log.info("  08_size_scaling.png")

    log.info("Generating LaTeX tables ...")
    write_main_results_table(df, tables_dir / "main_results.tex")
    log.info("  main_results.tex")
    write_decomposition_table(df, tables_dir / "decomposition.tex")
    log.info("  decomposition.tex")
    write_strategy_winners_table(df, tables_dir / "strategy_winners.tex")
    log.info("  strategy_winners.tex")
    write_fee_sensitivity_table(df, tables_dir / "fee_sensitivity.tex")
    log.info("  fee_sensitivity.tex")

    log.info("Writing summary CSV ...")
    write_summary_csv(df, out_dir / "summary_stats.csv")
    log.info("  summary_stats.csv")

    log.info("=" * 60)
    log.info("DONE in %.1fs", time.perf_counter() - t0)
    log.info("Plots:  %s", plots_dir)
    log.info("Tables: %s", tables_dir)


if __name__ == "__main__":
    main()
