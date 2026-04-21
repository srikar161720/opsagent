"""Training loop for the LSTM-Autoencoder with early stopping.

Handles DataLoader creation, Adam optimizer, MSELoss, and best-model
restoration. Disk I/O (checkpoint saving) is left to the caller.
"""

from __future__ import annotations

import copy

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset


class AnomalyTrainer:
    """Train an autoencoder model with early stopping on validation loss.

    The trainer keeps the best model weights in memory and restores them
    after training completes. The caller is responsible for saving the
    final model to disk via ``torch.save()``.
    """

    def __init__(
        self,
        model: nn.Module,
        device: str = "auto",
    ) -> None:
        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self.device = device
        self.model = model.to(self.device)
        self.history: dict[str, list[float]] = {
            "train_loss": [],
            "val_loss": [],
        }

    def train(
        self,
        train_sequences: np.ndarray,
        val_sequences: np.ndarray,
        epochs: int = 100,
        batch_size: int = 32,
        learning_rate: float = 0.001,
        early_stopping_patience: int = 10,
    ) -> dict[str, list[float]]:
        """Run the training loop with early stopping.

        Args:
            train_sequences: Training data, shape ``(N, seq_len, feature_dim)``.
            val_sequences: Validation data, shape ``(M, seq_len, feature_dim)``.
            epochs: Maximum number of training epochs.
            batch_size: Mini-batch size for DataLoader.
            learning_rate: Adam optimizer learning rate.
            early_stopping_patience: Stop if val loss doesn't improve for this
                many consecutive epochs.

        Returns:
            Dict with keys ``train_loss`` and ``val_loss``, each a list of
            per-epoch float values.
        """
        train_loader = DataLoader(
            TensorDataset(torch.FloatTensor(train_sequences)),
            batch_size=batch_size,
            shuffle=True,
        )
        val_loader = DataLoader(
            TensorDataset(torch.FloatTensor(val_sequences)),
            batch_size=batch_size,
            shuffle=False,
        )

        criterion = nn.MSELoss()
        optimizer = torch.optim.Adam(self.model.parameters(), lr=learning_rate)

        best_val_loss = float("inf")
        patience_counter = 0
        best_state: dict = {}

        for epoch in range(epochs):
            # ── Training ────────────────────────────────────────
            self.model.train()
            train_losses: list[float] = []
            for (batch,) in train_loader:
                batch = batch.to(self.device)
                optimizer.zero_grad()
                output = self.model(batch)
                loss = criterion(output, batch)
                loss.backward()
                optimizer.step()
                train_losses.append(loss.item())

            # ── Validation ──────────────────────────────────────
            self.model.eval()
            val_losses: list[float] = []
            with torch.no_grad():
                for (batch,) in val_loader:
                    batch = batch.to(self.device)
                    output = self.model(batch)
                    loss = criterion(output, batch)
                    val_losses.append(loss.item())

            epoch_train = float(np.mean(train_losses))
            epoch_val = float(np.mean(val_losses))
            self.history["train_loss"].append(epoch_train)
            self.history["val_loss"].append(epoch_val)

            print(
                f"Epoch {epoch + 1}/{epochs}  "
                f"train_loss={epoch_train:.6f}  "
                f"val_loss={epoch_val:.6f}"
            )

            # ── Early stopping ──────────────────────────────────
            if epoch_val < best_val_loss:
                best_val_loss = epoch_val
                patience_counter = 0
                best_state = copy.deepcopy(self.model.state_dict())
            else:
                patience_counter += 1
                if patience_counter >= early_stopping_patience:
                    print(
                        f"Early stopping at epoch {epoch + 1} (patience={early_stopping_patience})"
                    )
                    break

        # Restore best model weights
        if best_state:
            self.model.load_state_dict(best_state)

        return self.history
