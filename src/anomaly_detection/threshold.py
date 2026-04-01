"""Anomaly detection threshold calculation from baseline reconstruction errors.

Uses the 95th percentile of reconstruction error on normal baseline data.
Sequences with error above this threshold are flagged as anomalous during
inference.
"""

from __future__ import annotations

import numpy as np
import torch
from torch.utils.data import DataLoader, TensorDataset


def calculate_threshold(
    model: torch.nn.Module,
    baseline_sequences: np.ndarray,
    percentile: int = 95,
    batch_size: int = 256,
    device: str = "cpu",
) -> float:
    """Calculate anomaly detection threshold from normal baseline data.

    Computes the reconstruction error for every sequence in the baseline
    and returns the specified percentile as the threshold.

    Args:
        model: Trained LSTM-Autoencoder (must have ``get_reconstruction_error``).
        baseline_sequences: Normal operation data, shape ``(N, seq_len, feature_dim)``.
        percentile: Threshold percentile (default 95 → 5% false positive rate).
        batch_size: Batch size for inference.
        device: Device to run inference on.

    Returns:
        Threshold value as a float.
    """
    model.eval()
    model.to(device)

    loader = DataLoader(
        TensorDataset(torch.FloatTensor(baseline_sequences)),
        batch_size=batch_size,
        shuffle=False,
    )

    all_errors: list[float] = []
    with torch.no_grad():
        for (batch,) in loader:
            batch = batch.to(device)
            errors = model.get_reconstruction_error(batch)
            all_errors.extend(errors.cpu().tolist())

    threshold = float(np.percentile(all_errors, percentile))

    print(f"Threshold calculation (p{percentile}):")
    print(f"  Samples:   {len(all_errors)}")
    print(f"  Mean error: {np.mean(all_errors):.6f}")
    print(f"  Std error:  {np.std(all_errors):.6f}")
    print(f"  Threshold:  {threshold:.6f}")

    return threshold
