"""Streaming orchestrator for the Reservoir backend.

Memory-bounded: processes fills file-by-file, attributes trades to snapshots,
streams TradeRecord batches to parquet as snapshot windows complete. No
unbounded accumulation. Peak RAM scales with one day of fills, not total
study period.

Per asset:
  1. Determine (asset, date) pairs in the study window.
  2. Concurrent download of orderbook + fills parquets (idempotent).
  3. Read all orderbook files into memory (small).
  4. Subsample to snapshot grid.
  5. Build snapshot index with per-snapshot "deadline" date (latest fills
     file the snapshot's +window needs).
  6. STREAM through fills files in date order:
        a. Read one day's fills.
        b. For each snapshot whose window overlaps this day, slice via bisect
           and accumulate TradeRecord objects in pending dict.
        c. After processing this file, flush snapshots whose deadline has
           passed to parquet (pq.ParquetWriter, streaming).
        d. Free the day's fills from memory.
  7. Final flush; write book + meta parquets (small).
"""

from __future__ import annotations

import bisect
import gc
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from .config import AssetSpec, FetchConfig
from .extract import (
    MetaRecord,
    SnapshotRecord,
    TradeRecord,
    _make_snap_id,
    compute_meta,
    expand_book,
    read_fills_parquet,
    read_orderbook_parquet,
    subsample_frames,
)
from .reservoir import (
    DownloadResult,
    ReservoirClient,
    dates_in_window,
    fills_key,
    orderbook_key,
)
from .store import _BOOK_SCHEMA, _META_SCHEMA, _TRADE_SCHEMA, _records_to_table

log = logging.getLogger(__name__)


def _setup_logging(verbose: bool) -> None:
    level = logging.INFO if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )


# ---------------------------------------------------------------------------
# Checkpoint helpers
# ---------------------------------------------------------------------------


def _ckpt_file(cfg: FetchConfig, asset: AssetSpec) -> Path:
    return cfg.storage.checkpoint_dir() / f"{asset.dex}_{asset.coin}.ckpt.json"


def _load_ckpt(cfg: FetchConfig, asset: AssetSpec) -> set[str]:
    f = _ckpt_file(cfg, asset)
    if not f.exists():
        return set()
    try:
        return set(json.loads(f.read_text()).get("downloaded_keys", []))
    except json.JSONDecodeError:
        return set()


def _save_ckpt(cfg: FetchConfig, asset: AssetSpec, keys: set[str]) -> None:
    f = _ckpt_file(cfg, asset)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"downloaded_keys": sorted(keys)}, indent=2))


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------


def _concurrent_download(client: ReservoirClient, keys: list[str],
                         max_workers: int) -> list[DownloadResult]:
    out: list[DownloadResult] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        fut_map = {pool.submit(client.download, k): k for k in keys}
        for fut in as_completed(fut_map):
            try:
                out.append(fut.result())
            except Exception as e:  # noqa: BLE001
                out.append(DownloadResult(
                    key=fut_map[fut], bytes_downloaded=0,
                    local_path=None, error=str(e),
                ))
    return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _date_at_ms(ms: int) -> date:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date()


# ---------------------------------------------------------------------------
# Per-asset streaming pipeline
# ---------------------------------------------------------------------------


def _process_asset(cfg: FetchConfig, asset: AssetSpec) -> dict:
    base_coin = asset.coin                       # for fills base_symbol filter
    coin = f"{asset.dex}__{base_coin}"           # composite key (double-underscore for Windows compat)
    log.info("=== %s:%s ===", asset.dex, base_coin)

    client = ReservoirClient(cfg.reservoir, cfg.storage.raw_dir())
    all_dates = sorted(dates_in_window(cfg.sampling.start, cfg.sampling.end))

    fills_dates = sorted({d for d in all_dates} |
                         {all_dates[0] - timedelta(days=1)} |
                         {all_dates[-1] + timedelta(days=1)})
    fills_dates = [d for d in fills_dates if d >= cfg.reservoir.data_start_date]

    book_keys = [orderbook_key(cfg.reservoir, asset, d) for d in all_dates]
    fill_pairs = [(d, fills_key(cfg.reservoir, asset, d)) for d in fills_dates] \
                 if cfg.fetch_fills else []
    all_keys = book_keys + [k for _, k in fill_pairs]

    done = _load_ckpt(cfg, asset)
    todo = [k for k in all_keys if k not in done
            and not (cfg.storage.raw_dir() / k).exists()]

    log.info("Days: %d  |  files needed: %d  |  cached: %d  |  to fetch: %d",
             len(all_dates), len(all_keys), len(all_keys) - len(todo), len(todo))

    if cfg.dry_run:
        sample = todo[:3] if todo else book_keys[:3]
        log.info("DRY RUN — would fetch %d files. First 3: %s", len(todo), sample)
        return {"coin": coin, "dex": asset.dex, "downloaded": 0,
                "kept_snapshots": 0, "trades_in_windows": 0, "dry_run": True}

    # === DOWNLOAD ===
    t0 = time.perf_counter()
    results = _concurrent_download(client, todo, cfg.max_concurrent_downloads)
    n_ok = sum(1 for r in results if r.error is None)
    n_err = sum(1 for r in results if r.error is not None)
    total_mb = sum(r.bytes_downloaded for r in results) / 1_048_576
    log.info("Downloaded %d/%d files (%.1f MB) in %.1fs  [errors=%d, missing=%d]",
             n_ok, len(todo), total_mb, time.perf_counter() - t0, n_err, n_err)

    done |= {k for k in all_keys if (cfg.storage.raw_dir() / k).exists()}
    _save_ckpt(cfg, asset, done)

    # === STAGE 1: ORDERBOOK (in memory — small) ===
    log.info("Reading %d orderbook parquet files...", len(book_keys))
    all_frames: list[dict] = []
    for k in book_keys:
        path = cfg.storage.raw_dir() / k
        try:
            all_frames.extend(read_orderbook_parquet(path, coin))
        except Exception as e:  # noqa: BLE001
            log.warning("  read fail (%s): %s", k, e)
    all_frames.sort(key=lambda f: f["ts_ms"])
    log.info("  -> %d raw 1-min frames", len(all_frames))

    kept_frames = subsample_frames(all_frames, cfg.sampling)
    log.info("After subsampling (every %d min): %d frames",
             cfg.sampling.keep_every_n_minutes, len(kept_frames))

    del all_frames
    gc.collect()

    # === STAGE 2: STREAMING FILLS → TRADE PARQUET ===
    storage = cfg.storage
    trade_path = (storage.parquet_dir() / "trade_window"
                  / f"coin={coin}" / "trades.parquet")
    trade_path.parent.mkdir(parents=True, exist_ok=True)

    pad_before_ms = cfg.sampling.trade_window_minutes_before * 60_000
    pad_after_ms = cfg.sampling.trade_window_minutes_after * 60_000

    # Snapshot index: for each kept snapshot, precompute window bounds + deadline
    snap_index = []
    for frame in kept_frames:
        ts = frame["ts_ms"]
        snap_index.append({
            "ts": ts,
            "lo": ts - pad_before_ms,
            "hi": ts + pad_after_ms,
            "id": _make_snap_id(coin, ts),
            "deadline": _date_at_ms(ts + pad_after_ms),
        })

    pending: dict[str, list[TradeRecord]] = {}
    n_trades_by_snap: dict[str, int] = {}
    writer = None
    total_trades_written = 0

    if cfg.fetch_fills and fill_pairs:
        log.info("Streaming %d fills parquet files (memory-bounded)...",
                 len(fill_pairs))
        t_phase = time.perf_counter()

        for fi, (fill_date, k) in enumerate(fill_pairs, 1):
            path = storage.raw_dir() / k
            if not path.exists():
                continue

            t_file = time.perf_counter()
            try:
                day_trades = read_fills_parquet(path, base_coin)
            except Exception as e:  # noqa: BLE001
                log.warning("  read fail (%s): %s", k, e)
                continue

            n_added = 0
            if day_trades:
                day_lo_ms = day_trades[0]["ts_ms"]
                day_hi_ms = day_trades[-1]["ts_ms"]

                for snap in snap_index:
                    if snap["hi"] < day_lo_ms or snap["lo"] > day_hi_ms:
                        continue
                    i_lo = bisect.bisect_left(day_trades, snap["lo"],
                                               key=lambda t: t["ts_ms"])
                    i_hi = bisect.bisect_right(day_trades, snap["hi"],
                                                key=lambda t: t["ts_ms"])
                    if i_hi <= i_lo:
                        continue
                    snap_ts = snap["ts"]
                    snap_id = snap["id"]
                    recs = [
                        TradeRecord(
                            snapshot_id=snap_id, coin=coin,
                            ts_ms=t["ts_ms"],
                            ts_offset_ms=t["ts_ms"] - snap_ts,
                            side=t.get("side", ""),
                            px=t["px"], sz=t["sz"], tid=t.get("tid", 0),
                            builder=t.get("builder", ""),
                            builder_fee=t.get("builder_fee", 0.0),
                            fee=t.get("fee", 0.0),
                        )
                        for t in day_trades[i_lo:i_hi]
                    ]
                    pending.setdefault(snap_id, []).extend(recs)
                    n_trades_by_snap[snap_id] = (
                        n_trades_by_snap.get(snap_id, 0) + len(recs)
                    )
                    n_added += len(recs)

                del day_trades
                gc.collect()

            # Flush snapshots whose deadline has passed
            ready = [s for s in snap_index
                     if s["deadline"] < fill_date and s["id"] in pending]
            n_flushed = 0
            if ready:
                batch: list[TradeRecord] = []
                for snap in ready:
                    batch.extend(pending.pop(snap["id"]))
                if batch:
                    tbl = _records_to_table(batch, _TRADE_SCHEMA)
                    if writer is None:
                        writer = pq.ParquetWriter(
                            trade_path, tbl.schema,
                            compression=storage.parquet_compression,
                        )
                    writer.write_table(tbl)
                    n_flushed = len(batch)
                    total_trades_written += n_flushed
                    del batch

            elapsed = time.perf_counter() - t_file
            log.info("  [%3d/%d] %s: +%d trades  |  pending=%d snaps  |  "
                     "flushed=%d  |  %.1fs",
                     fi, len(fill_pairs), fill_date, n_added,
                     len(pending), n_flushed, elapsed)

        # Final flush: any remaining pending snapshots
        if pending:
            batch = []
            for recs in pending.values():
                batch.extend(recs)
            pending.clear()
            if batch:
                tbl = _records_to_table(batch, _TRADE_SCHEMA)
                if writer is None:
                    writer = pq.ParquetWriter(
                        trade_path, tbl.schema,
                        compression=storage.parquet_compression,
                    )
                writer.write_table(tbl)
                total_trades_written += len(batch)
                log.info("  final flush: %d trades", len(batch))

        if writer is not None:
            writer.close()
            mb = trade_path.stat().st_size / 1_048_576
            log.info("  trades -> %s (%.2f MB, %d trades, fills phase %.1fs)",
                     trade_path.relative_to(storage.root), mb,
                     total_trades_written, time.perf_counter() - t_phase)

    # === STAGE 3: BOOK + META (in memory — small) ===
    book_recs: list[SnapshotRecord] = []
    meta_recs: list[MetaRecord] = []
    for frame in kept_frames:
        snap_id = _make_snap_id(coin, frame["ts_ms"])
        n_window = n_trades_by_snap.get(snap_id, 0)
        book_recs.extend(expand_book(frame))
        meta_recs.append(compute_meta(frame, n_window))

    book_path = (storage.parquet_dir() / "book_snapshots"
                 / f"coin={coin}" / "snapshots.parquet")
    meta_path = (storage.parquet_dir() / "snapshot_meta"
                 / f"coin={coin}" / "meta.parquet")
    book_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.parent.mkdir(parents=True, exist_ok=True)

    if book_recs:
        pq.write_table(_records_to_table(book_recs, _BOOK_SCHEMA), book_path,
                       compression=storage.parquet_compression,
                       row_group_size=storage.parquet_row_group_size)
        log.info("  book -> %s (%.2f MB)",
                 book_path.relative_to(storage.root),
                 book_path.stat().st_size / 1_048_576)
    if meta_recs:
        pq.write_table(_records_to_table(meta_recs, _META_SCHEMA), meta_path,
                       compression=storage.parquet_compression)
        log.info("  meta -> %s (%.2f MB)",
                 meta_path.relative_to(storage.root),
                 meta_path.stat().st_size / 1_048_576)

    if not storage.keep_raw:
        log.info("keep_raw=False — clearing raw cache for %s", coin)
        for k in all_keys:
            (storage.raw_dir() / k).unlink(missing_ok=True)

    return {
        "coin": coin, "dex": asset.dex,
        "kept_snapshots": len(meta_recs),
        "trades_in_windows": total_trades_written,
        "downloaded_bytes_mb": round(total_mb, 1),
    }


# ---------------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------------


def run_pipeline(cfg: FetchConfig) -> list[dict]:
    cfg.validate()
    _setup_logging(cfg.verbose)
    log.info("\n%s\n", cfg.summary())
    cfg.storage.ensure_dirs()

    summaries = []
    for asset in cfg.assets:
        try:
            summaries.append(_process_asset(cfg, asset))
        except Exception as e:  # noqa: BLE001
            log.exception("Asset %s:%s failed", asset.dex, asset.coin)
            summaries.append({"coin": asset.coin, "dex": asset.dex,
                              "error": str(e)})

    log.info("\n=== DONE ===")
    for s in summaries:
        log.info("  %s", s)
    return summaries
