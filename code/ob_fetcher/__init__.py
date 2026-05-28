"""ob_fetcher — Hydromancer Reservoir order-book fetcher for IS research."""

from .config import (
    AssetSpec,
    FetchConfig,
    ReservoirConfig,
    SamplingConfig,
    StorageConfig,
)
from .pipeline import run_pipeline

__all__ = [
    "AssetSpec",
    "FetchConfig",
    "ReservoirConfig",
    "SamplingConfig",
    "StorageConfig",
    "run_pipeline",
]

__version__ = "0.2.0"  # adapted to Reservoir as primary backend
