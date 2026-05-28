"""
=============================================================================
  Quote Labs — Hydromancer Reservoir Order-Book Fetcher
  user-facing entry point
=============================================================================

Edit the CONFIG section below, then in PyCharm: right-click → Run 'run_fetch'.

What this script does:
  1. For each (asset, date) in your study window, downloads the corresponding
     Reservoir Parquet file from s3://hydromancer-reservoir (requester-pays).
  2. Reads the 1-minute, 20-level L2 snapshots from each file.
  3. Subsamples to the cadence you specified (e.g. every 15 minutes).
  4. Slices the per-day fills file to a ±-window around each kept snapshot.
  5. Writes three normalized Parquet tables per coin under ./data/parquet/:

         book_snapshots/coin={coin}/snapshots.parquet
         trade_window/coin={coin}/trades.parquet
         snapshot_meta/coin={coin}/meta.parquet

Re-running is safe: download state is checkpointed; existing files skipped.

PREREQS (one-time setup):
  1. AWS account with billing active (Reservoir is requester-pays)
  2. `aws configure`  →  set Default region: ap-northeast-1
  3. pip install -r requirements.txt

Verify access:
  aws s3 ls s3://hydromancer-reservoir/by_dex/hyperliquid/orderbook/1m/perps/ \
      --request-payer requester --region ap-northeast-1 | head

NOTE: The free Hydromancer API key is NOT used here — Reservoir is a public
requester-pays S3 bucket. The Hydromancer key is for live REST/WS only.
=============================================================================
"""

from datetime import datetime, timezone
from pathlib import Path

from ob_fetcher import (
    AssetSpec,
    FetchConfig,
    SamplingConfig,
    StorageConfig,
    run_pipeline,
)


# =============================================================================
#  CONFIG — edit me
# =============================================================================

CONFIG = FetchConfig(

    # ----- 1. WHICH ASSETS -----
    assets=[
        AssetSpec(coin="BTC",  dex="hyperliquid", asset_class="perps"),
        AssetSpec(coin="ETH",  dex="hyperliquid", asset_class="perps"),
        AssetSpec(coin="HYPE", dex="hyperliquid", asset_class="perps"),
        # Phase 2 examples (uncomment when ready):
        # AssetSpec(coin="SOL",   dex="hyperliquid", asset_class="perps"),
        # AssetSpec(coin="NVDA",  dex="xyz", asset_class="perps", description="Trade.xyz HIP-3"),
        # AssetSpec(coin="GOOGL", dex="xyz", asset_class="perps"),
    ],

    # ----- 2. SAMPLING (when to keep snapshots) -----
    sampling=SamplingConfig(
        # UTC window — Reservoir data starts 2025-07-28
        start=datetime(2025, 12, 15,  0, 0, 0, tzinfo=timezone.utc),
        end  =datetime(2026, 5, 12,  0, 0, 0, tzinfo=timezone.utc),

        # Reservoir is 1-min native. Keep every 15 minutes for IS analysis.
        # Set to 1 if you want every minute (≈60× more rows).
        keep_every_n_minutes=15,

        # Trade window around each snapshot (for TWAP simulation + opp-cost)
        trade_window_minutes_before=5,
        trade_window_minutes_after=30,
    ),

    # ----- 3. STORAGE (where the data lands) -----
    storage=StorageConfig(
        root=Path("./data"),
        parquet_compression="zstd",
        keep_raw=True,        # set False after validation to save disk
    ),

    # ----- 4. CONCURRENCY / DIAGNOSTICS -----
    max_concurrent_downloads=8,
    fetch_fills=True,         # set False if you only need book snapshots
    verbose=True,

    # If True: print plan, do NOT actually download. Use for first run!
    dry_run=False,
)


# =============================================================================
#  ENTRYPOINT
# =============================================================================

if __name__ == "__main__":
    summaries = run_pipeline(CONFIG)
    print("\nFinal summaries:")
    for s in summaries:
        print(" ", s)
