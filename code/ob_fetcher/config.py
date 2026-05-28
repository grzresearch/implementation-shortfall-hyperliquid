"""Configuration objects for the Hydromancer Reservoir order-book fetcher.

All configuration is exposed as plain dataclasses so the user can edit them
directly in Python (no YAML/CLI). Intended usage:

    from ob_fetcher import FetchConfig, AssetSpec, run_pipeline
    cfg = FetchConfig(...)
    run_pipeline(cfg)

Reservoir gives us pre-baked 1-minute, 20-level L2 snapshots in Parquet —
the heavy lifting (decompression, parsing, snapshot extraction) is gone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal


# ---------------------------------------------------------------------------
# Asset specification
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AssetSpec:
    """A single asset to fetch from Reservoir.

    `dex` selects the Reservoir partition prefix:
      - "hyperliquid"  → main HL spot/perp markets
      - "xyz"          → Trade.xyz HIP-3 (e.g. coin="NVDA")
      - "cash"         → Dreamcash HIP-3
      - "hyna"         → HyENA HIP-3
      - "flx"          → Felix HIP-3
      - "km"           → Markets.xyz HIP-3
      - "vntl"         → Ventuals HIP-3

    `asset_class` is "perps" or "spot".
    """

    coin: str
    dex: str = "hyperliquid"
    asset_class: Literal["perps", "spot"] = "perps"
    description: str = ""

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.dex}:{self.coin}({self.asset_class})"


# ---------------------------------------------------------------------------
# Sampling configuration
# ---------------------------------------------------------------------------


@dataclass
class SamplingConfig:
    """Controls the timestamp grid used for downstream IS analysis.

    Reservoir itself ships at 1-minute cadence. We therefore do NOT need a
    nearest-snapshot search — we just optionally downsample by keeping every
    Nth row in the Reservoir parquet, controlled by `keep_every_n_minutes`.
    """

    # Study window (UTC). Inclusive on both ends.
    start: datetime = datetime(2025, 8, 1, 0, 0, 0, tzinfo=timezone.utc)
    end: datetime = datetime(2026, 5, 1, 0, 0, 0, tzinfo=timezone.utc)

    # How dense the kept grid should be. Reservoir is 1-min native, so:
    #   1  → keep every snapshot (full Reservoir cadence)
    #   15 → keep every 15-min snapshot (matches the project plan default)
    #   60 → keep every hourly snapshot
    keep_every_n_minutes: int = 15

    # Trade-tape window around each kept snapshot, used by the IS simulator
    # for TWAP replay and opportunity-cost calculation. Keep these moderate
    # for Phase 1; widen later if testing 30-min+ TWAP variants.
    trade_window_minutes_before: int = 5
    trade_window_minutes_after: int = 30


# ---------------------------------------------------------------------------
# Storage configuration
# ---------------------------------------------------------------------------


@dataclass
class StorageConfig:
    """Where fetched data lands on local disk."""

    root: Path = Path("./data")

    raw_subdir: str = "raw"            # raw Reservoir parquet downloads (cache)
    parquet_subdir: str = "parquet"    # normalized output (book/trades/meta)
    checkpoint_subdir: str = "ckpt"    # resume state

    # Parquet output settings
    parquet_compression: Literal["zstd", "snappy", "gzip", "lz4"] = "zstd"
    parquet_row_group_size: int = 100_000

    keep_raw: bool = True              # set False after validation to save disk

    def raw_dir(self) -> Path:
        return self.root / self.raw_subdir

    def parquet_dir(self) -> Path:
        return self.root / self.parquet_subdir

    def checkpoint_dir(self) -> Path:
        return self.root / self.checkpoint_subdir

    def ensure_dirs(self) -> None:
        for d in (self.raw_dir(), self.parquet_dir(), self.checkpoint_dir()):
            d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Reservoir backend configuration
# ---------------------------------------------------------------------------


@dataclass
class ReservoirConfig:
    """Configuration for Hydromancer's Reservoir bucket.

    Reservoir is a public requester-pays AWS S3 bucket in ap-northeast-1.
    No Hydromancer API key is required — only AWS credentials with billing
    enabled. Path layout:

        s3://hydromancer-reservoir/
          by_dex/{dex}/orderbook/1m/{asset_class}/date=YYYY-MM-DD/{coin}.parquet
          by_dex/{dex}/fills/perp/all/date=YYYY-MM-DD/fills.parquet
          by_dex/{dex}/fills/spot/all/date=YYYY-MM-DD/fills.parquet

    Parquet schema for orderbook (per Hydromancer schema-reference):
        block_time_ms : int64
        block_number  : uint64
        bids          : list<struct<px: string, sz: string, n: uint32>>  (highest first)
        asks          : list<struct<px: string, sz: string, n: uint32>>  (lowest first)
    """

    bucket: str = "hydromancer-reservoir"
    aws_region: str = "ap-northeast-1"
    requester_pays: bool = True

    # Path templates (with {placeholders}); only edit if Hydromancer changes layout
    orderbook_path_template: str = (
        "by_dex/{dex}/orderbook/1m/{asset_class}/date={date}/{coin}.parquet"
    )
    fills_path_template: str = (
        "by_dex/{dex}/fills/{asset_class_singular}/all/date={date}/fills.parquet"
    )

    # boto3 client tuning
    max_pool_connections: int = 32
    connect_timeout_s: int = 10
    read_timeout_s: int = 120
    retries_max_attempts: int = 5

    # Earliest date with reliable data (per Reservoir docs: 2025-07-28)
    data_start_date: date = date(2025, 7, 28)


# ---------------------------------------------------------------------------
# Top-level fetch configuration
# ---------------------------------------------------------------------------


@dataclass
class FetchConfig:
    """Top-level config bundling assets, sampling, storage, backend.

    This is the single object the user constructs and hands to `run_pipeline`.
    """

    assets: list[AssetSpec] = field(default_factory=list)
    sampling: SamplingConfig = field(default_factory=SamplingConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    reservoir: ReservoirConfig = field(default_factory=ReservoirConfig)

    # Concurrency
    max_concurrent_downloads: int = 8

    # Whether to also fetch fills (needed for trade-window slicing & IS
    # opportunity-cost calc). Set False if you only want book snapshots.
    fetch_fills: bool = True

    # Diagnostics
    verbose: bool = True
    dry_run: bool = False  # if True, plan but don't actually download

    def validate(self) -> None:
        if not self.assets:
            raise ValueError("FetchConfig.assets is empty — nothing to fetch.")
        if self.sampling.end <= self.sampling.start:
            raise ValueError("Sampling window: end must be strictly after start.")
        if self.sampling.start.date() < self.reservoir.data_start_date:
            raise ValueError(
                f"Reservoir data starts {self.reservoir.data_start_date}; "
                f"requested start {self.sampling.start.date()} is too early."
            )
        if self.sampling.keep_every_n_minutes < 1:
            raise ValueError("keep_every_n_minutes must be >= 1.")

    def summary(self) -> str:
        n_assets = len(self.assets)
        days = (self.sampling.end.date() - self.sampling.start.date()).days
        coins = ", ".join(f"{a.dex}:{a.coin}" for a in self.assets)
        return (
            f"FetchConfig:\n"
            f"  assets:                {n_assets}  [{coins}]\n"
            f"  window:                {self.sampling.start.date()} → {self.sampling.end.date()}  ({days} days)\n"
            f"  cadence kept:          every {self.sampling.keep_every_n_minutes} min "
            f"(Reservoir native = 1-min)\n"
            f"  trade window:          [-{self.sampling.trade_window_minutes_before}, "
            f"+{self.sampling.trade_window_minutes_after}] min\n"
            f"  fetch fills:           {self.fetch_fills}\n"
            f"  bucket:                s3://{self.reservoir.bucket}  ({self.reservoir.aws_region})\n"
            f"  storage root:          {self.storage.root.resolve()}\n"
            f"  parallel downloads:    {self.max_concurrent_downloads}\n"
            f"  dry run:               {self.dry_run}"
        )
