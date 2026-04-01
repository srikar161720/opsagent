"""Isolation Forest baseline for anomaly detection comparison.

Serves as the "Anomaly Detection Only" baseline to demonstrate
OpsAgent's full-pipeline advantage over detection-only systems.
Input sequences must be flattened to 2D before fitting/predicting.
"""

from __future__ import annotations

import numpy as np
from sklearn.ensemble import IsolationForest


class IsolationForestDetector:
    """Scikit-learn Isolation Forest wrapper for sequence anomaly detection.

    Input must be flattened from ``(N, seq_len, feature_dim)`` to
    ``(N, seq_len * feature_dim)`` before calling ``fit`` / ``predict``.
    """

    def __init__(
        self,
        n_estimators: int = 100,
        contamination: float = 0.01,
        max_samples: int = 256,
        random_state: int = 42,
    ) -> None:
        self.model = IsolationForest(
            n_estimators=n_estimators,
            contamination=contamination,
            max_samples=max_samples,
            random_state=random_state,
            n_jobs=-1,
        )

    def fit(self, x: np.ndarray) -> None:
        """Train the Isolation Forest on flattened sequence data.

        Args:
            x: Array of shape ``(N, seq_len * feature_dim)``.
        """
        self.model.fit(x)

    def predict(self, x: np.ndarray) -> np.ndarray:
        """Predict anomaly labels.

        Args:
            x: Array of shape ``(N, seq_len * feature_dim)``.

        Returns:
            Array of ``+1`` (normal) or ``-1`` (anomaly).
        """
        result: np.ndarray = self.model.predict(x)
        return result

    def score_samples(self, x: np.ndarray) -> np.ndarray:
        """Get anomaly scores (more negative = more anomalous).

        Args:
            x: Array of shape ``(N, seq_len * feature_dim)``.

        Returns:
            Array of anomaly scores.
        """
        result: np.ndarray = self.model.score_samples(x)
        return result
