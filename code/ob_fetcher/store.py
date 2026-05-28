"""Parquet output writer for the three canonical tables."""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from .config import StorageConfig
from .extract import MetaRecord, SnapshotRecord, TradeRecord


_BOOK_SCHEMA = pa.schema([
    ("snapshot_id", pa.string()),
    ("target_ts_ms", pa.int64()),
    ("actual_ts_ms", pa.int64()),
    ("coin", pa.string()),
    ("side", pa.string()),
    ("level", pa.int16()),
    ("px", pa.float64()),
    ("sz", pa.float64()),
    ("n", pa.int32()),
])

_TRADE_SCHEMA = pa.schema([
    ("snapshot_id", pa.string()),
    ("coin", pa.string()),
    ("ts_ms", pa.int64()),
    ("ts_offset_ms", pa.int64()),
    ("side", pa.string()),
    ("px", pa.float64()),
    ("sz", pa.float64()),
    ("tid", pa.int64()),
])

_META_SCHEMA = pa.schema([
    ("snapshot_id", pa.string()),
    ("coin", pa.string()),
    ("target_ts_ms", pa.int64()),
    ("actual_ts_ms", pa.int64()),
    ("skew_ms", pa.int64()),
    ("mid", pa.float64()),
    ("microprice", pa.float64()),
    ("spread_bps", pa.float64()),
    ("top_bid_sz", pa.float64()),
    ("top_ask_sz", pa.float64()),
    ("book_levels", pa.int16()),
    ("n_trades_window", pa.int32()),
])


def _records_to_table(records: list, schema: pa.Schema) -> pa.Table:
    cols: dict[str, list] = {f.name: [] for f in schema}
    for r in records:
        d = r.__dict__
        for name in cols:
            cols[name].append(d[name])
    return pa.Table.from_pydict(cols, schema=schema)


def write_parquet_partition(
    storage: StorageConfig,
    coin: str,
    book_records: list[SnapshotRecord],
    trade_records: list[TradeRecord],
    meta_records: list[MetaRecord],
) -> dict[str, Path]:
    base = storage.parquet_dir()
    paths = {
        "book":   base / "book_snapshots" / f"coin={coin}" / "snapshots.parquet",
        "trades": base / "trade_window"   / f"coin={coin}" / "trades.parquet",
        "meta":   base / "snapshot_meta"  / f"coin={coin}" / "meta.parquet",
    }
    for p in paths.values():
        p.parent.mkdir(parents=True, exist_ok=True)

    if book_records:
        pq.write_table(
            _records_to_table(book_records, _BOOK_SCHEMA),
            paths["book"],
            compression=storage.parquet_compression,
            row_group_size=storage.parquet_row_group_size,
        )
    if trade_records:
        pq.write_table(
            _records_to_table(trade_records, _TRADE_SCHEMA),
            paths["trades"],
            compression=storage.parquet_compression,
            row_group_size=storage.parquet_row_group_size,
        )
    if meta_records:
        pq.write_table(
            _records_to_table(meta_records, _META_SCHEMA),
            paths["meta"],
            compression=storage.parquet_compression,
            row_group_size=storage.parquet_row_group_size,
        )
    return paths
