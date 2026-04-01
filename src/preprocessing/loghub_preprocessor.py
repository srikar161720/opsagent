"""LogHub HDFS preprocessor for LSTM-Autoencoder pretraining.

Workflow:
  1. Parse HDFS.log line-by-line using the shared Drain3 LogParser.
  2. Group extracted template IDs by block ID (blk_<id>).
  3. Load block-level anomaly labels from anomaly_label.csv.
  4. Convert block template-ID lists into fixed-length integer sequences.
  5. Expose normal sequences for pretraining; labeled sequences for benchmarking.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split

from src.preprocessing.log_parser import LogParser


class LogHubHDFSPreprocessor:
    """Preprocess LogHub HDFS dataset for LSTM-Autoencoder pretraining.

    The shared ``LogParser`` instance ensures template IDs are consistent
    between HDFS pretraining and OTel Demo fine-tuning.
    """

    BLOCK_PATTERN = re.compile(r"(blk_-?\d+)")

    def __init__(
        self,
        data_dir: str,
        seq_length: int = 10,
        parser: LogParser | None = None,
    ) -> None:
        """
        Args:
            data_dir:   Path to directory containing HDFS.log and anomaly_label.csv.
            seq_length: Fixed length of each output sequence (zero-padded if shorter).
            parser:     Shared LogParser instance. If None, a new one is created
                        (NOT recommended — pass the shared instance to maintain vocabulary).
        """
        self.data_dir = Path(data_dir)
        self.seq_length = seq_length
        self.parser = parser if parser is not None else LogParser()

        self._block_sequences: dict[str, list[int]] = {}
        self._labels: dict[str, int] = {}
        self._parsed: bool = False

    # ── Public API ───────────────────────────────────────────────────────

    def parse(self) -> None:
        """Parse HDFS.log and load labels. Must be called once before get_* methods."""
        self._parse_logs()
        self._load_labels()
        self._parsed = True

    def get_normal_sequences(self) -> np.ndarray:
        """Return fixed-length sequences from normal (non-anomalous) blocks.

        Returns:
            np.ndarray of shape (N, seq_length), dtype int32
        """
        self._ensure_parsed()
        return self._build_sequences(label_filter=0)

    def get_anomalous_sequences(self) -> np.ndarray:
        """Return fixed-length sequences from anomalous blocks.

        Returns:
            np.ndarray of shape (M, seq_length), dtype int32
        """
        self._ensure_parsed()
        return self._build_sequences(label_filter=1)

    def get_labeled_sequences(self) -> tuple[np.ndarray, np.ndarray]:
        """Return ALL sequences with binary labels for HDFS benchmark evaluation.

        Returns:
            Tuple of:
              sequences: np.ndarray shape (N, seq_length)
              labels:    np.ndarray shape (N,)  — 0=normal, 1=anomaly
        """
        self._ensure_parsed()
        normal = self._build_sequences(label_filter=0)
        anomalous = self._build_sequences(label_filter=1)
        sequences = np.vstack([normal, anomalous])
        labels = np.concatenate([
            np.zeros(len(normal), dtype=int),
            np.ones(len(anomalous), dtype=int),
        ])
        return sequences, labels

    @property
    def num_templates(self) -> int:
        """Number of unique Drain3 templates discovered across HDFS logs."""
        self._ensure_parsed()
        return self.parser.num_templates

    # ── Internal helpers ─────────────────────────────────────────────────

    def _parse_logs(self) -> None:
        """Read HDFS.log line-by-line; group template IDs by block ID."""
        log_path = self.data_dir / "HDFS.log"
        block_logs: dict[str, list[int]] = defaultdict(list)

        # HDFS log format: "081109 203615 148 INFO dfs.DataNode$PacketResponder: blk_-..."
        # Strip the structured header before passing to Drain3.
        hdfs_header_pattern = re.compile(
            r"^\d{6}\s+\d{6}\s+\d+\s+\w+\s+[\w.$]+:\s*"
        )

        with open(log_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue

                # Extract block ID from the original line before header stripping
                block_ids = self.BLOCK_PATTERN.findall(line)
                # Strip structured header; feed only free-text message to Drain3
                clean_line = hdfs_header_pattern.sub("", line)
                template_id, _ = self.parser.parse(clean_line)

                for blk_id in block_ids:
                    block_logs[blk_id].append(template_id)

        self._block_sequences = dict(block_logs)

    def _load_labels(self) -> None:
        """Load block-level anomaly labels from anomaly_label.csv.

        Expected CSV columns: BlockId, Label
        Label values: "Normal" → 0, "Anomaly" → 1
        """
        label_path = self.data_dir / "anomaly_label.csv"
        df = pd.read_csv(label_path)

        # Handle column naming variations across LogHub dataset versions
        label_col = "Label" if "Label" in df.columns else df.columns[-1]
        id_col = "BlockId" if "BlockId" in df.columns else df.columns[0]

        self._labels = {
            row[id_col]: 0 if str(row[label_col]).strip() == "Normal" else 1
            for _, row in df.iterrows()
        }

    def _build_sequences(self, label_filter: int) -> np.ndarray:
        """Convert block template-ID lists into fixed-length sequences.

        Blocks shorter than seq_length are zero-padded on the LEFT.
        Blocks longer than seq_length are chunked; the last chunk is padded.
        """
        sequences: list[list[int]] = []
        for block_id, template_ids in self._block_sequences.items():
            if self._labels.get(block_id, 0) != label_filter:
                continue

            for start in range(0, max(1, len(template_ids)), self.seq_length):
                chunk = template_ids[start : start + self.seq_length]
                if len(chunk) < self.seq_length:
                    chunk = [0] * (self.seq_length - len(chunk)) + chunk  # left-pad
                sequences.append(chunk)

        return np.array(sequences, dtype=np.int32)

    def _ensure_parsed(self) -> None:
        if not self._parsed:
            raise RuntimeError("Call parse() before accessing sequences.")


# ── Dataset split helpers ─────────────────────────────────────────────────


def create_hdfs_splits(
    preprocessor: LogHubHDFSPreprocessor, val_ratio: float = 0.2
) -> dict:
    """Create train/val split from LogHub HDFS normal sequences for LSTM-AE pretraining.

    Only NORMAL sequences are used for unsupervised pretraining.
    Anomalous sequences are withheld for optional benchmarking.

    Returns:
        dict with keys: train, val, input_dim
    """
    normal_seqs = preprocessor.get_normal_sequences()
    train_seqs, val_seqs = train_test_split(
        normal_seqs, test_size=val_ratio, random_state=42
    )
    return {
        "train": train_seqs,
        "val": val_seqs,
        "input_dim": preprocessor.num_templates,
    }


def create_otel_splits(baseline_windows: list, val_ratio: float = 0.20) -> dict:
    """Create train/val splits from OTel Demo baseline window data.

    Args:
        baseline_windows: List of window dicts from WindowAggregator (normal operation only).
        val_ratio: Fraction of data to hold out for validation (default 0.20).

    Returns:
        dict with keys: train, val (each a list of window dicts).

    Note:
        The test set is the fault injection evaluation suite — not a held-out split.
    """
    np.random.seed(42)
    indices = np.random.permutation(len(baseline_windows))

    n_train = int(len(indices) * (1.0 - val_ratio))

    train_idx = indices[:n_train]
    val_idx = indices[n_train:]

    return {
        "train": [baseline_windows[i] for i in train_idx],
        "val": [baseline_windows[i] for i in val_idx],
    }
