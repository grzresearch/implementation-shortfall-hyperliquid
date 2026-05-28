# Pipeline Architecture

## Overview

The pipeline is a 6-module sequential workflow. See ../README.md for the
high-level diagram.

## Design Decisions

### Memory-bounded streaming

The total dataset is 15 GB of compressed Parquet. Loading it all into RAM
is infeasible on most machines. The pipeline uses a two-pass per-asset
approach:

1. Load all daily orderbook parquets per dex+coin into memory (small,
   10 to 50 MB per coin).
2. Stream daily fills parquets, slicing each days trades into per-snapshot
   windows via numpy searchsorted. Flush completed snapshots to a parquet
   writer, free the days data.

Peak RAM scales with one days fills, not the full study period.

### Composite keys

HIP-3 means the same underlying (e.g., TSLA) trades on multiple deployers
(xyz, flx, km, cash). The original namespace used coin alone as the partition
key, which caused cross-deployer data to overwrite each other when snapshot
timestamps aligned.

The fix: use dex__COIN as the universal partition key (e.g., xyz__TSLA).
The double-underscore separator is filesystem-safe on Windows where colon
is reserved for drive letters.

### Per-deployer fees from CSV

Different deployers configure different fee scales, growth-mode status,
and quote-asset alignments. Hardcoding fees would couple the IS decomposer
to a snapshot in time. Instead, the fee schedule is generated externally
from the Hyperliquid info API and loaded at runtime.

The CSV is committed at results/hip3_fee_schedule.csv for reproducibility.

## Performance

Total pipeline runtime: ~6 hours on a laptop with 8 cores + 32 GB RAM.

| Module | Duration | Output Size |
|---|---|---|
| Modul 1 (discovery) | 5 min | universe.csv (50 KB) |
| Modul 2 (HIP-3 fetch) | 2-3 hours | data/parquet/ (12 GB) |
| Modul 2 (native HL fetch) | 30-60 min | data/parquet/ (3 GB) |
| Modul 4 (simulator) | 60 min | runs.parquet (920 MB) |
| Modul 5 (IS decomp) | 3 min | is_decomp.parquet (1.9 GB) |
| Modul 6 (aggregator) | 15 min | summary_stats.csv (1.2 MB) |
