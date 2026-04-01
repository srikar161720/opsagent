"""LSTM-Autoencoder for log sequence anomaly detection.

Architecture: encoder-decoder with latent bottleneck.
  Input  → Linear embedding → LSTM encoder → latent projection (bottleneck)
         → latent expansion → repeat-vector → LSTM decoder → Linear output

The bottleneck forces the model to learn a compressed representation of normal
log sequences. Anomalous sequences produce higher reconstruction error because
the model has not learned to represent them.
"""

from __future__ import annotations

import torch
import torch.nn as nn


class LSTMAutoencoder(nn.Module):
    """LSTM-based autoencoder for detecting anomalous log template sequences.

    Trained on normal operation data only (unsupervised). At inference time,
    sequences with reconstruction error above a learned threshold are flagged
    as anomalous.

    Input shape:  ``(batch, seq_len, input_dim)``
    Output shape: ``(batch, seq_len, input_dim)``
    """

    def __init__(
        self,
        input_dim: int,
        embedding_dim: int = 32,
        hidden_dim: int = 64,
        latent_dim: int = 16,
        num_layers: int = 2,
        dropout: float = 0.2,
        seq_len: int = 10,
    ) -> None:
        super().__init__()
        self.seq_len = seq_len

        # Encoder
        self.embedding = nn.Linear(input_dim, embedding_dim)
        self.encoder = nn.LSTM(
            input_size=embedding_dim,
            hidden_size=hidden_dim,
            num_layers=num_layers,
            batch_first=True,
            dropout=dropout,
        )

        # Bottleneck
        self.to_latent = nn.Linear(hidden_dim, latent_dim)
        self.from_latent = nn.Linear(latent_dim, hidden_dim)

        # Decoder
        self.decoder = nn.LSTM(
            input_size=hidden_dim,
            hidden_size=hidden_dim,
            num_layers=num_layers,
            batch_first=True,
            dropout=dropout,
        )
        self.output_layer = nn.Linear(hidden_dim, input_dim)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """Reconstruct the input sequence through the bottleneck.

        Args:
            x: Input tensor of shape ``(batch, seq_len, input_dim)``.

        Returns:
            Reconstructed tensor of shape ``(batch, seq_len, input_dim)``.
        """
        # Encode
        x_emb = self.embedding(x)  # (batch, seq_len, embedding_dim)
        _, (hidden, _) = self.encoder(x_emb)  # hidden: (num_layers, batch, hidden_dim)

        # Bottleneck — use last layer hidden state
        latent = self.to_latent(hidden[-1])  # (batch, latent_dim)

        # Decode — expand latent and repeat across sequence length
        dec_input = self.from_latent(latent)  # (batch, hidden_dim)
        dec_input = dec_input.unsqueeze(1).repeat(
            1, self.seq_len, 1
        )  # (batch, seq_len, hidden_dim)
        dec_output, _ = self.decoder(dec_input)  # (batch, seq_len, hidden_dim)

        result: torch.Tensor = self.output_layer(dec_output)
        return result  # (batch, seq_len, input_dim)

    def get_reconstruction_error(self, x: torch.Tensor) -> torch.Tensor:
        """Compute mean squared reconstruction error per sequence.

        Args:
            x: Input tensor of shape ``(batch, seq_len, input_dim)``.

        Returns:
            Tensor of shape ``(batch,)`` with MSE per sequence.
        """
        reconstructed = self.forward(x)
        error: torch.Tensor = torch.mean(
            (x - reconstructed) ** 2, dim=(1, 2)
        )
        return error
