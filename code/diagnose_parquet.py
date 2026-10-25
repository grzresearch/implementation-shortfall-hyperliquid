"""
diagnose_parquet.py
====================
Investigates whether multi-deployer data is properly separated in the
existing parquet directory. Critical pre-pipeline check.
"""

from __future__ import annotations
import os
from pathlib import Path

import polars as pl

BASE = Path(__file__).resolve().parents[1] / "data" / "parquet" / "book_snapshots"

# Coins that exist on multiple deployers (cross-listings)
CROSS_LISTED = {
    "TSLA":   ["xyz", "flx", "km", "cash"],
    "NVDA":   ["xyz", "flx", "km", "cash"],
    "GOLD":   ["xyz", "flx", "km", "cash"],
    "SILVER": ["xyz", "flx", "km", "cash"],
    "COIN":   ["xyz", "flx"],
    "HOOD":   ["xyz", "cash"],
    "AAPL":   ["xyz", "km"],
    "EWY":    ["xyz", "cash"],
    "GOOGL":  ["km", "cash"],
    "XMR":    ["flx", "hyna"],
    "BTC":    ["native", "hyna"],
    "ETH":    ["native", "hyna"],
    "HYPE":   ["native", "hyna"],
    "SOL":    ["native", "hyna"],
}


def inspect_coin(coin: str, expected_dexes: list[str]) -> None:
    coin_dir = BASE / f"coin={coin}"
    if not coin_dir.exists():
        print(f"  coin={coin:<14} → NOT FOUND")
        return

    pq_files = list(coin_dir.glob("*.parquet"))
    if not pq_files:
        print(f"  coin={coin:<14} → no .parquet inside")
        return

    pq_file = pq_files[0]
    size_mb = pq_file.stat().st_size / 1_048_576

    try:
        df = pl.read_parquet(pq_file)
    except Exception as e:
        print(f"  coin={coin:<14} → read error: {e}")
        return

    n_rows = len(df)
    cols = df.columns
    has_dex = "dex" in cols

    # Try common column names for deployer
    dex_col = None
    for candidate in ["dex", "deployer", "exchange", "venue"]:
        if candidate in cols:
            dex_col = candidate
            break

    unique_dexes = []
    if dex_col:
        unique_dexes = sorted(df[dex_col].unique().to_list())

    # Count unique snapshot IDs (a proxy for "how many distinct days/timestamps")
    n_unique_snaps = df["snapshot_id"].n_unique() if "snapshot_id" in cols else "n/a"

    status = "✅" if (len(unique_dexes) >= len(expected_dexes)) else "❌"

    print(f"  coin={coin:<10} {status} "
          f"rows={n_rows:>10,}  uniq_snaps={n_unique_snaps:>8}  "
          f"size={size_mb:>6.1f}MB  "
          f"dex_col={'YES' if dex_col else 'NO ':<3}  "
          f"actual_dexes={unique_dexes}  "
          f"expected={expected_dexes}")


def main():
    print(f"Diagnosing {BASE}\n")

    # First: list ALL columns in one parquet to understand schema
    sample_coin_dir = BASE / "coin=TSLA"
    sample_files = list(sample_coin_dir.glob("*.parquet"))
    if sample_files:
        sample_df = pl.read_parquet(sample_files[0])
        print(f"=== Schema of coin=TSLA/{sample_files[0].name} ===")
        for col in sample_df.columns:
            dtype = sample_df.schema[col]
            print(f"  {col:<30} {dtype}")
        print(f"\n=== First 2 rows ===")
        print(sample_df.head(2))
        print()

    print(f"\n=== Cross-listed Coin Inspection ===\n")
    print(f"  {'Coin':<14} {'Status':<3} {'Details'}")
    print(f"  {'-'*14} {'-'*3} {'-'*80}")
    for coin, expected_dexes in CROSS_LISTED.items():
        inspect_coin(coin, expected_dexes)


if __name__ == "__main__":
    main()
