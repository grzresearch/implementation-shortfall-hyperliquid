"""
Orchestrator — runs Modul 4 → Modul 5 → Modul 6 in sequence.

Usage:
    python run_is_pipeline.py
    python run_is_pipeline.py --skip-simulator   # re-aggregate only
    python run_is_pipeline.py --coins BTC ETH    # subset
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("pipeline")


def run_step(name: str, script: str, extra_args: list) -> bool:
    log.info("")
    log.info("=" * 60)
    log.info("STEP: %s  (%s)", name, script)
    log.info("=" * 60)
    t0 = time.perf_counter()
    cmd = [sys.executable, script, *extra_args]
    result = subprocess.run(cmd, check=False)
    elapsed = time.perf_counter() - t0
    if result.returncode != 0:
        log.error("%s failed (exit %d) after %.1fs",
                  name, result.returncode, elapsed)
        return False
    log.info("%s done in %.1fs", name, elapsed)
    return True


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--data-dir",
                   default=r"C:\Users\grego\Downloads\fetcher\data")
    p.add_argument("--coins", nargs="*", default=None)
    p.add_argument("--skip-simulator", action="store_true")
    p.add_argument("--skip-decomp", action="store_true")
    p.add_argument("--skip-aggregator", action="store_true")
    args = p.parse_args()

    here = Path(__file__).parent
    sim_script = str(here / "simulator.py")
    is_script = str(here / "is_decomp.py")
    agg_script = str(here / "aggregator.py")

    common = ["--data-dir", args.data_dir]
    sim_args = common[:]
    if args.coins:
        sim_args += ["--coins", *args.coins]

    t_all = time.perf_counter()

    if not args.skip_simulator:
        if not run_step("Modul 4 — Simulator", sim_script, sim_args):
            return 1
    else:
        log.info("Skipping simulator")

    if not args.skip_decomp:
        if not run_step("Modul 5 — IS Decomposer", is_script, common):
            return 1
    else:
        log.info("Skipping IS decomposer")

    if not args.skip_aggregator:
        if not run_step("Modul 6 — Aggregator", agg_script, common):
            return 1
    else:
        log.info("Skipping aggregator")

    log.info("")
    log.info("=" * 60)
    log.info("PIPELINE COMPLETE — total %.1f min", (time.perf_counter() - t_all)/60)
    log.info("=" * 60)
    log.info("Results in: %s", Path(args.data_dir))
    log.info("  simulator/runs.parquet           — raw simulator output")
    log.info("  is_results/is_decomp.parquet     — IS components per run")
    log.info("  aggregator/plots/*.png           — paper figures")
    log.info("  aggregator/tables/*.tex          — paper tables (LaTeX)")
    log.info("  aggregator/summary_stats.csv     — all aggregated stats")
    return 0


if __name__ == "__main__":
    sys.exit(main())
