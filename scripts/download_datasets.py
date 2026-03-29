"""Download external datasets for OpsAgent evaluation.

Downloads RCAEval (RE1/RE2/RE3) and LogHub HDFS datasets from Zenodo.

Usage:
    poetry run python scripts/download_datasets.py --all
    poetry run python scripts/download_datasets.py --rcaeval
    poetry run python scripts/download_datasets.py --loghub
    poetry run python scripts/download_datasets.py --rcaeval --force
    poetry run python scripts/download_datasets.py --status
"""

from __future__ import annotations

import argparse
import io
import sys
import zipfile
from pathlib import Path
from urllib.request import Request, urlopen

# Zenodo record IDs
_RCAEVAL_RECORD = "14590730"
_LOGHUB_RECORD = "8196385"

# RCAEval ZIP files: (filename, variant directory, expected cases per system)
_RCAEVAL_FILES: list[tuple[str, str]] = [
    ("RE1-OB.zip", "re1"),
    ("RE1-SS.zip", "re1"),
    ("RE1-TT.zip", "re1"),
    ("RE2-OB.zip", "re2"),
    ("RE2-SS.zip", "re2"),
    ("RE2-TT.zip", "re2"),
    ("RE3-OB.zip", "re3"),
    ("RE3-SS.zip", "re3"),
    ("RE3-TT.zip", "re3"),
]

# True case counts (verified from actual data)
_RCAEVAL_EXPECTED: dict[str, int] = {"re1": 375, "re2": 271, "re3": 90}


def _zenodo_url(record_id: str, filename: str) -> str:
    """Build a Zenodo file download URL."""
    return f"https://zenodo.org/api/records/{record_id}/files/{filename}/content"


def _download_and_extract_zip(url: str, extract_to: Path, filename: str) -> None:
    """Download a ZIP file from a URL and extract it to a directory."""
    print(f"    Downloading {filename}...", end="", flush=True)
    req = Request(url, headers={"User-Agent": "OpsAgent/1.0"})
    with urlopen(req) as resp:
        data = resp.read()
    size_mb = len(data) / (1024 * 1024)
    print(f" {size_mb:.1f} MB", flush=True)

    print(f"    Extracting to {extract_to}/...", flush=True)
    extract_to.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        zf.extractall(extract_to)


def _count_cases(variant_path: Path) -> int:
    """Count RCAEval cases by counting inject_time.txt files."""
    return len(list(variant_path.rglob("inject_time.txt")))


def download_rcaeval(force: bool = False) -> None:
    """Download RCAEval RE1, RE2, RE3 datasets (~4.9 GB total).

    Downloads from Zenodo (DOI: 10.5281/zenodo.14590730).
    Extracts to data/RCAEval/re1/, re2/, re3/.
    """
    base_path = Path("data/RCAEval")

    if not force:
        existing = []
        for variant in ("re1", "re2", "re3"):
            variant_path = base_path / variant
            if variant_path.exists() and _count_cases(variant_path) > 0:
                existing.append(variant)
        if len(existing) == 3:
            print("RCAEval datasets already present — skipping. Use --force to re-download.")
            return

    print("Downloading RCAEval datasets from Zenodo (~4.9 GB total)...")
    print()

    for i, (filename, variant) in enumerate(_RCAEVAL_FILES, 1):
        variant_path = base_path / variant
        system_name = filename.replace(".zip", "")
        system_path = variant_path / system_name

        if not force and system_path.exists() and any(system_path.iterdir()):
            print(f"  [{i}/9] {filename} — already present, skipping.")
            continue

        print(f"  [{i}/9] {filename}")
        url = _zenodo_url(_RCAEVAL_RECORD, filename)
        _download_and_extract_zip(url, variant_path, filename)

    print()
    print("Verifying RCAEval downloads...")
    for variant, expected in _RCAEVAL_EXPECTED.items():
        variant_path = base_path / variant
        if variant_path.exists():
            count = _count_cases(variant_path)
            status = "OK" if count >= expected else f"WARN (expected {expected})"
            print(f"  {variant.upper()}: {count} cases — {status}")
        else:
            print(f"  {variant.upper()}: NOT FOUND — download may have failed")

    print()
    print("RCAEval download complete.")


def download_loghub_hdfs(force: bool = False) -> None:
    """Download LogHub HDFS dataset (~178 MB compressed, ~1.5 GB extracted).

    Downloads HDFS_v1.zip from Zenodo (DOI: 10.5281/zenodo.8196385).
    Extracts HDFS.log and anomaly_label.csv to data/LogHub/HDFS/.
    """
    hdfs_dir = Path("data/LogHub/HDFS")
    hdfs_dir.mkdir(parents=True, exist_ok=True)

    hdfs_log = hdfs_dir / "HDFS.log"
    anomaly_labels = hdfs_dir / "anomaly_label.csv"

    if not force and hdfs_log.exists() and anomaly_labels.exists():
        log_size_mb = hdfs_log.stat().st_size / (1024 * 1024)
        print(f"LogHub HDFS already present (HDFS.log: {log_size_mb:.0f} MB) — skipping.")
        return

    print("Downloading LogHub HDFS from Zenodo (~178 MB compressed)...")
    url = _zenodo_url(_LOGHUB_RECORD, "HDFS_v1.zip")

    print("    Downloading HDFS_v1.zip...", end="", flush=True)
    req = Request(url, headers={"User-Agent": "OpsAgent/1.0"})
    with urlopen(req) as resp:
        data = resp.read()
    size_mb = len(data) / (1024 * 1024)
    print(f" {size_mb:.1f} MB", flush=True)

    print("    Extracting HDFS.log and anomaly_label.csv...", flush=True)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        # Extract only the files we need
        for member in zf.namelist():
            basename = Path(member).name
            if basename == "HDFS.log":
                with zf.open(member) as src, open(hdfs_log, "wb") as dst:
                    while chunk := src.read(8192):
                        dst.write(chunk)
            elif basename == "anomaly_label.csv":
                with zf.open(member) as src, open(anomaly_labels, "wb") as dst:
                    while chunk := src.read(8192):
                        dst.write(chunk)

    # Verify
    if hdfs_log.exists() and anomaly_labels.exists():
        log_size_mb = hdfs_log.stat().st_size / (1024 * 1024)
        with open(anomaly_labels) as fh:
            label_lines = sum(1 for _ in fh) - 1  # subtract header
        print(f"    HDFS.log: {log_size_mb:.0f} MB")
        print(f"    anomaly_label.csv: {label_lines} blocks")
        print()
        print("LogHub HDFS download complete.")
    else:
        print("ERROR: Expected files not found in HDFS_v1.zip.")
        sys.exit(1)


def verify_all() -> None:
    """Print a summary of all dataset availability."""
    print()
    print("=" * 60)
    print("Dataset Status Summary")
    print("=" * 60)
    print()

    # RCAEval
    rcaeval_base = Path("data/RCAEval")
    for variant in _RCAEVAL_EXPECTED:
        variant_path = rcaeval_base / variant
        if variant_path.exists():
            count = _count_cases(variant_path)
            systems = [d.name for d in sorted(variant_path.iterdir()) if d.is_dir()]
            status = f"{count} cases across {', '.join(systems)}"
        else:
            status = "NOT DOWNLOADED"
        print(f"  RCAEval {variant.upper():>3}: {status}")

    # LogHub HDFS
    hdfs_dir = Path("data/LogHub/HDFS")
    hdfs_log = hdfs_dir / "HDFS.log"
    anomaly_labels = hdfs_dir / "anomaly_label.csv"
    if hdfs_log.exists() and anomaly_labels.exists():
        size_mb = hdfs_log.stat().st_size / (1024 * 1024)
        with open(anomaly_labels) as fh:
            label_count = sum(1 for _ in fh) - 1
        status = f"OK (HDFS.log: {size_mb:.0f} MB, {label_count} blocks)"
    elif hdfs_log.exists():
        status = "PARTIAL (missing anomaly_label.csv)"
    elif anomaly_labels.exists():
        status = "PARTIAL (missing HDFS.log)"
    else:
        status = "NOT DOWNLOADED"
    print(f"  LogHub HDFS : {status}")

    # OTel Demo baseline
    metadata_path = Path("data/baseline/metadata.json")
    if metadata_path.exists():
        import json

        with open(metadata_path) as f:
            meta = json.load(f)
        status_val = meta.get("status", "unknown")
        snapshots = meta.get("metric_snapshots", 0)
        print(f"  OTel Baseline: {status_val} ({snapshots} snapshots)")
    else:
        print("  OTel Baseline: NOT COLLECTED")

    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Download external datasets for OpsAgent.")
    parser.add_argument(
        "--rcaeval",
        action="store_true",
        help="Download RCAEval RE1, RE2, RE3 datasets (~4.9 GB)",
    )
    parser.add_argument(
        "--loghub",
        action="store_true",
        help="Download LogHub HDFS dataset (~178 MB compressed)",
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
