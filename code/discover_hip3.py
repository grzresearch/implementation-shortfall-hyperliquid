"""
discover_hip3.py  (v3)
========================
Exact per-ticker launch dates and complete data inventory for HIP-3 markets.

Approach:
  1. List EVERY key under by_dex/{deployer}/orderbook/1m/perps/  (paginated)
  2. Parse each key into (ticker, date, size_bytes)
  3. Per ticker compute:
       - launch_date      = earliest date seen
       - latest_date      = most recent date seen
       - n_days_available = number of distinct dates
       - n_days_missing   = gaps between launch and latest (delisted days)
       - total_size_mb    = sum of all orderbook file sizes
  4. Also list fills directory structure to confirm coverage

Output is sorted by total data volume — markets at the top are the
most-active ones for the IS analysis.

A copy-paste-ready Python dict at the end gives you LAUNCH_DATES for
fetch_hip3.py.

Usage:
    python discover_hip3.py                    # full inventory
    python discover_hip3.py --deployer xyz     # only one
    python discover_hip3.py --include-fills    # also inventory fills dir
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from datetime import date, timedelta

import boto3

BUCKET = "hydromancer-reservoir"
REGION = "ap-northeast-1"

DEPLOYERS = ["xyz", "vntl", "flx", "hyna", "km", "cash"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_client():
    return boto3.client("s3", region_name=REGION)


def list_all_orderbook_keys(s3, deployer: str) -> list[tuple[str, str, int]]:
    """List every (ticker, date_str, size_bytes) under the orderbook dir."""
    prefix = f"by_dex/{deployer}/orderbook/1m/perps/"
    paginator = s3.get_paginator("list_objects_v2")
    out: list[tuple[str, str, int]] = []
    pages = paginator.paginate(
        Bucket=BUCKET, Prefix=prefix, RequestPayer="requester",
    )
    n_pages = 0
    for page in pages:
        n_pages += 1
        for obj in page.get("Contents", []):
            key: str = obj["Key"]
            size: int = obj["Size"]
            parts = key.split("/")
            # Expect: by_dex/{dex}/orderbook/1m/perps/date=YYYY-MM-DD/TICKER.parquet
            if len(parts) < 7:
                continue
            date_part = parts[-2]
            file_part = parts[-1]
            if not date_part.startswith("date="):
                continue
            if not file_part.endswith(".parquet"):
                continue
            date_str = date_part[len("date="):]
            ticker = file_part[:-len(".parquet")]
            out.append((ticker, date_str, size))
    return out


def list_fills_dates(s3, deployer: str) -> list[str]:
    """List which dates have fills/perp/all/ files."""
    prefix = f"by_dex/{deployer}/fills/perp/all/"
    paginator = s3.get_paginator("list_objects_v2")
    dates = []
    pages = paginator.paginate(
        Bucket=BUCKET, Prefix=prefix, RequestPayer="requester",
    )
    for page in pages:
        for obj in page.get("Contents", []):
            key = obj["Key"]
            parts = key.split("/")
            if len(parts) < 6 or not parts[-2].startswith("date="):
                continue
            dates.append(parts[-2][len("date="):])
    return sorted(set(dates))


def daterange(start_str: str, end_str: str) -> list[str]:
    """Inclusive list of YYYY-MM-DD between two dates."""
    s = date.fromisoformat(start_str)
    e = date.fromisoformat(end_str)
    days = []
    d = s
    while d <= e:
        days.append(d.isoformat())
        d += timedelta(days=1)
    return days


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def summarize_ticker_inventory(records: list[tuple[str, str, int]],
                                deployer: str,
                                fills_dates: set[str] | None) -> dict:
    """Per-ticker aggregates with optional fills coverage check."""
    by_ticker: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for ticker, d, sz in records:
        by_ticker[ticker].append((d, sz))

    summary = {}
    for ticker, items in by_ticker.items():
        items.sort()
        dates_avail = [it[0] for it in items]
        total_bytes = sum(it[1] for it in items)
        launch = dates_avail[0]
        latest = dates_avail[-1]
        full_range = daterange(launch, latest)
        n_missing = len(full_range) - len(set(dates_avail))
        summary[ticker] = {
            "launch_date":      launch,
            "latest_date":      latest,
            "n_days_available": len(set(dates_avail)),
            "n_days_in_range":  len(full_range),
            "n_days_missing":   n_missing,
            "total_size_mb":    total_bytes / 1_048_576,
            "avg_daily_mb":     total_bytes / 1_048_576 / max(1, len(set(dates_avail))),
        }
    return summary


def print_inventory_table(summary: dict, deployer: str):
    if not summary:
        print(f"  No data found for {deployer}")
        return
    rows = sorted(summary.items(),
                   key=lambda kv: -kv[1]["total_size_mb"])
    print(f"\n  {'Ticker':<14}{'Launch':<13}{'Latest':<13}"
          f"{'Days':>6}{'Missing':>9}{'TotalMB':>12}{'Avg/d MB':>10}")
    print(f"  {'-'*14}{'-'*13}{'-'*13}{'-'*6}{'-'*9}{'-'*12}{'-'*10}")
    for ticker, s in rows:
        print(f"  {ticker:<14}{s['launch_date']:<13}{s['latest_date']:<13}"
              f"{s['n_days_available']:>6}"
              f"{s['n_days_missing']:>9}"
              f"{s['total_size_mb']:>11.2f}"
              f"{s['avg_daily_mb']:>10.3f}")


def print_python_dict(summary: dict, deployer: str):
    print(f"\n# ---- copy into fetch_hip3.py ----")
    print(f"{deployer.upper()}_LAUNCH_DATES = {{")
    rows = sorted(summary.items())
    for ticker, s in rows:
        print(f'    "{ticker}": "{s["launch_date"]}",')
    print(f"}}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--deployer", choices=["xyz", "vntl", "flx", "hyna", "km", "cash"], default=None,
                   help="Only inventory one deployer")
    p.add_argument("--include-fills", action="store_true",
                   help="Also list fills directory dates (slower)")
    p.add_argument("--out-json", default=None,
                   help="Write summary to JSON file")
    args = p.parse_args()

    s3 = make_client()
    deployers = [args.deployer] if args.deployer else DEPLOYERS

    full_summary = {}
    for deployer in deployers:
        print(f"\n{'=' * 78}")
        print(f"  Deployer: {deployer}")
        print(f"{'=' * 78}")

        print(f"  Listing all orderbook keys under by_dex/{deployer}/orderbook/...")
        t0 = time.perf_counter()
        records = list_all_orderbook_keys(s3, deployer)
        t_elapsed = time.perf_counter() - t0
        print(f"  {len(records):,} keys discovered in {t_elapsed:.1f}s")

        fills_dates_set: set[str] | None = None
        if args.include_fills:
            print(f"  Listing fills directory ...")
            fills_dates = list_fills_dates(s3, deployer)
            fills_dates_set = set(fills_dates)
            print(f"  {len(fills_dates_set):,} fills dates found "
                  f"({min(fills_dates) if fills_dates else 'n/a'} → "
                  f"{max(fills_dates) if fills_dates else 'n/a'})")

        summary = summarize_ticker_inventory(records, deployer, fills_dates_set)
        full_summary[deployer] = summary

        print(f"\n  Per-ticker inventory (sorted by total data volume):")
        print_inventory_table(summary, deployer)

        # Total bucket size
        total_mb = sum(s["total_size_mb"] for s in summary.values())
        total_days = sum(s["n_days_available"] for s in summary.values())
        print(f"\n  Total: {len(summary)} tickers, "
              f"{total_days} ticker-days, "
              f"{total_mb:,.0f} MB orderbook data")

        print_python_dict(summary, deployer)

    # JSON dump
    if args.out_json:
        with open(args.out_json, "w") as f:
            json.dump(full_summary, f, indent=2, default=str)
        print(f"\nFull summary written to {args.out_json}")

    print(f"\n{'=' * 78}")
    print("DONE")
    print(f"{'=' * 78}")


if __name__ == "__main__":
    sys.exit(main() or 0)
