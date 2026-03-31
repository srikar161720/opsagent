"""Feature vector construction from windowed log + metric data.

Computes fixed-length feature vectors from the output of WindowAggregator,
combining log template statistics and metric summary statistics into a single
vector suitable as input to the LSTM-Autoencoder.

Scope: Used exclusively in the OTel Demo pipeline. The LogHub HDFS
preprocessor produces one-hot encoded sequences directly from template IDs,
bypassing feature engineering entirely.
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from src.preprocessing.log_parser import LogParser


class FeatureEngineer:
    """Compute fixed-length feature vectors from windowed log + metric data.

    Feature vector layout for a single window::

        [template_0_count, template_0_freq, template_1_count, template_1_freq, ...,
         error_ratio, unique_template_count,
         metric_0_mean, metric_0_std, metric_0_min, metric_0_max,
         metric_0_p50, metric_0_p99, metric_0_delta,
         ...]

    Total dimension: ``(num_templates * 2 + 2) + (len(metrics) * 7)``
    """

    ERROR_KEYWORDS: frozenset = frozenset(
        {"error", "exception", "fail", "fatal", "critical", "severe"}
    )

    def __init__(
        self,
        num_templates: int,
        services: list[str],
        metrics: list[str],
        parser: LogParser | None = None,
    ) -> None:
        self.num_templates = num_templates
        self.services = services
        self.metrics = metrics
        self.parser = parser
        self._prev_metric_means: dict[str, float] = {}

    def compute_features(self, window: dict) -> np.ndarray:
        """Compute the feature vector for a single time window.

        Args:
            window: Dict from ``WindowAggregator._finalize_window()``.
                    Keys: ``window_start``, ``window_end``, ``logs``, ``metrics``.

        Returns:
            1-D numpy array of shape ``(feature_dim,)``.
        """
        features: list[float] = []
        logs = window.get("logs", [])
        metrics = window.get("metrics", {})

        # ── Log template features ────────────────────────────────────────
        template_ids = [log["template_id"] for log in logs]
        template_counts = Counter(template_ids)
        total_logs = max(len(logs), 1)

        for tid in range(self.num_templates):
            count = template_counts.get(tid, 0)
            features.append(float(count))
            features.append(float(count) / total_logs)

        # Error ratio
        if self.parser is not None:
            error_count = sum(
                count
                for tid, count in template_counts.items()
                if any(kw in self.parser.get_template(tid).lower() for kw in self.ERROR_KEYWORDS)
            )
        else:
            error_count = 0
        features.append(float(error_count) / total_logs)
        features.append(float(len(template_counts)))

        # ── Metric features ──────────────────────────────────────────────
        for metric_name in self.metrics:
            values = metrics.get(metric_name, [])
            if values:
                arr = np.array(values, dtype=float)
                mean_val = float(np.mean(arr))
                features.extend(
                    [
                        mean_val,
                        float(np.std(arr)),
                        float(np.min(arr)),
                        float(np.max(arr)),
                        float(np.percentile(arr, 50)),
                        float(np.percentile(arr, 99)),
                        mean_val - self._prev_metric_means.get(metric_name, mean_val),
                    ]
                )
                self._prev_metric_means[metric_name] = mean_val
            else:
                features.extend([0.0] * 7)

        return np.array(features, dtype=np.float32)

    def build_sequence(self, windows: list[dict], sequence_length: int = 10) -> np.ndarray:
        """Build an ``(sequence_length, feature_dim)`` input tensor for the LSTM-AE.

        Takes the last *sequence_length* windows from the provided list.

        Raises:
            ValueError: If fewer than *sequence_length* windows are provided.
        """
        if len(windows) < sequence_length:
            raise ValueError(f"Need at least {sequence_length} windows; got {len(windows)}")
        feature_vecs = [self.compute_features(w) for w in windows[-sequence_length:]]
        return np.stack(feature_vecs)

    @property
    def feature_dim(self) -> int:
        """Total feature vector dimension for a single window."""
        log_features = self.num_templates * 2 + 2
        metric_features = len(self.metrics) * 7
        return log_features + metric_features

    def reset(self) -> None:
        """Reset internal state (delta tracking). Call between independent batches."""
        self._prev_metric_means.clear()
