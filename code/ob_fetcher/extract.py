"""Read raw Reservoir Parquet files and convert to our internal record schema.

v2 changes (May 16, 2026):
  - Fills filter now uses `base_symbol` column (HIP-3 compatible) with
    fallback to `coin`/`symbol` for backwards compatibility.
  - HIP-3 fills have `coin = 'xyz:NVDA'` but `base_symbol = 'NVDA'`.
    Filtering on base_symbol works uniformly across HL native and HIP-3.

Reservoir orderbook schema:
    block_time_ms : int64
    block_number  : uint64
    bids/asks     : list<struct<px: string, sz: string, n: uint32>>

Reservoir fills schema (per docs.hydromancer.xyz):
    timestamp, coin (prefixed for HIP-3), dex, asset_class, base_symbol,
    quote_symbol, price, size, side, direction, trade_id, builder,
    builder_fee, fee, ... (27 columns total)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from .config import AssetSpec, SamplingConfig

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal record schema
# ---------------------------------------------------------------------------


@dataclass
class SnapshotRecord:
    snapshot_id: str
    target_ts_ms: int
    actual_ts_ms: int
    coin: str
    side: str
    level: int
    px: float
    sz: float
    n: int


@dataclass
class TradeRecord:
    snapshot_id: str
    coin: str
    ts_ms: int
    ts_offset_ms: int
    side: str
    px: float
    sz: float
    tid: int
    builder: str = ""
    builder_fee: float = 0.0
    fee: float = 0.0


@dataclass
class MetaRecord:
    snapshot_id: str
    coin: str
    target_ts_ms: int
    actual_ts_ms: int
    skew_ms: int
    mid: float
    microprice: float
    spread_bps: float
    top_bid_sz: float
    top_ask_sz: float
    book_levels: int
    n_trades_window: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_snap_id(coin: str, ts_ms: int) -> str:
    iso = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{coin}_{iso}"


def _safe_float(s) -> float:
    try:
        return float(s)
    except (TypeError, ValueError):
        return float("nan")


def _safe_int(s, default: int = 0) -> int:
    try:
        return int(s)
    except (TypeError, ValueError):
        return default


def _read_timestamps_as_ms_array(ts_array: pa.Array) -> np.ndarray:
    """Vectorized conversion of a pyarrow timestamp/integer column to ms-since-epoch."""
    t = ts_array.type
    if pa.types.is_timestamp(t):
        ts_int = ts_array.cast(pa.int64()).to_numpy(zero_copy_only=False).astype('int64')
        unit = t.unit
        if unit == 's':
            return (ts_int * 1000).astype('int64')
        elif unit == 'ms':
            return ts_int
        elif unit == 'us':
            return (ts_int // 1000).astype('int64')
        elif unit == 'ns':
            return (ts_int // 1_000_000).astype('int64')
        else:
            return ts_int

    if pa.types.is_integer(t):
        ts_int = ts_array.to_numpy(zero_copy_only=False).astype('int64')
        abs_int = np.abs(ts_int)
        return np.where(
            abs_int < 10**11, ts_int * 1000,
            np.where(
                abs_int < 10**14, ts_int,
                np.where(
                    abs_int < 10**17, ts_int // 1000,
                    ts_int // 1_000_000,
                ),
            ),
        ).astype('int64')

    log.warning("Unexpected timestamp column type %s, falling back to slow path", t)
    out = np.empty(len(ts_array), dtype='int64')
    for i, v in enumerate(ts_array.to_pylist()):
        if v is None:
            out[i] = 0
        elif hasattr(v, 'timestamp'):
            out[i] = int(v.timestamp() * 1000)
        else:
            try:
                out[i] = int(v)
            except (TypeError, ValueError):
                out[i] = 0
    return out


# ---------------------------------------------------------------------------
# Reading orderbook parquet → snapshots
# ---------------------------------------------------------------------------


def read_orderbook_parquet(path: Path, coin: str) -> list[dict]:
    if not path.exists():
        return []
    table = pq.read_table(path, columns=["block_time_ms", "bids", "asks"])
    block_times = table.column("block_time_ms").to_pylist()
    bids_col = table.column("bids").to_pylist()
    asks_col = table.column("asks").to_pylist()

    out: list[dict] = []
    for ts_ms, bids, asks in zip(block_times, bids_col, asks_col):
        if ts_ms is None or bids is None or asks is None:
            continue
        out.append({
            "ts_ms": int(ts_ms),
            "coin": coin,
            "bids": [(_safe_float(lvl["px"]), _safe_float(lvl["sz"]),
                      _safe_int(lvl.get("n"))) for lvl in bids],
            "asks": [(_safe_float(lvl["px"]), _safe_float(lvl["sz"]),
                      _safe_int(lvl.get("n"))) for lvl in asks],
        })
    out.sort(key=lambda f: f["ts_ms"])
    return out


# ---------------------------------------------------------------------------
# Reading fills parquet → trade list (VECTORIZED)
# ---------------------------------------------------------------------------


_POSSIBLE_TS_COLS = ["timestamp", "block_time_ms", "time_ms", "time", "ts_ms",
                     "timestamp_ms", "ts"]
_POSSIBLE_PX_COLS = ["price", "px"]
_POSSIBLE_SZ_COLS = ["size", "sz", "qty"]
_POSSIBLE_TID_COLS = ["trade_id", "tid", "hash"]
_POSSIBLE_BUILDER_COLS = ["builder"]
_POSSIBLE_BUILDER_FEE_COLS = ["builder_fee"]
_POSSIBLE_FEE_COLS = ["fee"]

# Filter column priority (v2): base_symbol works for HL native AND HIP-3 deployers.
# 'coin' is the raw market identifier (prefixed like 'xyz:NVDA' for HIP-3).
# 'symbol' is the legacy fallback.
_POSSIBLE_FILTER_COLS = ["base_symbol", "coin", "symbol"]


def _pick_col(table: pa.Table, candidates: list[str]) -> str | None:
    for c in candidates:
        if c in table.schema.names:
            return c
    return None


def read_fills_parquet(path: Path, coin: str) -> list[dict]:
    if not path.exists():
        return []
    table = pq.read_table(path)
    if table.num_rows == 0:
        return []

    # v2: prefer base_symbol (universal) over coin (HIP-3 prefixed) over symbol (legacy)
    filter_col = _pick_col(table, _POSSIBLE_FILTER_COLS)
    if filter_col:
        mask = pa.compute.equal(table.column(filter_col), pa.scalar(coin))
        table = table.filter(mask)
        if table.num_rows == 0:
            return []

    ts_col = _pick_col(table, _POSSIBLE_TS_COLS)
    px_col = _pick_col(table, _POSSIBLE_PX_COLS)
    sz_col = _pick_col(table, _POSSIBLE_SZ_COLS)
    tid_col = _pick_col(table, _POSSIBLE_TID_COLS)
    if not (ts_col and px_col and sz_col):
        log.warning("Fills schema missing essential cols in %s; have %s",
                    path.name, table.schema.names)
        return []

    side_col = "side" if "side" in table.schema.names else None
    builder_col = _pick_col(table, _POSSIBLE_BUILDER_COLS)
    builder_fee_col = _pick_col(table, _POSSIBLE_BUILDER_FEE_COLS)
    fee_col = _pick_col(table, _POSSIBLE_FEE_COLS)

    n = table.num_rows

    # === VECTORIZED column extraction ===
    ts_ms_arr = _read_timestamps_as_ms_array(table.column(ts_col))
    ts_ms_list = ts_ms_arr.tolist()

    px_list = table.column(px_col).to_pylist()
    sz_list = table.column(sz_col).to_pylist()
    tid_list = table.column(tid_col).to_pylist() if tid_col else [0] * n
    side_list = table.column(side_col).to_pylist() if side_col else [""] * n
    builder_list = table.column(builder_col).to_pylist() if builder_col else [""] * n
    builder_fee_list = (table.column(builder_fee_col).to_pylist()
                        if builder_fee_col else [0.0] * n)
    fee_list = table.column(fee_col).to_pylist() if fee_col else [0.0] * n

    # === BUILD records via list comprehension ===
    out = [
        {
            "ts_ms": ts_ms_list[i],
            "coin": coin,
            "side": str(side_list[i]) if side_list[i] is not None else "",
            "px": _safe_float(px_list[i]),
            "sz": _safe_float(sz_list[i]),
            "tid": _safe_int(tid_list[i]),
            "builder": str(builder_list[i]) if builder_list[i] is not None else "",
            "builder_fee": _safe_float(builder_fee_list[i]),
            "fee": _safe_float(fee_list[i]),
        }
        for i in range(n)
    ]
    out.sort(key=lambda t: t["ts_ms"])
    return out


# ---------------------------------------------------------------------------
# Sub-sampling Reservoir's 1-minute frames
# ---------------------------------------------------------------------------


def subsample_frames(frames: list[dict], sampling: SamplingConfig) -> list[dict]:
    if not frames:
        return []
    start_ms = int(sampling.start.timestamp() * 1000)
    end_ms = int(sampling.end.timestamp() * 1000)
    step_min = max(1, sampling.keep_every_n_minutes)
    out: list[dict] = []
    for f in frames:
        if f["ts_ms"] < start_ms or f["ts_ms"] > end_ms:
            continue
        if (f["ts_ms"] // 60_000) % step_min != 0:
            continue
        out.append(f)
    return out


# ---------------------------------------------------------------------------
# Per-snapshot derivations
# ---------------------------------------------------------------------------


def expand_book(frame: dict) -> Iterator[SnapshotRecord]:
    snap_id = _make_snap_id(frame["coin"], frame["ts_ms"])
    for level, (px, sz, n) in enumerate(frame["bids"]):
        yield SnapshotRecord(snap_id, frame["ts_ms"], frame["ts_ms"],
                             frame["coin"], "bid", level, px, sz, n)
    for level, (px, sz, n) in enumerate(frame["asks"]):
        yield SnapshotRecord(snap_id, frame["ts_ms"], frame["ts_ms"],
                             frame["coin"], "ask", level, px, sz, n)


def expand_trades(trades: list[dict], target_ts_ms: int, coin: str
                  ) -> Iterator[TradeRecord]:
    snap_id = _make_snap_id(coin, target_ts_ms)
    for t in trades:
        yield TradeRecord(
            snapshot_id=snap_id, coin=coin,
            ts_ms=t["ts_ms"], ts_offset_ms=t["ts_ms"] - target_ts_ms,
            side=t.get("side", ""),
            px=t["px"], sz=t["sz"], tid=t.get("tid", 0),
            builder=t.get("builder", ""),
            builder_fee=t.get("builder_fee", 0.0),
            fee=t.get("fee", 0.0),
        )


def compute_meta(frame: dict, n_trades_window: int) -> MetaRecord:
    bids = frame["bids"]
    asks = frame["asks"]
    coin = frame["coin"]
    ts_ms = frame["ts_ms"]
    snap_id = _make_snap_id(coin, ts_ms)

    if not bids or not asks:
        return MetaRecord(snap_id, coin, ts_ms, ts_ms, 0,
                          float("nan"), float("nan"), float("nan"),
                          0.0, 0.0, max(len(bids), len(asks)), n_trades_window)

    pb, qb, _ = bids[0]
    pa_, qa, _ = asks[0]
    mid = 0.5 * (pb + pa_)
    micro = (qa * pb + qb * pa_) / (qa + qb) if (qa + qb) > 0 else mid
    spread_bps = 1e4 * (pa_ - pb) / mid if mid > 0 else float("nan")

    return MetaRecord(
        snapshot_id=snap_id, coin=coin,
        target_ts_ms=ts_ms, actual_ts_ms=ts_ms, skew_ms=0,
        mid=mid, microprice=micro, spread_bps=spread_bps,
        top_bid_sz=qb, top_ask_sz=qa,
        book_levels=max(len(bids), len(asks)),
        n_trades_window=n_trades_window,
    )


# ---------------------------------------------------------------------------
# Trade-tape window slicing — uses bisect for O(log n) per call
# ---------------------------------------------------------------------------


def slice_trade_window(trades: list[dict], lo_ms: int, hi_ms: int) -> list[dict]:
    """Trades MUST be sorted by ts_ms (read_fills_parquet guarantees this)."""
    if not trades:
        return []
    import bisect
    lo = bisect.bisect_left(trades, lo_ms, key=lambda t: t["ts_ms"])
    hi = bisect.bisect_right(trades, hi_ms, key=lambda t: t["ts_ms"])
    return trades[lo:hi]
