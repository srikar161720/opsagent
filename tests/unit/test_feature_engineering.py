"""Unit tests for FeatureEngineer."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import numpy as np
import pytest

from src.preprocessing.feature_engineering import FeatureEngineer


@pytest.fixture()
def engineer() -> FeatureEngineer:
    """FeatureEngineer with 5 templates, 2 metrics, no parser."""
    return FeatureEngineer(
        num_templates=5,
        services=["frontend", "cartservice"],
        metrics=["cpu_usage", "memory_usage"],
    )


@pytest.fixture()
def engineer_with_parser() -> FeatureEngineer:
    """FeatureEngineer with a mock parser for error keyword detection."""
    mock_parser = MagicMock()
    mock_parser.get_template.side_effect = lambda tid: {
        0: "Service started on port 8080",
        1: "Error: connection timeout after retries",
        2: "Request completed successfully",
    }.get(tid, "UNKNOWN")

    return FeatureEngineer(
        num_templates=3,
        services=["frontend"],
        metrics=["cpu_usage"],
        parser=mock_parser,
    )


class TestFeatureDim:
    def test_feature_dim_formula(self, engineer: FeatureEngineer) -> None:
        # (5 templates * 2 + 2) + (2 metrics * 7) = 12 + 14 = 26
        assert engineer.feature_dim == 26

    def test_feature_dim_no_metrics(self) -> None:
        eng = FeatureEngineer(num_templates=10, services=[], metrics=[])
        # 10 * 2 + 2 = 22
        assert eng.feature_dim == 22

    def test_feature_dim_no_templates(self) -> None:
        eng = FeatureEngineer(num_templates=0, services=[], metrics=["cpu"])
        # (0 * 2 + 2) + (1 * 7) = 2 + 7 = 9
        assert eng.feature_dim == 9


class TestComputeFeatures:
    def test_output_shape(
        self, engineer: FeatureEngineer, sample_window_dict: dict[str, Any]
    ) -> None:
        features = engineer.compute_features(sample_window_dict)
        assert features.shape == (engineer.feature_dim,)
        assert features.dtype == np.float32

    def test_template_counts(self, engineer: FeatureEngineer) -> None:
        window = {
            "logs": [
                {"template_id": 0, "service": "frontend"},
                {"template_id": 0, "service": "frontend"},
                {"template_id": 2, "service": "redis"},
            ],
            "metrics": {},
        }
        features = engineer.compute_features(window)
        # Template 0: count=2, freq=2/3
        assert features[0] == 2.0  # count for template 0
        assert abs(features[1] - 2.0 / 3.0) < 1e-6  # freq for template 0
        # Template 2: count=1, freq=1/3
        assert features[4] == 1.0  # count for template 2
        # Unique template count (last of log features section)
        log_feature_end = engineer.num_templates * 2 + 2
        assert features[log_feature_end - 1] == 2.0  # 2 unique templates

    def test_empty_window(self, engineer: FeatureEngineer) -> None:
        window: dict[str, Any] = {"logs": [], "metrics": {}}
        features = engineer.compute_features(window)
        assert features.shape == (engineer.feature_dim,)
        # All template counts should be 0
        for i in range(0, engineer.num_templates * 2, 2):
            assert features[i] == 0.0

    def test_metric_features(self) -> None:
        eng = FeatureEngineer(num_templates=0, services=[], metrics=["cpu"])
        window = {
            "logs": [],
            "metrics": {"cpu": [1.0, 2.0, 3.0, 4.0]},
        }
        features = eng.compute_features(window)
        # After 2 log features (error_ratio, unique_count), we have 7 metric features
        offset = 2  # num_templates * 2 + 2 = 0 * 2 + 2 = 2
        assert abs(features[offset] - 2.5) < 1e-6  # mean
        assert features[offset + 2] == 1.0  # min
        assert features[offset + 3] == 4.0  # max

    def test_error_ratio_with_parser(self, engineer_with_parser: FeatureEngineer) -> None:
        window = {
            "logs": [
                {"template_id": 0, "service": "frontend"},  # not error
                {"template_id": 1, "service": "frontend"},  # "Error:" keyword
                {"template_id": 2, "service": "frontend"},  # not error
            ],
            "metrics": {},
        }
        features = engineer_with_parser.compute_features(window)
        # error_ratio = 1 error template / 3 total = 0.333...
        error_ratio_idx = engineer_with_parser.num_templates * 2
        assert abs(features[error_ratio_idx] - 1.0 / 3.0) < 1e-6

    def test_error_ratio_without_parser(self, engineer: FeatureEngineer) -> None:
        window = {
            "logs": [{"template_id": 0, "service": "frontend"}],
            "metrics": {},
        }
        features = engineer.compute_features(window)
        error_ratio_idx = engineer.num_templates * 2
        assert features[error_ratio_idx] == 0.0  # No parser → always 0


class TestBuildSequence:
    def test_output_shape(self, engineer: FeatureEngineer) -> None:
        windows = [{"logs": [], "metrics": {}} for _ in range(10)]
        seq = engineer.build_sequence(windows, sequence_length=10)
        assert seq.shape == (10, engineer.feature_dim)

    def test_too_few_windows_raises(self, engineer: FeatureEngineer) -> None:
        windows = [{"logs": [], "metrics": {}} for _ in range(5)]
        with pytest.raises(ValueError, match="Need at least 10"):
            engineer.build_sequence(windows, sequence_length=10)

    def test_takes_last_n_windows(self, engineer: FeatureEngineer) -> None:
        windows = [{"logs": [], "metrics": {}} for _ in range(15)]
        seq = engineer.build_sequence(windows, sequence_length=10)
        assert seq.shape == (10, engineer.feature_dim)


class TestReset:
    def test_reset_clears_delta_state(self) -> None:
        eng = FeatureEngineer(num_templates=0, services=[], metrics=["cpu"])
        window = {"logs": [], "metrics": {"cpu": [10.0]}}
        eng.compute_features(window)
        eng.reset()
        # After reset, delta should be 0 (mean - mean = 0 since no prior)
        features = eng.compute_features(window)
        delta_idx = 2 + 6  # offset 2 (log features) + 6 (delta is 7th metric feature)
        assert features[delta_idx] == 0.0
