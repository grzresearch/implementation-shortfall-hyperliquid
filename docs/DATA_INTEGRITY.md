# Data Integrity: The Cross-Deployer Namespace Bug

## Discovery

During mid-project diagnostics, we observed that the coin=TSLA/ partition
contained only 11,067 unique snapshot IDs. This was exactly the count from
km:TSLA alone, despite TSLA being fetched from xyz, flx, km, AND cash.

## Root Cause

The fetcher used the bare coin name (e.g., TSLA) as the partition key in
both:

1. snapshot_id construction (formatted as coin underscore timestamp)
2. Parquet partition path (coin=COIN/snapshots.parquet)

When fetching TSLA from multiple deployers, each new fetch wrote to the
same coin=TSLA/ directory and used the same snapshot_id namespace. Where
timestamps aligned (which happened frequently due to 15-min subsampling),
later writes silently overwrote earlier ones.

## Diagnostic

See code/diagnose_parquet.py. The script inspects all partitions and
counts unique snap_ids vs total rows.

## Fix

A 2-line patch to ob_fetcher/pipeline.py in the _process_asset function:

    def _process_asset(cfg, asset):
        base_coin = asset.coin                       # for fills filter
        coin = f'{asset.dex}__{base_coin}'           # composite key
        ...

The composite key dex__COIN propagates naturally through extract.py and
store.py without further changes.

## Verification

After re-extracting all 92 HIP-3 markets with the patched pipeline:

- diagnose_parquet.py confirms 92 properly-keyed partitions
- Old coin=TSLA/ etc. partitions no longer exist
- Cross-listings now properly separated:
  - coin=xyz__TSLA:  14,182 snaps
  - coin=flx__TSLA:  14,182 snaps
  - coin=km__TSLA:   11,067 snaps
  - coin=cash__TSLA: 10,477 snaps

Total: 4 distinct datasets ready for cross-deployer analysis.

## Why double-underscore separator

Initial attempts used a colon as separator (mirroring Hyperliquids API
naming), but Windows filesystem rejects colon in directory names (WinError
267, since colon is reserved for drive letters). Switched to double
underscore which is universally safe.
