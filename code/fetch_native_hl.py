"""
fetch_native_hl.py (EXPANDED)
==============================
Re-extract / fetch native Hyperliquid markets for cross-listing analysis
with HIP-3 deployers.

Phase 1 (cached): BTC, ETH, HYPE
Phase 2 (NEW S3 download): SOL, XRP, DOGE, BNB, LINK, XMR

Total 9 native HL markets. ~10 GB S3 download for the 6 new ones.
Estimated cost: $0.50-$1.00 AWS egress.

Prerequisites:
  - ob_fetcher/pipeline.py is PATCHED (composite key with __ separator)

Usage:
  python fetch_native_hl.py
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timezone
from pathlib import Path

from ob_fetcher.config import (
    AssetSpec,
    FetchConfig,
    ReservoirConfig,
    SamplingConfig,
    StorageConfig,
)
from ob_fetcher.pipeline import run_pipeline

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("fetch_native_hl")


# Native HL markets — Phase 1 (cached) + Phase 2 (will download from S3)
NATIVE_HL_MARKETS = [
    # ─── Phase 1: already cached locally ───
    {"coin": "BTC",  "launch": date(2025, 12, 15), "asset_class": "crypto_native"},
    {"coin": "ETH",  "launch": date(2025, 12, 15), "asset_class": "crypto_native"},
    {"coin": "HYPE", "launch": date(2025, 12, 15), "asset_class": "crypto_native"},
    # ─── Phase 2: NEW S3 downloads (cross-listings with HyENA) ───
    {"coin": "SOL",  "launch": date(2025, 12, 15), "asset_class": "crypto_native"},
    {"coin": "XRP",  "launch": date(2025, 12, 15), "asset_class": "crypto_altcoin"},
    {"coin": "DOGE", "launch": date(2025, 12, 15), "asset_class": "crypto_altcoin"},
    {"coin": "BNB",  "launch": date(2025, 12, 15), "asset_class": "crypto_altcoin"},
    {"coin": "LINK", "launch": date(2025, 12, 15), "asset_class": "crypto_altcoin"},
    {"coin": "XMR",  "launch": date(2025, 12, 15), "asset_class": "crypto_altcoin"},
]


DATA_DIR = Path(r"C:\Users\grego\Downloads\fetcher\data")
END_DATE = date(2026, 5, 12)
DRY_RUN = False


def main():
    log.info("=" * 70)
    log.info("NATIVE HL RE-EXTRACT + EXPANSION")
    log.info("=" * 70)
    log.info("Selected markets: %d", len(NATIVE_HL_MARKETS))
    log.info("End date:         %s", END_DATE)
    log.info("Data dir:         %s", DATA_DIR)
    log.info("Dry run:          %s", DRY_RUN)
    log.info("")
    log.info("Phase 1 (cached): BTC, ETH, HYPE")
    log.info("Phase 2 (S3 dl):  SOL, XRP, DOGE, BNB, LINK, XMR")
    log.info("")

    log.info("%-12s %-15s %-25s", "Coin", "Launch", "Asset class")
    log.info("-" * 70)
    for m in NATIVE_HL_MARKETS:
        days = (END_DATE - m["launch"]).days
        log.info("%-12s %-15s %-25s (%d days)",
                 m["coin"], m["launch"], m["asset_class"], days)

    t_all = time.perf_counter()
    summaries = []

    for i, m in enumerate(NATIVE_HL_MARKETS, 1):
        log.info("")
        log.info("=" * 70)
        log.info("[%d/%d] Fetching hyperliquid:%s  (launch=%s, %s)",
                 i, len(NATIVE_HL_MARKETS), m["coin"], m["launch"],
                 m["asset_class"])
        log.info("=" * 70)

        start_dt = datetime.combine(
            m["launch"], datetime.min.time()
        ).replace(tzinfo=timezone.utc)
        end_dt = datetime.combine(
            END_DATE, datetime.max.time()
        ).replace(tzinfo=timezone.utc)

        cfg = FetchConfig(
            assets=[AssetSpec(coin=m["coin"], dex="hyperliquid",
                              asset_class="perps")],
            sampling=SamplingConfig(
                start=start_dt,
                end=end_dt,
                keep_every_n_minutes=15,
                trade_window_minutes_before=5,
                trade_window_minutes_after=30,
            ),
            reservoir=ReservoirConfig(
                bucket="hydromancer-reservoir",
                requester_pays=True,
                data_start_date=date(2025, 12, 1),
            ),
            storage=StorageConfig(root=DATA_DIR),
            fetch_fills=True,
            max_concurrent_downloads=8,
            dry_run=DRY_RUN,
            verbose=True,
        )

        t0 = time.perf_counter()
        try:
            result = run_pipeline(cfg)
            elapsed = time.perf_counter() - t0
            for r in result:
                summaries.append({
                    "coin": r["coin"],
                    "dex": "hyperliquid",
                    "snaps": r.get("kept_snapshots", 0),
                    "trades": r.get("trades_in_windows", 0),
                    "mb": r.get("downloaded_bytes_mb", 0.0),
                    "elapsed": round(elapsed),
                    "asset_class": m["asset_class"],
                    "error": r.get("error"),
                })
                if r.get("error"):
                    log.error("[%d/%d] FAILED hyperliquid:%s — %s",
                              i, len(NATIVE_HL_MARKETS), m["coin"], r["error"])
                else:
                    log.info("[%d/%d] DONE hyperliquid:%s in %.1fs — "
                             "snaps=%s, trades=%s, %.1f MB downloaded",
                             i, len(NATIVE_HL_MARKETS), m["coin"], elapsed,
                             f"{r.get('kept_snapshots', 0):,}",
                             f"{r.get('trades_in_windows', 0):,}",
                             r.get('downloaded_bytes_mb', 0.0))
        except Exception as e:
            log.exception("Failure on hyperliquid:%s", m["coin"])
            summaries.append({
                "coin": m["coin"], "dex": "hyperliquid",
                "error": str(e), "asset_class": m["asset_class"],
            })

    log.info("")
    log.info("=" * 70)
    log.info("NATIVE HL RE-EXTRACT + EXPANSION COMPLETE — total time: %.1f min",
             (time.perf_counter() - t_all) / 60)
    log.info("=" * 70)

    n_ok = sum(1 for s in summaries if not s.get("error"))
    n_failed = sum(1 for s in summaries if s.get("error"))
    total_snaps = sum(s.get("snaps", 0) for s in summaries)
    total_trades = sum(s.get("trades", 0) for s in summaries)
    total_mb = sum(s.get("mb", 0.0) for s in summaries)

    log.info("Deployer hyperliquid: %d ok, %d failed, %s snaps, %s trades, "
             "%.1f MB downloaded",
             n_ok, n_failed,
             f"{total_snaps:,}", f"{total_trades:,}", total_mb)
    log.info("%-15s %-12s %-12s %-12s %-8s %s",
             "Coin", "Snaps", "Trades", "MB", "Elapsed", "Group")
    for s in summaries:
        log.info("%-15s %-12s %-12s %-12s %-8s %s",
                 s["coin"],
                 f"{s.get('snaps', 0):,}",
                 f"{s.get('trades', 0):,}",
                 f"{s.get('mb', 0.0):.1f}",
                 s.get("elapsed", 0),
                 s.get("asset_class", ""))


if __name__ == "__main__":
    main()