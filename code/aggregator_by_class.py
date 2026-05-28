"""
Modul 6.5 — Aggregator by Asset Class (v2, Tier 1+2 = 23 markets, 8 classes)
=============================================================================

Generates publication-grade plots and LaTeX tables structured by paper section:

  Section 5.1  crypto             BTC, ETH, HYPE                  (3)
  Section 5.2  equities_megacap   NVDA, TSLA, AAPL                (3)
  Section 5.3  indices_etf        XYZ100, SP500, EWY              (3)
  Section 5.4  commodities        GOLD, SILVER, CL                (3)
  Section 5.5  forex              EUR, JPY                        (2)
  Section 5.6  pre_ipo            ANTHROPIC, SPACEX, OPENAI       (3)
  Section 5.7  CROSS-CLASS MASTER PLOTS                           ─
  Section 5.8  equities_crypto    MSTR, COIN, HOOD     (Tier 2)   (3)
  Appendix     thematic_indices   MAG7, SEMIS, INFOTECH (Tier 2)  (3)

Output structure (data/aggregator_by_class/):
  plots/
    00_master_is_by_class.png
    00_master_decomposition.png
    00_master_fee_sensitivity.png
    00_master_completion.png
    crypto/01_is_boxplot.png  …  05_completion.png
    equities_megacap/…
    equities_crypto/…
    indices_etf/…
    commodities/…
    forex/…
    pre_ipo/…
    thematic_indices/…
  tables/
    master_cross_class.tex
    by_class_<name>.tex          (one per class)
    strategy_winners_by_class.tex
  summary_by_class.csv

Usage:
    python aggregator_by_class.py
"""

from __future__ import annotations

import argparse
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
log = logging.getLogger("agg_by_class")


# ---------------------------------------------------------------------------
# Asset-class taxonomy (Tier 1+2)
# ---------------------------------------------------------------------------

ASSET_CLASSES: dict[str, list[str]] = {
    "crypto":            ["BTC", "ETH", "HYPE"],
    "equities_megacap":  ["NVDA", "TSLA", "AAPL"],
    "equities_crypto":   ["MSTR", "COIN", "HOOD"],
    "indices_etf":       ["XYZ100", "SP500", "EWY"],
    "commodities":       ["GOLD", "SILVER", "CL"],
    "forex":             ["EUR", "JPY"],
    "pre_ipo":           ["ANTHROPIC", "SPACEX", "OPENAI"],
    "thematic_indices":  ["MAG7", "SEMIS", "INFOTECH"],
}

CLASS_DISPLAY: dict[str, str] = {
    "crypto":            "Crypto perps (native HL)",
    "equities_megacap":  "Equities — Mega-Cap (Trade.xyz)",
    "equities_crypto":   "Equities — Crypto-adjacent (Trade.xyz)",
    "indices_etf":       "Indices \\& ETFs (Trade.xyz)",
    "commodities":       "Commodities (Trade.xyz)",
    "forex":             "Forex (Trade.xyz)",
    "pre_ipo":           "Pre-IPO (Ventuals)",
    "thematic_indices":  "Thematic Indices (Ventuals)",
}

# Plot ordering for cross-class plots (paper Section 5.7 master plot)
CLASS_ORDER = [
    "crypto", "equities_megacap", "equities_crypto",
    "indices_etf", "commodities", "forex",
    "pre_ipo", "thematic_indices",
]

CLASS_COLOR: dict[str, str] = {
    "crypto":            "#F7931A",
    "equities_megacap":  "#76B900",
    "equities_crypto":   "#9933CC",
    "indices_etf":       "#0066CC",
    "commodities":       "#FFD700",
    "forex":             "#003399",
    "pre_ipo":           "#10A37F",
    "thematic_indices":  "#FF6B6B",
}

COIN_COLOR: dict[str, str] = {
    # Crypto
    "BTC":  "#F7931A", "ETH":  "#627EEA", "HYPE": "#50D2C1",
    # Mega-cap equities
    "NVDA": "#76B900", "TSLA": "#CC0000", "AAPL": "#A2AAAD",
    # Crypto-adjacent equities
    "MSTR": "#FF6B00", "COIN": "#0052FF", "HOOD": "#00C805",
    # Indices/ETFs
    "XYZ100": "#0066CC", "SP500":  "#FF6600", "EWY":    "#9933CC",
    # Commodities
    "GOLD":   "#FFD700", "SILVER": "#C0C0C0", "CL":     "#404040",
    # Forex
    "EUR":    "#003399", "JPY":    "#BC002D",
    # Pre-IPO
    "ANTHROPIC": "#D4A571", "SPACEX": "#005288", "OPENAI": "#10A37F",
    # Thematic indices
    "MAG7":     "#FF6B6B", "SEMIS": "#4ECDC4", "INFOTECH": "#95E1D3",
}

STRATEGY_COLOR = {
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
STRATEGIES_ORDER = ["NAIVE_MARKET", "MARKET_CAP5", "TWAP_5MIN",
                     "TWAP_30MIN", "VWAP_30MIN"]

COMPONENT_COLOR = {
    "spread":      "#4C72B0",
    "impact":      "#DD8452",
    "fee":         "#55A868",
    "opportunity": "#C44E52",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def fmt_size(usd: float) -> str:
    if usd >= 1_000_000:
        v = usd / 1_000_000
        return f"${v:.0f}M" if v == int(v) else f"${v:.1f}M"
    if usd >= 1_000:
        return f"${usd/1000:.0f}k"
    return f"${usd:.0f}"


def coin_to_class(coin: str) -> str:
    for cls, coins in ASSET_CLASSES.items():
        if coin in coins:
            return cls
    return "other"


def assign_classes(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["asset_class"] = df["coin"].apply(coin_to_class)
    return df


def filter_class(df: pd.DataFrame, asset_class: str) -> pd.DataFrame:
    return df[df["asset_class"] == asset_class]


def display(cls: str) -> str:
    """LaTeX-style label rendered for matplotlib (no escape chars)."""
    return CLASS_DISPLAY.get(cls, cls).replace("\\&", "&").replace("\\%", "%")


# ---------------------------------------------------------------------------
# Per-asset-class plots
# ---------------------------------------------------------------------------


def plot_is_boxplot(sub: pd.DataFrame, asset_class: str, out_dir: Path):
    coins = sorted(sub["coin"].unique())
    if not coins:
        return
    n = len(coins)
    fig, axes = plt.subplots(1, n, figsize=(4.5*n, 5), sharey=False)
    if n == 1:
        axes = [axes]
    for ax, coin in zip(axes, coins):
        c_sub = sub[(sub["coin"] == coin) & (sub["side"] == "BUY")]
        sizes = sorted(c_sub["order_size_usd"].unique())
        x_positions = np.arange(len(sizes))
        width = 0.16
        for i, strat in enumerate(STRATEGIES_ORDER):
            data = [
                c_sub[(c_sub["order_size_usd"] == sz) &
                       (c_sub["strategy"] == strat)][
                    "total_is_baseline_bps"].clip(lower=-50, upper=300).values
                for sz in sizes
            ]
            offset = (i - len(STRATEGIES_ORDER)/2 + 0.5) * width
            bp = ax.boxplot(
                data, positions=x_positions + offset, widths=width*0.9,
                patch_artist=True, showfliers=False,
                medianprops=dict(color="white", linewidth=1.2),
            )
            for patch in bp["boxes"]:
                patch.set_facecolor(STRATEGY_COLOR.get(strat, "#888"))
                patch.set_alpha(0.85)
        ax.set_xticks(x_positions)
        ax.set_xticklabels([fmt_size(s) for s in sizes])
        ax.set_xlabel("Order size")
        ax.set_ylabel("Total IS (bps)")
        ax.set_title(coin)
        ax.grid(True, alpha=0.3, axis="y")
        ax.axhline(0, color="black", linewidth=0.5)

    handles = [plt.Rectangle((0, 0), 1, 1,
                              facecolor=STRATEGY_COLOR.get(s, "#888"),
                              alpha=0.85) for s in STRATEGIES_ORDER]
    fig.legend(handles,
                [STRATEGY_LABEL[s].replace("\\%", "%") for s in STRATEGIES_ORDER],
                loc="lower center", ncol=5, bbox_to_anchor=(0.5, -0.02))
    fig.suptitle(f"IS distribution — {display(asset_class)}", fontsize=12)
    plt.tight_layout(rect=(0, 0.05, 1, 0.96))
    plt.savefig(out_dir / "01_is_boxplot.png", dpi=140, bbox_inches="tight")
    plt.close()


def plot_decomposition(sub: pd.DataFrame, asset_class: str, out_dir: Path):
    coins = sorted(sub["coin"].unique())
    if not coins:
        return
    n = len(coins)
    fig, axes = plt.subplots(1, n, figsize=(4.5*n, 5), sharey=False)
    if n == 1:
        axes = [axes]
    strat = "TWAP_30MIN"
    for ax, coin in zip(axes, coins):
        c_sub = sub[(sub["coin"] == coin) & (sub["side"] == "BUY") &
                     (sub["strategy"] == strat)]
        if len(c_sub) == 0:
            ax.set_visible(False)
            continue
        agg = (c_sub.groupby("order_size_usd")
                    [["spread_bps", "impact_bps", "fee_base_bps",
                      "opportunity_bps"]]
                    .mean().sort_index())
        sizes = agg.index.values
        x = np.arange(len(sizes))
        labels = [fmt_size(s) for s in sizes]
        bottom = np.zeros(len(sizes))
        for col, label, color in [
            ("spread_bps",      "Spread",      COMPONENT_COLOR["spread"]),
            ("impact_bps",      "Impact",      COMPONENT_COLOR["impact"]),
            ("fee_base_bps",    "Fee",         COMPONENT_COLOR["fee"]),
            ("opportunity_bps", "Opportunity", COMPONENT_COLOR["opportunity"]),
        ]:
            vals = agg[col].values
            ax.bar(x, vals, bottom=bottom, label=label, color=color, alpha=0.9)
            bottom = bottom + vals
        ax.set_xticks(x)
        ax.set_xticklabels(labels)
        ax.set_xlabel("Order size")
        ax.set_ylabel("Mean IS (bps)")
        ax.set_title(coin)
        ax.grid(True, alpha=0.3, axis="y")
        if ax is axes[-1]:
            ax.legend(loc="upper left", fontsize=9)
    fig.suptitle(f"IS decomposition — {display(asset_class)} (BUY, TWAP-30min)",
                  fontsize=12)
    plt.tight_layout(rect=(0, 0, 1, 0.96))
    plt.savefig(out_dir / "02_decomposition.png", dpi=140, bbox_inches="tight")
    plt.close()


def plot_strategy_comparison(sub: pd.DataFrame, asset_class: str,
                              out_dir: Path):
    coins = sorted(sub["coin"].unique())
    if not coins:
        return
    n = len(coins)
    fig, axes = plt.subplots(n, 1, figsize=(11, 4*n), sharex=False)
    if n == 1:
        axes = [axes]
    for ax, coin in zip(axes, coins):
        c_sub = sub[(sub["coin"] == coin) & (sub["side"] == "BUY")]
        sizes = sorted(c_sub["order_size_usd"].unique())
        x = np.arange(len(sizes))
        width = 0.16
        for i, strat in enumerate(STRATEGIES_ORDER):
            vals = [
                c_sub[(c_sub["order_size_usd"] == sz) &
                       (c_sub["strategy"] == strat)][
                    "total_is_baseline_bps"].median()
                for sz in sizes
            ]
            ax.bar(x + (i - 2)*width, vals, width=width*0.95,
                    label=STRATEGY_LABEL[strat].replace("\\%", "%"),
                    color=STRATEGY_COLOR.get(strat, "#888"), alpha=0.9)
        ax.set_xticks(x)
        ax.set_xticklabels([fmt_size(s) for s in sizes])
        ax.set_xlabel("Order size")
        ax.set_ylabel("Median total IS (bps)")
        ax.set_title(coin)
        ax.grid(True, alpha=0.3, axis="y")
        ax.legend(fontsize=8, ncol=5, loc="upper left")
    fig.suptitle(f"Strategy comparison — {display(asset_class)} (BUY)", fontsize=12)
    plt.tight_layout(rect=(0, 0, 1, 0.97))
    plt.savefig(out_dir / "03_strategy_comparison.png", dpi=140,
                 bbox_inches="tight")
    plt.close()


def plot_size_scaling(sub: pd.DataFrame, asset_class: str, out_dir: Path):
    coins = sorted(sub["coin"].unique())
    if not coins:
        return
    fig, ax = plt.subplots(figsize=(8, 5.5))
    strat = "TWAP_30MIN"
    s_sub = sub[(sub["strategy"] == strat) & (sub["side"] == "BUY")]
    for coin in coins:
        c_sub = s_sub[s_sub["coin"] == coin]
        if len(c_sub) == 0:
            continue
        med = c_sub.groupby("order_size_usd")["total_is_baseline_bps"].median()
        ax.plot(med.index, med.values, marker="o", linewidth=2, markersize=7,
                color=COIN_COLOR.get(coin, "#888"), label=coin)
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("Order size (USD, log)")
    ax.set_ylabel("Median total IS (bps, log)")
    ax.set_title(f"Size scaling — {display(asset_class)} (TWAP-30min BUY)")
    ax.grid(True, alpha=0.3, which="both")
    ax.legend()
    plt.tight_layout()
    plt.savefig(out_dir / "04_size_scaling.png", dpi=140, bbox_inches="tight")
    plt.close()


def plot_completion(sub: pd.DataFrame, asset_class: str, out_dir: Path):
    coins = sorted(sub["coin"].unique())
    if not coins:
        return
    n = len(coins)
    fig, axes = plt.subplots(1, n, figsize=(4.5*n, 4.5), sharey=True)
    if n == 1:
        axes = [axes]
    strat = "NAIVE_MARKET"
    for ax, coin in zip(axes, coins):
        c_sub = sub[(sub["coin"] == coin) & (sub["side"] == "BUY") &
                     (sub["strategy"] == strat)]
        sizes = sorted(c_sub["order_size_usd"].unique())
        data = [c_sub[c_sub["order_size_usd"] == sz]["fill_completion_pct"].values
                 for sz in sizes]
        bp = ax.boxplot(data, tick_labels=[fmt_size(s) for s in sizes],
                          patch_artist=True, showfliers=False)
        for patch in bp["boxes"]:
            patch.set_facecolor(COIN_COLOR.get(coin, "#888"))
            patch.set_alpha(0.85)
        ax.set_xlabel("Order size")
        ax.set_ylabel("Fill completion (%)")
        ax.set_title(coin)
        ax.grid(True, alpha=0.3, axis="y")
        ax.set_ylim(0, 105)
    fig.suptitle(f"Fill completion — {display(asset_class)} (Naive Market BUY)",
                  fontsize=12)
    plt.tight_layout(rect=(0, 0, 1, 0.96))
    plt.savefig(out_dir / "05_completion.png", dpi=140, bbox_inches="tight")
    plt.close()


# ---------------------------------------------------------------------------
# Cross-asset-class master plots
# ---------------------------------------------------------------------------


def _mid_grid_size_for_class(sub_class: pd.DataFrame) -> float:
    sizes = sorted(sub_class["order_size_usd"].unique())
    if len(sizes) >= 3:
        return sizes[len(sizes)//2]
    if sizes:
        return sizes[0]
    return 0


def master_is_by_class(df: pd.DataFrame, out_dir: Path):
    fig, ax = plt.subplots(figsize=(12, 6))
    sub = df[(df["strategy"] == "TWAP_30MIN") & (df["side"] == "BUY")]
    classes = [c for c in CLASS_ORDER if c in sub["asset_class"].values]
    data, labels, colors = [], [], []
    for cls in classes:
        c_sub = sub[sub["asset_class"] == cls]
        mid_size = _mid_grid_size_for_class(c_sub)
        vals = c_sub[c_sub["order_size_usd"] == mid_size][
            "total_is_baseline_bps"].clip(lower=-20, upper=150).values
        if len(vals) == 0:
            continue
        data.append(vals)
        labels.append(f"{display(cls)}\n({fmt_size(mid_size)})")
        colors.append(CLASS_COLOR.get(cls, "#888"))

    bp = ax.boxplot(data, tick_labels=labels, patch_artist=True,
                      showfliers=False)
    for patch, color in zip(bp["boxes"], colors):
        patch.set_facecolor(color)
        patch.set_alpha(0.85)
    ax.set_ylabel("Total IS (bps)")
    ax.set_title("Cross-asset-class IS comparison "
                  "(TWAP-30min BUY, mid-grid order size)")
    ax.grid(True, alpha=0.3, axis="y")
    ax.axhline(0, color="black", linewidth=0.5)
    plt.xticks(rotation=25, ha="right")
    plt.tight_layout()
    plt.savefig(out_dir / "00_master_is_by_class.png", dpi=140,
                  bbox_inches="tight")
    plt.close()


def master_decomposition(df: pd.DataFrame, out_dir: Path):
    sub = df[(df["strategy"] == "TWAP_30MIN") & (df["side"] == "BUY")]
    classes = [c for c in CLASS_ORDER if c in sub["asset_class"].values]
    agg = (sub.groupby("asset_class")
              [["spread_bps", "impact_bps", "fee_base_bps", "opportunity_bps"]]
              .mean())
    agg = agg.reindex(classes)

    fig, ax = plt.subplots(figsize=(13, 6))
    x = np.arange(len(classes))
    labels = [display(c) for c in classes]
    bottom = np.zeros(len(classes))
    for col, label, color in [
        ("spread_bps",      "Spread",      COMPONENT_COLOR["spread"]),
        ("impact_bps",      "Impact",      COMPONENT_COLOR["impact"]),
        ("fee_base_bps",    "Fee",         COMPONENT_COLOR["fee"]),
        ("opportunity_bps", "Opportunity", COMPONENT_COLOR["opportunity"]),
    ]:
        vals = agg[col].values
        ax.bar(x, vals, bottom=bottom, label=label, color=color, alpha=0.9)
        bottom = bottom + vals
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=25, ha="right")
    ax.set_ylabel("Mean IS (bps)")
    ax.set_title("Cross-asset-class IS decomposition (TWAP-30min BUY, all sizes)")
    ax.grid(True, alpha=0.3, axis="y")
    ax.legend(loc="upper left")
    plt.tight_layout()
    plt.savefig(out_dir / "00_master_decomposition.png", dpi=140,
                  bbox_inches="tight")
    plt.close()


def master_fee_sensitivity(df: pd.DataFrame, out_dir: Path):
    fee_cols = [c for c in df.columns if c.startswith("fee_builder_")]
    fee_levels = sorted([int(c.replace("fee_builder_", "").replace("bp", ""))
                          for c in fee_cols])
    fig, ax = plt.subplots(figsize=(10, 6))
    sub = df[(df["strategy"] == "TWAP_30MIN") & (df["side"] == "BUY")]
    for cls in CLASS_ORDER:
        c_sub = sub[sub["asset_class"] == cls]
        if len(c_sub) == 0:
            continue
        mid_size = _mid_grid_size_for_class(c_sub)
        c_sub_top = c_sub[c_sub["order_size_usd"] == mid_size]
        base = c_sub_top[["spread_bps", "impact_bps", "opportunity_bps"]].sum(axis=1)
        meds = []
        for bf in fee_levels:
            total = base + c_sub_top[f"fee_builder_{bf}bp"]
            meds.append(total.median())
        ax.plot(fee_levels, meds, marker="o", linewidth=2,
                color=CLASS_COLOR.get(cls, "#888"),
                label=f"{display(cls)} ({fmt_size(mid_size)})")
    ax.set_xlabel("Builder fee (bps)")
    ax.set_ylabel("Median total IS (bps)")
    ax.set_title("Fee sensitivity by asset class — TWAP-30min BUY")
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=9, loc="upper left")
    plt.tight_layout()
    plt.savefig(out_dir / "00_master_fee_sensitivity.png", dpi=140,
                  bbox_inches="tight")
    plt.close()


def master_completion(df: pd.DataFrame, out_dir: Path):
    sub = df[(df["strategy"] == "NAIVE_MARKET") & (df["side"] == "BUY")]
    classes = [c for c in CLASS_ORDER if c in sub["asset_class"].values]
    data, labels, colors = [], [], []
    for cls in classes:
        c_sub = sub[sub["asset_class"] == cls]
        max_size = c_sub["order_size_usd"].max()
        vals = c_sub[c_sub["order_size_usd"] == max_size][
            "fill_completion_pct"].values
        if len(vals) == 0:
            continue
        data.append(vals)
        labels.append(f"{display(cls)}\n({fmt_size(max_size)})")
        colors.append(CLASS_COLOR.get(cls, "#888"))

    fig, ax = plt.subplots(figsize=(12, 6))
    bp = ax.boxplot(data, tick_labels=labels, patch_artist=True,
                      showfliers=False)
    for patch, color in zip(bp["boxes"], colors):
        patch.set_facecolor(color)
        patch.set_alpha(0.85)
    ax.set_ylabel("Fill completion (%)")
    ax.set_title("Fill completion at top-of-grid order size "
                  "by asset class (Naive Market BUY)")
    ax.grid(True, alpha=0.3, axis="y")
    ax.set_ylim(0, 105)
    plt.xticks(rotation=25, ha="right")
    plt.tight_layout()
    plt.savefig(out_dir / "00_master_completion.png", dpi=140,
                  bbox_inches="tight")
    plt.close()


# ---------------------------------------------------------------------------
# LaTeX tables
# ---------------------------------------------------------------------------


def write_class_summary_table(sub: pd.DataFrame, asset_class: str,
                                out_dir: Path):
    s = sub[(sub["strategy"] == "TWAP_30MIN") & (sub["side"] == "BUY")]
    if len(s) == 0:
        return
    pivot = (s.pivot_table(index="coin", columns="order_size_usd",
                            values="total_is_baseline_bps", aggfunc="median")
                .sort_index())
    sizes = sorted(pivot.columns)
    col_spec = "l" + "r" * len(sizes)
    cap_class = CLASS_DISPLAY.get(asset_class, asset_class)
    lines = [
        r"\begin{table}[h]", r"\centering",
        rf"\caption{{Median total IS (bps) for {cap_class}, "
        r"TWAP-30min BUY, baseline fees.}}",
        rf"\label{{tab:is_{asset_class}}}",
        rf"\begin{{tabular}}{{{col_spec}}}",
        r"\toprule",
        "Coin & " + " & ".join(fmt_size(sz) for sz in sizes) + r" \\",
        r"\midrule",
    ]
    for coin in pivot.index:
        row = pivot.loc[coin]
        vals = " & ".join(
            f"{row[s]:.2f}" if pd.notna(row[s]) else "—" for s in sizes
        )
        lines.append(f"{coin} & {vals} \\\\")
    lines += [r"\bottomrule", r"\end{tabular}", r"\end{table}"]
    (out_dir / f"by_class_{asset_class}.tex").write_text("\n".join(lines),
                                                            encoding="utf-8")


def write_master_table(df: pd.DataFrame, out_dir: Path):
    sub = df[(df["strategy"] == "TWAP_30MIN") & (df["side"] == "BUY")]
    rows = []
    for cls in CLASS_ORDER:
        c_sub = sub[sub["asset_class"] == cls]
        if len(c_sub) == 0:
            continue
        rows.append({
            "class": cls,
            "n_coins":   c_sub["coin"].nunique(),
            "n_obs":     len(c_sub),
            "median_is": float(c_sub["total_is_baseline_bps"].median()),
            "spread":    float(c_sub["spread_bps"].mean()),
            "impact":    float(c_sub["impact_bps"].mean()),
            "fee":       float(c_sub["fee_base_bps"].mean()),
            "opp":       float(c_sub["opportunity_bps"].mean()),
        })
    if not rows:
        return
    lines = [
        r"\begin{table}[h]", r"\centering",
        r"\caption{Cross-asset-class IS comparison. TWAP-30min BUY orders. "
        r"Median total IS in bps; component means in bps.}",
        r"\label{tab:master_cross_class}",
        r"\begin{tabular}{lrrrrrrr}",
        r"\toprule",
        r"Asset class & N coins & N obs & Median IS & Spread & Impact "
        r"& Fee & Opp. \\",
        r" & & & (bps) & (bps) & (bps) & (bps) & (bps) \\",
        r"\midrule",
    ]
    for r in rows:
        cls_label = CLASS_DISPLAY.get(r["class"], r["class"])
        lines.append(
            f"{cls_label} & {r['n_coins']} & {r['n_obs']:,} & "
            f"{r['median_is']:.2f} & {r['spread']:.2f} & {r['impact']:.2f} & "
            f"{r['fee']:.2f} & {r['opp']:.2f} \\\\"
        )
    lines += [r"\bottomrule", r"\end{tabular}", r"\end{table}"]
    (out_dir / "master_cross_class.tex").write_text("\n".join(lines),
                                                         encoding="utf-8")


def write_strategy_winners_table(df: pd.DataFrame, out_dir: Path):
    sub = df[df["side"] == "BUY"]
    medians = (sub.groupby(["asset_class", "coin", "order_size_usd", "strategy"])
                  ["total_is_baseline_bps"].median().reset_index())
    winners_per_coin = medians.loc[
        medians.groupby(["asset_class", "coin", "order_size_usd"])
        ["total_is_baseline_bps"].idxmin()
    ]
    win_strat = (winners_per_coin.groupby(["asset_class", "order_size_usd"])
                 ["strategy"].agg(lambda x: x.mode()[0] if len(x) > 0 else None)
                 .reset_index())
    lines = [
        r"\begin{table}[h]", r"\centering",
        r"\caption{Most-frequently-winning execution strategy "
        r"per asset class × order size, BUY side.}",
        r"\label{tab:strategy_winners_by_class}",
        r"\begin{tabular}{llrl}", r"\toprule",
        r"Asset class & & Order size & Best strategy \\",
        r"\midrule",
    ]
    last_class = None
    for _, row in win_strat.iterrows():
        cls = row["asset_class"]
        cls_label = CLASS_DISPLAY.get(cls, cls) if cls != last_class else ""
        last_class = cls
        lines.append(
            f"{cls_label} & & {fmt_size(row['order_size_usd'])} & "
            f"{STRATEGY_LABEL.get(row['strategy'], row['strategy'])} \\\\"
        )
    lines += [r"\bottomrule", r"\end{tabular}", r"\end{table}"]
    (out_dir / "strategy_winners_by_class.tex").write_text("\n".join(lines),
                                                              encoding="utf-8")


# ---------------------------------------------------------------------------
# Summary CSV
# ---------------------------------------------------------------------------


def write_summary_csv(df: pd.DataFrame, out_path: Path):
    summary = (df.groupby(
        ["asset_class", "coin", "side", "strategy", "order_size_usd"])
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
    summary.to_csv(out_path, index=False, float_format="%.4f")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--data-dir",
                   default=r"C:\Users\grego\Downloads\fetcher\data")
    args = p.parse_args()

    data_root = Path(args.data_dir)
    is_path = data_root / "is_results" / "is_decomp.parquet"
    out_dir = data_root / "aggregator_by_class"
    plots_dir = out_dir / "plots"
    tables_dir = out_dir / "tables"
    plots_dir.mkdir(parents=True, exist_ok=True)
    tables_dir.mkdir(parents=True, exist_ok=True)

    if not is_path.exists():
        log.error("IS results not found: %s", is_path)
        sys.exit(2)

    log.info("=" * 60)
    log.info("Aggregator by Asset Class — Modul 6.5 (v2)")
    log.info("=" * 60)

    log.info("Loading IS results ...")
    t0 = time.perf_counter()
    df = pq.read_table(is_path).to_pandas()
    log.info("  %s rows loaded in %.1fs", f"{len(df):,}",
             time.perf_counter() - t0)

    df = df.dropna(subset=["total_is_baseline_bps", "arrival_mid"])
    df = assign_classes(df)

    detected = df["asset_class"].value_counts()
    log.info("\nAsset classes detected:")
    for cls, count in detected.items():
        coins = sorted(df[df["asset_class"] == cls]["coin"].unique())
        log.info("  %-20s %s rows  (%s)", cls, f"{count:,}", ", ".join(coins))

    plt.style.use("default")
    plt.rcParams.update({"figure.dpi": 110, "font.size": 10,
                          "axes.grid": True, "grid.alpha": 0.3})

    log.info("\nGenerating master (cross-class) plots ...")
    master_is_by_class(df, plots_dir)
    log.info("  00_master_is_by_class.png")
    master_decomposition(df, plots_dir)
    log.info("  00_master_decomposition.png")
    master_fee_sensitivity(df, plots_dir)
    log.info("  00_master_fee_sensitivity.png")
    master_completion(df, plots_dir)
    log.info("  00_master_completion.png")

    for cls in CLASS_ORDER:
        sub = filter_class(df, cls)
        if len(sub) == 0:
            log.info("Skipping %s — no data", cls)
            continue
        log.info("\nGenerating plots for %s ...", cls)
        class_dir = plots_dir / cls
        class_dir.mkdir(parents=True, exist_ok=True)
        plot_is_boxplot(sub, cls, class_dir)
        plot_decomposition(sub, cls, class_dir)
        plot_strategy_comparison(sub, cls, class_dir)
        plot_size_scaling(sub, cls, class_dir)
        plot_completion(sub, cls, class_dir)
        log.info("  5 plots in %s/", class_dir.name)

    log.info("\nGenerating LaTeX tables ...")
    write_master_table(df, tables_dir)
    log.info("  master_cross_class.tex")
    for cls in CLASS_ORDER:
        sub = filter_class(df, cls)
        if len(sub) == 0:
            continue
        write_class_summary_table(sub, cls, tables_dir)
        log.info("  by_class_%s.tex", cls)
    write_strategy_winners_table(df, tables_dir)
    log.info("  strategy_winners_by_class.tex")

    log.info("\nWriting summary CSV ...")
    write_summary_csv(df, out_dir / "summary_by_class.csv")
    log.info("  summary_by_class.csv")

    log.info("\n" + "=" * 60)
    log.info("DONE in %.1fs", time.perf_counter() - t0)


if __name__ == "__main__":
    main()
