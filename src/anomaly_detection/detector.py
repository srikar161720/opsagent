"""Real-time anomaly detection service — Fast Loop to Slow Loop bridge.

Wraps the fine-tuned LSTM-AE and threshold. Scores each incoming window
sequence; fires an alert callback when reconstruction error exceeds threshold.

Usage::

    detector = AnomalyDetector(model, threshold=0.042, on_anomaly=agent.investigate)
    detector.score(sequence)  # call per window from the data collection loop
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import numpy as np
import torch

from src.anomaly_detection.lstm_autoencoder import LSTMAutoencoder


class AnomalyDetector:
    """Score window sequences and trigger investigation on anomalies.

    Bridges the Fast Loop (continuous monitoring) and Slow Loop (LangGraph
    agent investigation). When a sequence's reconstruction error exceeds
    the threshold, the ``on_anomaly`` callback is invoked with an alert dict.
    """

    def __init__(
        self,
        model: LSTMAutoencoder,
        threshold: float,
        on_anomaly: Callable[[dict], None],
        affected_services: list[str] | None = None,
        device: str = "cpu",
    ) -> None:
        """
        Args:
            model: Fine-tuned LSTM-Autoencoder.
            threshold: Anomaly detection threshold (from ``calculate_threshold``).
            on_anomaly: Callback invoked when anomaly detected. Receives alert dict.
            affected_services: Service names to include in alert context.
            device: Device for inference.
        """
        self.device = device
        self.model = model.to(device)
        self.model.eval()
        self.threshold = threshold
        self.on_anomaly = on_anomaly
        self.affected_services = affected_services or []

    def score(self, sequence: np.ndarray) -> float:
        """Score one window sequence and fire callback if anomalous.

        Args:
            sequence: Feature vector of shape ``(seq_len, feature_dim)``.

        Returns:
            Reconstruction error as a float.
        """
        tensor = (
            torch.FloatTensor(sequence).unsqueeze(0).to(self.device)
        )  # (1, seq_len, feature_dim)

        with torch.no_grad():
            error = self.model.get_reconstruction_error(tensor).item()

        if error > self.threshold:
            alert = {
                "title": "LSTM-AE Anomaly Detected",
                "severity": "high",
                "timestamp": datetime.now(UTC).isoformat(),
                "affected_services": self.affected_services,
                "anomaly_score": round(error, 6),
                "threshold": round(self.threshold, 6),
            }
            self.on_anomaly(alert)

        return error
