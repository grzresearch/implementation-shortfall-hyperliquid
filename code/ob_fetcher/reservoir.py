"""Reservoir S3 client — download daily Parquet files, requester-pays."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from .config import AssetSpec, ReservoirConfig

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Boto3 client construction (lazy import)
# ---------------------------------------------------------------------------


def _make_s3_client(cfg: ReservoirConfig):
    import boto3
    from botocore.config import Config as BotoConfig

    bcfg = BotoConfig(
        region_name=cfg.aws_region,
        max_pool_connections=cfg.max_pool_connections,
        connect_timeout=cfg.connect_timeout_s,
        read_timeout=cfg.read_timeout_s,
        retries={"max_attempts": cfg.retries_max_attempts, "mode": "standard"},
    )
    return boto3.client("s3", config=bcfg)


# ---------------------------------------------------------------------------
# Path construction
# ---------------------------------------------------------------------------


_ASSET_CLASS_SINGULAR = {"perps": "perp", "spot": "spot"}


def orderbook_key(cfg: ReservoirConfig, asset: AssetSpec, dt: date) -> str:
    return cfg.orderbook_path_template.format(
        dex=asset.dex,
        asset_class=asset.asset_class,
        date=dt.isoformat(),
        coin=asset.coin,
    )


def fills_key(cfg: ReservoirConfig, asset: AssetSpec, dt: date) -> str:
    return cfg.fills_path_template.format(
        dex=asset.dex,
        asset_class_singular=_ASSET_CLASS_SINGULAR[asset.asset_class],
        date=dt.isoformat(),
    )


def dates_in_window(start: datetime, end: datetime) -> Iterator[date]:
    """Yield every UTC date in [start.date(), end.date()] inclusive."""
    cur = start.astimezone(timezone.utc).date()
    last = end.astimezone(timezone.utc).date()
    while cur <= last:
        yield cur
        cur += timedelta(days=1)


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------


@dataclass
class DownloadResult:
    key: str
    bytes_downloaded: int
    local_path: Path | None
    skipped: bool = False
    error: str | None = None


class ReservoirClient:
    """Thin wrapper around boto3 with requester-pays + on-disk cache."""

    def __init__(self, cfg: ReservoirConfig, raw_dir: Path):
        self.cfg = cfg
        self.raw_dir = raw_dir
        self._s3 = None  # lazy

    @property
    def s3(self):
        if self._s3 is None:
            self._s3 = _make_s3_client(self.cfg)
        return self._s3

    def _local_cache_path(self, key: str) -> Path:
        return self.raw_dir / key

    def download(self, key: str, *, force: bool = False) -> DownloadResult:
        """Download a single S3 object to local cache. Idempotent."""
        local = self._local_cache_path(key)
        if local.exists() and not force:
            return DownloadResult(
                key=key, bytes_downloaded=local.stat().st_size,
                local_path=local, skipped=True,
            )

        local.parent.mkdir(parents=True, exist_ok=True)
        extra: dict[str, Any] = {}
        if self.cfg.requester_pays:
            extra["RequestPayer"] = "requester"

        try:
            obj = self.s3.get_object(Bucket=self.cfg.bucket, Key=key, **extra)
            body = obj["Body"].read()
        except Exception as e:  # noqa: BLE001
            return DownloadResult(
                key=key, bytes_downloaded=0, local_path=None,
                error=f"{type(e).__name__}: {e}",
            )

        # atomic write to avoid leaving partial files on crash
        tmp = local.with_suffix(local.suffix + ".tmp")
        tmp.write_bytes(body)
        tmp.replace(local)
        return DownloadResult(key=key, bytes_downloaded=len(body), local_path=local)

    def head(self, key: str) -> bool:
        """Cheap existence check — useful for finding missing dates."""
        extra: dict[str, Any] = {}
        if self.cfg.requester_pays:
            extra["RequestPayer"] = "requester"
        try:
            self.s3.head_object(Bucket=self.cfg.bucket, Key=key, **extra)
            return True
        except Exception:  # noqa: BLE001
            return False
