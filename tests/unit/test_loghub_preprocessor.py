"""Tests for LogHubHDFSPreprocessor and dataset split helpers."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from src.preprocessing.log_parser import LogParser
from src.preprocessing.loghub_preprocessor import (
    LogHubHDFSPreprocessor,
    create_hdfs_splits,
    create_otel_splits,
)


def _make_preprocessor(
    hdfs_data_dir: Path, seq_length: int = 10
) -> LogHubHDFSPreprocessor:
    """Create a preprocessor with a fresh parser (tmp_path persistence)."""
    parser = LogParser(
        persistence_path=str(hdfs_data_dir / "_drain3")
    )
    return LogHubHDFSPreprocessor(
        data_dir=str(hdfs_data_dir),
        seq_length=seq_length,
        parser=parser,
    )


class TestLogHubHDFSPreprocessor:
    """Tests for the HDFS preprocessor using synthetic data."""

    def test_parse_required_before_access(
        self, hdfs_data_dir: Path
    ) -> None:
        preprocessor = _make_preprocessor(hdfs_data_dir)
        with pytest.raises(RuntimeError, match="Call parse"):
            preprocessor.get_normal_sequences()

    def test_parse_populates_block_sequences(
        self, hdfs_data_dir: Path
    ) -> None:
        preprocessor = _make_preprocessor(hdfs_data_dir)
        preprocessor.parse()
        assert len(preprocessor._block_sequences) == 4

    def test_block_id_regex_extraction(self) -> None:
        pattern = LogHubHDFSPreprocessor.BLOCK_PATTERN
        line = (
            "Receiving block blk_-1608999687919862906"
            " src: /10.0.0.1:50010"
        )
        matches = pattern.findall(line)
        assert matches == ["blk_-1608999687919862906"]

    def test_block_id_regex_positive(self) -> None:
        pattern = LogHubHDFSPreprocessor.BLOCK_PATTERN
        assert pattern.findall("blk_12345") == ["blk_12345"]
        assert pattern.findall("blk_-99999") == ["blk_-99999"]

    def test_normal_sequences_shape(
        self, hdfs_data_dir: Path
    ) -> None:
        preprocessor = _make_preprocessor(hdfs_data_dir)
        preprocessor.parse()
        normal = preprocessor.get_normal_sequences()
        assert normal.ndim == 2
        assert normal.shape[1] == 10
        assert normal.dtype == np.int32

    def test_anomalous_sequences_shape(
        self, hdfs_data_dir: Path
    ) -> None:
        preprocessor = _make_preprocessor(hdfs_data_dir)
        preprocessor.parse()
        anomalous = preprocessor.get_anomalous_sequences()
        assert anomalous.ndim == 2
        assert anomalous.shape[1] == 10
        assert anomalous.dtype == np.int32
        assert len(anomalous) > 0  # blk_9999 is anomalous

    def test_labeled_sequences_combined(
        self, hdfs_data_dir: Path
    ) -> None:
        preprocessor = _make_preprocessor(hdfs_data_dir)
        preprocessor.parse()
        sequences, labels = preprocessor.get_labeled_sequences()
        assert len(sequences) == len(labels)
        assert set(np.unique(labels)).issubset({0, 1})
        assert 0 in labels
        assert 1 in labels

    def test_short_block_left_padded(
        self, hdfs_data_dir: Path
    ) -> None:
        preprocessor = _make_preprocessor(hdfs_data_dir)
        preprocessor.parse()
        # blk_9999 has only 2 log lines → left-padded with zeros
        anomalous = preprocessor.get_anomalous_sequences()
        assert len(anomalous) == 1
        seq = anomalous[0]
        # First 8 elements should be 0 (left-padded)
        assert all(v == 0 for v in seq[:8])
        # Last 2 should be template IDs (>= 0)
        assert any(v >= 0 for v in seq[8:])

    def test_long_block_chunked(self, tmp_path: Path) -> None:
        """Block with 15 events → 2 sequences (seq_length=10)."""
        lines = []
        for i in range(15):
            lines.append(
                f"081109 20361{i % 10} 148 INFO"
                f" dfs.DataNode$PacketResponder:"
                f" Receiving block blk_5555"
                f" src: /10.0.0.{i}:50010"
                f" dest: /10.0.0.{i}:50010"
            )
        (tmp_path / "HDFS.log").write_text(
            "\n".join(lines) + "\n"
        )
        (tmp_path / "anomaly_label.csv").write_text(
            "BlockId,Label\nblk_5555,Normal\n"
        )

        preprocessor = _make_preprocessor(tmp_path)
        preprocessor.parse()
        normal = preprocessor.get_normal_sequences()
        # 15 events → chunk [0:10] + chunk [10:15] (padded)
        assert len(normal) == 2

    def test_num_templates_property(
        self, hdfs_data_dir: Path
    ) -> None:
        preprocessor = _make_preprocessor(hdfs_data_dir)
        preprocessor.parse()
        assert preprocessor.num_templates > 0
        assert (
            preprocessor.num_templates
            == preprocessor.parser.num_templates
        )

    def test_shared_parser_instance(
        self, hdfs_data_dir: Path
    ) -> None:
        parser = LogParser(
            persistence_path=str(hdfs_data_dir / "drain3_shared")
        )
        parser.parse("some pre-existing template line")
        initial_count = parser.num_templates

        preprocessor = LogHubHDFSPreprocessor(
            data_dir=str(hdfs_data_dir), parser=parser
        )
        preprocessor.parse()
        assert parser.num_templates >= initial_count


class TestDatasetSplitHelpers:
    """Tests for create_hdfs_splits and create_otel_splits."""

    def test_create_hdfs_splits_keys(
        self, hdfs_data_dir: Path
    ) -> None:
        preprocessor = _make_preprocessor(hdfs_data_dir)
        preprocessor.parse()
        splits = create_hdfs_splits(preprocessor, val_ratio=0.2)
        assert "train" in splits
        assert "val" in splits
        assert "input_dim" in splits

    def test_create_hdfs_splits_partition(
        self, hdfs_data_dir: Path
    ) -> None:
        preprocessor = _make_preprocessor(hdfs_data_dir)
        preprocessor.parse()
        normal_total = len(preprocessor.get_normal_sequences())
        splits = create_hdfs_splits(preprocessor, val_ratio=0.2)
        assert (
            len(splits["train"]) + len(splits["val"])
            == normal_total
        )

    def test_create_hdfs_splits_input_dim(
        self, hdfs_data_dir: Path
    ) -> None:
        preprocessor = _make_preprocessor(hdfs_data_dir)
        preprocessor.parse()
        splits = create_hdfs_splits(preprocessor)
        assert splits["input_dim"] == preprocessor.num_templates

    def test_create_otel_splits_keys(self) -> None:
        windows = [{"id": i} for i in range(20)]
        splits = create_otel_splits(windows, val_ratio=0.2)
        assert "train" in splits
        assert "val" in splits
        assert len(splits["train"]) + len(splits["val"]) == 20
