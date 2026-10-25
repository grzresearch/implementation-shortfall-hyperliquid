"""
Phase 2 v2 — HIP-3 Markets Fetcher (Tier 1 + Tier 2 = 20 markets)
==================================================================

Tier 1 (core paper, sections 5.2–5.6):
  Equities Mega-Cap (3)   : NVDA, TSLA, AAPL
  Indices/ETFs (3)        : XYZ100, SP500, EWY
  Commodities (3)         : GOLD, SILVER, CL  (WTI Crude)
  Forex (2)               : EUR, JPY
  Pre-IPO Ventuals (3)    : ANTHROPIC, SPACEX, OPENAI

Tier 2 (bonus, section 5.8 / appendix):
  Crypto-adjacent (3)     : MSTR, COIN, HOOD
  Thematic Indices (3)    : MAG7, SEMIS, INFOTECH

Per-ticker launch dates discovered via discover_hip3.py v3 (full S3 listing).
Each market fetches from its exact launch date — no wasted 404 probes.

Uses the existing ob_fetcher pipeline by calling run_pipeline() once per
ticker. Fills are disk-cached per (deployer, date) so subsequent markets
on the same deployer reuse cached fills.

Usage:
    python fetch_hip3.py                       # Tier 1 + 2 (all 20)
    python fetch_hip3.py --tier 1              # only Tier 1 (14)
    python fetch_hip3.py --tier 2              # only Tier 2 (6)
    python fetch_hip3.py --deployer xyz        # only Trade.xyz
    python fetch_hip3.py --deployer vntl       # only Ventuals
    python fetch_hip3.py --markets NVDA,TSLA   # specific tickers
    python fetch_hip3.py --dry-run             # plan without download
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

from ob_fetcher import (
    FetchConfig, AssetSpec, SamplingConfig, StorageConfig,
    ReservoirConfig, run_pipeline,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("fetch_hip3")


# ===========================================================================
# Tier 1 + 2 markets with verified launch dates
# (discovered via discover_hip3.py — May 15, 2026)
# ===========================================================================

# Format: (coin, dex, launch_date_iso, asset_group, tier)

MARKETS: list[tuple[str, str, str, str, int]] = [
    # ===== TIER 1 (core paper, ≥90 days in sample window) =====

    # ---- Trade.xyz (xyz) — EXISTING, kept unchanged ----
    ("NVDA",      "xyz",  "2025-12-15", "equities_megacap",      1),
    ("TSLA",      "xyz",  "2025-12-15", "equities_megacap",      1),
    ("AAPL",      "xyz",  "2025-12-15", "equities_megacap",      1),
    ("XYZ100",    "xyz",  "2025-12-15", "indices_etf",           1),
    ("SP500",     "xyz",  "2026-03-18", "indices_etf",           1),
    ("EWY",       "xyz",  "2026-03-02", "indices_etf",           1),
    ("GOLD",      "xyz",  "2025-12-22", "commodities",           1),
    ("SILVER",    "xyz",  "2025-12-26", "commodities",           1),
    ("CL",        "xyz",  "2026-01-06", "commodities",           1),
    ("EUR",       "xyz",  "2025-12-23", "forex",                 1),
    ("JPY",       "xyz",  "2025-12-23", "forex",                 1),

    # ---- Ventuals (vntl) — EXISTING ----
    ("ANTHROPIC", "vntl", "2025-12-15", "pre_ipo",               1),
    ("SPACEX",    "vntl", "2025-12-15", "pre_ipo",               1),
    ("OPENAI",    "vntl", "2025-12-15", "pre_ipo",               1),

    # ---- Felix Exchange (flx) — NEW Tier 1 ----
    ("TSLA",      "flx",  "2025-12-15", "equities_megacap",      1),
    ("NVDA",      "flx",  "2026-01-21", "equities_megacap",      1),
    ("COIN",      "flx",  "2025-12-15", "equities_crypto",       1),
    ("CRCL",      "flx",  "2025-12-15", "equities_crypto",       1),
    ("GOLD",      "flx",  "2025-12-15", "commodities",           1),
    ("SILVER",    "flx",  "2025-12-17", "commodities",           1),
    ("COPPER",    "flx",  "2026-01-20", "commodities",           1),
    ("PLATINUM",  "flx",  "2026-01-27", "commodities",           1),
    ("PALLADIUM", "flx",  "2026-01-28", "commodities",           1),
    ("OIL",       "flx",  "2026-01-09", "commodities",           1),
    ("XMR",       "flx",  "2025-12-16", "crypto_altcoins",       1),

    # ---- HyENA (hyna) — NEW Tier 1 ----
    # Crypto majors (cross-listed with native HL — fee delta experiment)
    ("BTC",       "hyna", "2025-12-15", "crypto_native_hip3",    1),
    ("ETH",       "hyna", "2025-12-15", "crypto_native_hip3",    1),
    ("HYPE",      "hyna", "2025-12-15", "crypto_native_hip3",    1),
    ("SOL",       "hyna", "2025-12-15", "crypto_native_hip3",    1),
    # Crypto altcoins
    ("XRP",       "hyna", "2026-01-05", "crypto_altcoins",       1),
    ("BNB",       "hyna", "2026-01-07", "crypto_altcoins",       1),
    ("DOGE",      "hyna", "2026-01-09", "crypto_altcoins",       1),
    ("SUI",       "hyna", "2026-01-12", "crypto_altcoins",       1),
    ("LINK",      "hyna", "2026-01-20", "crypto_altcoins",       1),
    ("LTC",       "hyna", "2026-01-20", "crypto_altcoins",       1),
    ("BCH",       "hyna", "2026-01-20", "crypto_altcoins",       1),
    ("ADA",       "hyna", "2026-01-20", "crypto_altcoins",       1),
    ("XMR",       "hyna", "2026-01-20", "crypto_altcoins",       1),
    ("ZEC",       "hyna", "2025-12-15", "crypto_altcoins",       1),
    # Crypto memes
    ("FARTCOIN",  "hyna", "2026-01-14", "crypto_memes",          1),
    ("PUMP",      "hyna", "2026-01-15", "crypto_memes",          1),
    ("1000PEPE",  "hyna", "2026-02-11", "crypto_memes",          1),
    # Crypto protocol/yield tokens
    ("LIGHTER",   "hyna", "2025-12-22", "crypto_protocol",       1),
    ("ENA",       "hyna", "2026-01-19", "crypto_protocol",       1),
    ("IP",        "hyna", "2026-01-20", "crypto_protocol",       1),
    ("XPL",       "hyna", "2026-01-20", "crypto_protocol",       1),

    # ---- Markets by Kinetiq (km) — NEW Tier 1 ----
    ("TSLA",      "km",   "2026-01-16", "equities_megacap",      1),
    ("NVDA",      "km",   "2026-02-11", "equities_megacap",      1),
    ("AAPL",      "km",   "2026-01-29", "equities_megacap",      1),
    ("GOOGL",     "km",   "2026-01-29", "equities_megacap",      1),
    ("BABA",      "km",   "2026-01-15", "equities_megacap",      1),
    ("US500",     "km",   "2026-01-12", "indices_etf",           1),
    ("USTECH",    "km",   "2026-01-13", "indices_etf",           1),
    ("SMALL2000", "km",   "2026-01-14", "indices_etf",           1),
    ("USOIL",     "km",   "2026-01-22", "commodities",           1),
    ("GOLD",      "km",   "2026-01-28", "commodities",           1),
    ("SILVER",    "km",   "2026-01-28", "commodities",           1),
    ("USBOND",    "km",   "2026-01-21", "fixed_income",          1),
    ("USENERGY",  "km",   "2026-02-04", "thematic_indices",      1),

    # ---- dreamcash (cash) — NEW Tier 1 ----
    ("TSLA",      "cash", "2026-01-22", "equities_megacap",      1),
    ("NVDA",      "cash", "2026-01-23", "equities_megacap",      1),
    ("INTC",      "cash", "2026-01-29", "equities_megacap",      1),
    ("AMZN",      "cash", "2026-01-27", "equities_megacap",      1),
    ("META",      "cash", "2026-01-28", "equities_megacap",      1),
    ("GOOGL",     "cash", "2026-01-27", "equities_megacap",      1),
    ("MSFT",      "cash", "2026-01-29", "equities_megacap",      1),
    ("HOOD",      "cash", "2026-01-28", "equities_crypto",       1),
    ("USA500",    "cash", "2026-01-20", "indices_etf",           1),
    ("GOLD",      "cash", "2026-02-05", "commodities",           1),
    ("SILVER",    "cash", "2026-02-06", "commodities",           1),

    # ===== TIER 2 (robustness, 30-89 days) =====

    # ---- Trade.xyz Tier 2 (existing) ----
    ("MSTR",      "xyz",  "2025-12-15", "equities_crypto",       2),
    ("COIN",      "xyz",  "2025-12-15", "equities_crypto",       2),
    ("HOOD",      "xyz",  "2025-12-15", "equities_crypto",       2),

    # ---- Ventuals Tier 2 (existing) ----
    ("MAG7",      "vntl", "2025-12-15", "thematic_indices",      2),
    ("SEMIS",     "vntl", "2026-01-06", "thematic_indices",      2),
    ("INFOTECH",  "vntl", "2026-01-08", "thematic_indices",      2),

    # ---- Felix Tier 2 (new) ----
    ("USDE",      "flx",  "2026-02-19", "crypto_protocol",       2),
    ("USA500",    "flx",  "2026-03-10", "indices_etf",           2),
    ("USA100",    "flx",  "2026-03-30", "indices_etf",           2),
    ("GAS",       "flx",  "2026-03-31", "commodities",           2),

    # ---- HyENA Tier 2 (new) ----
    ("BASED",     "hyna", "2026-04-02", "crypto_memes",          2),

    # ---- Markets by Kinetiq Tier 2 (new) ----
    ("MU",        "km",   "2026-02-24", "equities_megacap",      2),
    ("PLTR",      "km",   "2026-02-24", "equities_megacap",      2),
    ("RTX",       "km",   "2026-03-10", "equities_megacap",      2),
    ("BMNR",      "km",   "2026-03-12", "equities_megacap",      2),
    ("XIAOMI",    "km",   "2026-04-09", "equities_megacap",      2),
    ("TENCENT",   "km",   "2026-04-09", "equities_megacap",      2),
    ("SEMI",      "km",   "2026-02-26", "thematic_indices",      2),
    ("GLDMINE",   "km",   "2026-02-26", "thematic_indices",      2),

    # ---- dreamcash Tier 2 (new) ----
    ("EWY",       "cash", "2026-03-02", "indices_etf",           2),
    ("KWEB",      "cash", "2026-03-18", "indices_etf",           2),
    ("WTI",       "cash", "2026-03-11", "commodities",           2),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def parse_iso_date(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def filter_markets(tier: int | None, deployer: str | None,
                    markets: list[str] | None
                    ) -> list[tuple[str, str, str, str, int]]:
    out = MARKETS
    if tier is not None:
        out = [m for m in out if m[4] == tier]
    if deployer is not None:
        out = [m for m in out if m[1] == deployer]
    if markets is not None:
        wanted = {m.upper() for m in markets}
        out = [m for m in out if m[0].upper() in wanted]
    return out


def fetch_one_market(coin: str, dex: str, launch_date_str: str,
                      end_date: datetime, data_dir: Path,
                      dry_run: bool) -> dict:
    """Fetch ONE market using its own launch date as the start."""
    start = parse_iso_date(launch_date_str)
    launch_d = date.fromisoformat(launch_date_str)

    cfg = FetchConfig(
        assets=[AssetSpec(coin=coin, dex=dex, asset_class="perps")],
        sampling=SamplingConfig(
            start=start,
            end=end_date,
            keep_every_n_minutes=15,
            trade_window_minutes_before=5,
            trade_window_minutes_after=30,
        ),
        storage=StorageConfig(root=data_dir),
        reservoir=ReservoirConfig(
            # Per-market start avoids probing dates before launch
            data_start_date=launch_d,
        ),
        max_concurrent_downloads=8,
        fetch_fills=True,
        verbose=True,
        dry_run=dry_run,
    )
    summaries = run_pipeline(cfg)
    return summaries[0] if summaries else {}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    p = argparse.ArgumentParser(description="HIP-3 Markets Fetcher v2")
    p.add_argument("--data-dir",
                   default=str(Path(__file__).resolve().parents[1] / "data"),
                   help="Root data directory (same as Phase 1)")
    p.add_argument("--tier", type=int, choices=[1, 2], default=None,
                   help="Only one tier (default: both)")
    p.add_argument("--deployer", choices=["xyz", "vntl", "flx", "hyna", "km", "cash"], default=None,
                   help="Only one deployer")
    p.add_argument("--markets", type=str, default=None,
                   help="Comma-separated ticker list (e.g. NVDA,TSLA)")
    p.add_argument("--end", default="2026-05-12",
                   help="End date YYYY-MM-DD")
    p.add_argument("--dry-run", action="store_true",
                   help="Plan without downloading")
    args = p.parse_args()

    markets_list = None
    if args.markets:
        markets_list = [m.strip().upper() for m in args.markets.split(",")]

    selected = filter_markets(args.tier, args.deployer, markets_list)
    end_dt = parse_iso_date(args.end)

    log.info("=" * 70)
    log.info("HIP-3 FETCHER v2")
    log.info("=" * 70)
    log.info("Selected markets: %d", len(selected))
    log.info("End date:         %s", args.end)
    log.info("Data dir:         %s", args.data_dir)
    log.info("Dry run:          %s", args.dry_run)
    log.info("")
    log.info("%-12s %-6s %-12s %-25s %-5s",
             "Coin", "Dex", "Launch", "Asset group", "Tier")
    log.info("%s", "-" * 70)
    for coin, dex, launch, group, tier in selected:
        days = (date.fromisoformat(args.end) - date.fromisoformat(launch)).days
        log.info("%-12s %-6s %-12s %-25s %-5d  (%d days)",
                 coin, dex, launch, group, tier, days)
    log.info("")

    summaries = []
    t_global = time.perf_counter()
    for i, (coin, dex, launch, group, tier) in enumerate(selected, 1):
        log.info("=" * 70)
        log.info("[%d/%d] Fetching %s:%s  (launch=%s, %s, tier=%d)",
                 i, len(selected), dex, coin, launch, group, tier)
        log.info("=" * 70)
        try:
            t0 = time.perf_counter()
            s = fetch_one_market(coin, dex, launch, end_dt,
                                  Path(args.data_dir), args.dry_run)
            elapsed = time.perf_counter() - t0
            s["__asset_group"] = group
            s["__tier"] = tier
            s["__elapsed_sec"] = elapsed
            summaries.append(s)
            log.info("[%d/%d] DONE %s:%s in %.1fs — snaps=%s, trades=%s",
                     i, len(selected), dex, coin, elapsed,
                     f"{s.get('kept_snapshots', 0):,}",
                     f"{s.get('trades_in_windows', 0):,}")
        except Exception as e:
            log.error("[%d/%d] FAILED %s:%s — %s", i, len(selected), dex, coin, e)
            summaries.append({
                "coin": coin, "dex": dex, "error": str(e),
                "__asset_group": group, "__tier": tier,
            })

    log.info("")
    log.info("=" * 70)
    log.info("HIP-3 FETCH COMPLETE — total time: %.1f min",
             (time.perf_counter() - t_global) / 60)
    log.info("=" * 70)

    # Per-deployer summary
    by_dex: dict[str, list[dict]] = {}
    for s in summaries:
        by_dex.setdefault(s.get("dex", "?"), []).append(s)
    for dex, items in sorted(by_dex.items()):
        ok = [it for it in items if "error" not in it]
        bad = [it for it in items if "error" in it]
        total_snaps = sum(it.get("kept_snapshots", 0) for it in ok)
        total_trades = sum(it.get("trades_in_windows", 0) for it in ok)
        total_mb = sum(it.get("downloaded_bytes_mb", 0) for it in ok)
        log.info("Deployer %-5s: %d ok, %d failed, %s snaps, "
                 "%s trades, %.1f MB downloaded",
                 dex, len(ok), len(bad),
                 f"{total_snaps:,}", f"{total_trades:,}", total_mb)
        log.info("%-15s %-12s %-12s %-14s %-7s %-25s",
                 "Coin", "Snaps", "Trades", "MB", "Elapsed", "Group")
        for it in items:
            if "error" in it:
                log.info("%-15s  FAILED: %s", it.get("coin", "?"), it["error"])
                continue
            log.info("%-15s %-12s %-12s %-14.1f %-7.0f %-25s",
                     it.get("coin", "?"),
                     f"{it.get('kept_snapshots', 0):,}",
                     f"{it.get('trades_in_windows', 0):,}",
                     it.get("downloaded_bytes_mb", 0),
                     it.get("__elapsed_sec", 0),
                     it.get("__asset_group", "?"))

    return 0


if __name__ == "__main__":
    sys.exit(main())
