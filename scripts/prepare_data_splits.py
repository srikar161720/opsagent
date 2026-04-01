"""Prepare train/val data splits for LSTM-AE pretraining and fine-tuning.

Usage:
    poetry run python scripts/prepare_data_splits.py --hdfs
    poetry run python scripts/prepare_data_splits.py --otel
    poetry run python scripts/prepare_data_splits.py --all
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np


def prepare_hdfs_splits(
    data_dir: str = "data/LogHub/HDFS/",
    output_dir: str = "data/splits/hdfs/",
    val_ratio: float = 0.2,
) -> None:
    """Parse LogHub HDFS and create train/val splits for LSTM-AE pretraining."""
    from src.preprocessing.log_parser import LogParser
    from src.preprocessing.loghub_preprocessor import (
        LogHubHDFSPreprocessor,
        create_hdfs_splits,
    )

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    print("Initializing LogParser...")
    parser = LogParser(persistence_path="models/drain3/")

    print(f"Initializing LogHubHDFSPreprocessor (data_dir={data_dir})...")
    preprocessor = LogHubHDFSPreprocessor(
        data_dir=data_dir,
        seq_length=10,
        parser=parser,
    )

    print("Parsing HDFS.log (this may take several minutes for 11M+ lines)...")
    preprocessor.parse()
    print(f"  Blocks found: {len(preprocessor._block_sequences)}")
    print(f"  Templates discovered: {preprocessor.num_templates}")

    print(f"Creating train/val splits (val_ratio={val_ratio})...")
    splits = create_hdfs_splits(preprocessor, val_ratio=val_ratio)

    train_path = out / "train.npy"
    val_path = out / "val.npy"
    meta_path = out / "metadata.json"

    np.save(str(train_path), splits["train"])
    np.save(str(val_path), splits["val"])

    metadata = {
        "input_dim": splits["input_dim"],
        "train_samples": len(splits["train"]),
        "val_samples": len(splits["val"]),
        "sequence_length": 10,
        "num_templates": splits["input_dim"],
        "val_ratio": val_ratio,
    }
    meta_path.write_text(json.dumps(metadata, indent=2))

    print(f"\nSaved to {out}/:")
    print(f"  train.npy:     {splits['train'].shape}")
    print(f"  val.npy:       {splits['val'].shape}")
    print(f"  metadata.json: input_dim={splits['input_dim']}")

    # Save parser state for reuse in fine-tuning
    parser.save()
    print("  Drain3 state saved to models/drain3/")


def prepare_otel_splits() -> None:
    """Placeholder for OTel Demo data splits.

    OTel Demo log splits require a configured log shipper (Promtail → Kafka),
    which is not yet set up. This will be implemented in Phase 4 when the
    real-time pipeline is running.
    """
    print("OTel Demo splits: SKIPPED")
    print("  Reason: No log shipper configured yet (Promtail → Kafka).")
    print("  OTel Demo splits will be available after Phase 4 infrastructure setup.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prepare train/val data splits for LSTM-AE training."
    )
    parser.add_argument(
        "--hdfs",
        action="store_true",
        help="Create HDFS splits for pretraining",
    )
    parser.add_argument(
        "--otel",
        action="store_true",
        help="Create OTel Demo splits for fine-tuning",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Create all available splits",
    )
    parser.add_argument(
        "--val-ratio",
        type=float,
        default=0.2,
        help="Validation set ratio (default: 0.2)",
    )

    args = parser.parse_args()

    if not any([args.hdfs, args.otel, args.all]):
        parser.print_help()
        return

    if args.hdfs or args.all:
        prepare_hdfs_splits(val_ratio=args.val_ratio)

    if args.otel or args.all:
        prepare_otel_splits()


if __name__ == "__main__":
    main()
