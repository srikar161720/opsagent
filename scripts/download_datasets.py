"""Download external datasets for OpsAgent evaluation.

Downloads RCAEval (RE1/RE2/RE3) and LogHub HDFS datasets.

Usage:
    poetry run python scripts/download_datasets.py --all
    poetry run python scripts/download_datasets.py --rcaeval
    poetry run python scripts/download_datasets.py --loghub
    poetry run python scripts/download_datasets.py --rcaeval --force
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def download_rcaeval(force: bool = False) -> None:
    """Download RCAEval RE1, RE2, RE3 datasets (~5 GB total).

    Uses RCAEval package's built-in download utilities.
    Downloads to data/RCAEval/re1/, re2/, re3/.
    """
    base_path = Path("data/RCAEval")

    # Check if already downloaded
    if not force:
        existing = []
        for variant, expected in [("re1", 375), ("re2", 270), ("re3", 90)]:
            variant_path = base_path / variant
            if variant_path.exists() and any(variant_path.iterdir()):
                existing.append(variant)
        if len(existing) == 3:
            print("RCAEval datasets already present — skipping. Use --force to re-download.")
            return

    try:
        from RCAEval.utility import (
            download_re1_dataset,
            download_re2_dataset,
            download_re3_dataset,
        )
    except ImportError:
        print("ERROR: RCAEval package not installed.")
        print("Install with: poetry install --with eval")
        sys.exit(1)

    print("Downloading RCAEval datasets (~5 GB total)...")
    print()

    print("[1/3] Downloading RE1 (375 cases, ~1.5 GB)...")
    download_re1_dataset()
    print("  RE1 download complete.")

    print("[2/3] Downloading RE2 (270 cases, ~2.0 GB)...")
    download_re2_dataset()
    print("  RE2 download complete.")

    print("[3/3] Downloading RE3 (90 cases, ~0.5 GB)...")
    download_re3_dataset()
    print("  RE3 download complete.")

    # Verify downloads
    print()
    print("Verifying RCAEval downloads...")
    for variant, expected_cases in [("re1", 375), ("re2", 270), ("re3", 90)]:
        variant_path = base_path / variant
        if variant_path.exists():
            # Count case directories (each case has a metadata.json)
            cases = list(variant_path.glob("*/metadata.json"))
            if not cases:
                # Try counting subdirectories
                cases_dirs = [d for d in variant_path.iterdir() if d.is_dir()]
                count = len(cases_dirs)
            else:
                count = len(cases)
            status = "OK" if count >= expected_cases else f"WARN (expected {expected_cases})"
            print(f"  {variant.upper()}: {count} cases — {status}")
        else:
            print(f"  {variant.upper()}: NOT FOUND — download may have failed")

    print()
    print("RCAEval download complete.")


def download_loghub_hdfs(force: bool = False) -> None:
    """Download LogHub HDFS dataset (~1 GB).

    The LogHub HDFS dataset must be downloaded manually from Zenodo since
    it requires accepting the license terms.

    Files needed: HDFS.log, anomaly_label.csv
    Place in: data/LogHub/HDFS/
    """
    hdfs_dir = Path("data/LogHub/HDFS")
    hdfs_dir.mkdir(parents=True, exist_ok=True)

    hdfs_log = hdfs_dir / "HDFS.log"
    anomaly_labels = hdfs_dir / "anomaly_label.csv"

    if not force and hdfs_log.exists() and anomaly_labels.exists():
        log_size_mb = hdfs_log.stat().st_size / (1024 * 1024)
        print(f"LogHub HDFS already present (HDFS.log: {log_size_mb:.0f} MB) — skipping.")
        return

    print("=" * 60)
    print("LogHub HDFS — Manual Download Required")
    print("=" * 60)
    print()
    print("The LogHub HDFS dataset requires manual download from Zenodo.")
    print()
    print("Steps:")
    print("  1. Visit: https://zenodo.org/record/8196385")
    print("  2. Download the following files:")
    print("     - HDFS.log (~1.5 GB)")
    print("     - anomaly_label.csv")
    print(f"  3. Place both files in: {hdfs_dir.resolve()}")
    print()
    print("After downloading, re-run this script to verify:")
    print("  poetry run python scripts/download_datasets.py --loghub")
    print()

    # Check if partially downloaded
    if hdfs_log.exists() and not anomaly_labels.exists():
        print("NOTE: HDFS.log found but anomaly_label.csv is missing.")
    elif anomaly_labels.exists() and not hdfs_log.exists():
        print("NOTE: anomaly_label.csv found but HDFS.log is missing.")


def verify_all() -> None:
    """Print a summary of all dataset availability."""
    print()
    print("=" * 60)
    print("Dataset Status Summary")
    print("=" * 60)
    print()

    # RCAEval
    rcaeval_base = Path("data/RCAEval")
    for variant, expected in [("re1", 375), ("re2", 270), ("re3", 90)]:
        variant_path = rcaeval_base / variant
        if variant_path.exists():
            dirs = [d for d in variant_path.iterdir() if d.is_dir()]
            status = f"{len(dirs)} cases"
        else:
            status = "NOT DOWNLOADED"
        print(f"  RCAEval {variant.upper():>3}: {status}")

    # LogHub HDFS
    hdfs_dir = Path("data/LogHub/HDFS")
    hdfs_log = hdfs_dir / "HDFS.log"
    anomaly_labels = hdfs_dir / "anomaly_label.csv"
    if hdfs_log.exists() and anomaly_labels.exists():
        size_mb = hdfs_log.stat().st_size / (1024 * 1024)
        status = f"OK (HDFS.log: {size_mb:.0f} MB)"
    elif hdfs_log.exists():
        status = "PARTIAL (missing anomaly_label.csv)"
    else:
        status = "NOT DOWNLOADED"
    print(f"  LogHub HDFS : {status}")

    # OTel Demo baseline
    metadata_path = Path("data/baseline/metadata.json")
    if metadata_path.exists():
        import json

        with open(metadata_path) as f:
            meta = json.load(f)
        status = meta.get("status", "unknown")
        snapshots = meta.get("metric_snapshots", 0)
        print(f"  OTel Baseline: {status} ({snapshots} snapshots)")
    else:
        print("  OTel Baseline: NOT COLLECTED")

    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download external datasets for OpsAgent."
    )
    parser.add_argument(
        "--rcaeval",
        action="store_true",
        help="Download RCAEval RE1, RE2, RE3 datasets (~5 GB)",
    )
    parser.add_argument(
        "--loghub",
        action="store_true",
        help="Download LogHub HDFS dataset (~1 GB, manual)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Download all datasets (equivalent to --rcaeval --loghub)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download even if files already exist",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show download status for all datasets without downloading",
    )

    args = parser.parse_args()

    if args.status:
        verify_all()
        return

    if not (args.rcaeval or args.loghub or args.all):
        parser.print_help()
        print("\nError: Specify at least one of --rcaeval, --loghub, or --all")
        sys.exit(1)

    if args.all or args.rcaeval:
        download_rcaeval(force=args.force)
        print()

    if args.all or args.loghub:
        download_loghub_hdfs(force=args.force)
        print()

    verify_all()


if __name__ == "__main__":
    main()
