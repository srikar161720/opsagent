"""Tests for anomaly detection components."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest
import torch

from src.anomaly_detection.detector import AnomalyDetector
from src.anomaly_detection.isolation_forest import IsolationForestDetector
from src.anomaly_detection.lstm_autoencoder import LSTMAutoencoder
from src.anomaly_detection.pretrain_on_loghub import (
    _load_compatible_weights,
    _one_hot_encode,
)
from src.anomaly_detection.threshold import calculate_threshold
from src.anomaly_detection.trainer import AnomalyTrainer

# ── Fixtures ─────────────────────────────────────────────────────────

INPUT_DIM = 5
SEQ_LEN = 3
BATCH = 4


@pytest.fixture()
def small_model() -> LSTMAutoencoder:
    torch.manual_seed(42)
    return LSTMAutoencoder(
        input_dim=INPUT_DIM,
        embedding_dim=4,
        hidden_dim=8,
        latent_dim=4,
        num_layers=2,
        dropout=0.0,
        seq_len=SEQ_LEN,
    )


@pytest.fixture()
def sample_batch() -> torch.Tensor:
    torch.manual_seed(42)
    return torch.randn(BATCH, SEQ_LEN, INPUT_DIM)


@pytest.fixture()
def small_train_data() -> np.ndarray:
    np.random.seed(42)
    return np.random.randn(20, SEQ_LEN, INPUT_DIM).astype(np.float32)


@pytest.fixture()
def small_val_data() -> np.ndarray:
    np.random.seed(99)
    return np.random.randn(8, SEQ_LEN, INPUT_DIM).astype(np.float32)


# ── LSTMAutoencoder ─────────────────────────────────────────────────


class TestLSTMAutoencoder:
    def test_forward_output_shape(
        self,
        small_model: LSTMAutoencoder,
        sample_batch: torch.Tensor,
    ) -> None:
        output = small_model(sample_batch)
        assert output.shape == (BATCH, SEQ_LEN, INPUT_DIM)

    def test_reconstruction_error_shape(
        self,
        small_model: LSTMAutoencoder,
        sample_batch: torch.Tensor,
    ) -> None:
        error = small_model.get_reconstruction_error(sample_batch)
        assert error.shape == (BATCH,)

    def test_reconstruction_error_nonnegative(
        self,
        small_model: LSTMAutoencoder,
        sample_batch: torch.Tensor,
    ) -> None:
        error = small_model.get_reconstruction_error(sample_batch)
        assert (error >= 0).all()

    def test_gradient_flow(
        self,
        small_model: LSTMAutoencoder,
        sample_batch: torch.Tensor,
    ) -> None:
        output = small_model(sample_batch)
        loss = torch.mean((output - sample_batch) ** 2)
        loss.backward()
        for name, param in small_model.named_parameters():
            assert param.grad is not None, (
                f"No gradient for {name}"
            )

    def test_default_hyperparameters(self) -> None:
        model = LSTMAutoencoder(input_dim=10)
        assert model.embedding.in_features == 10
        assert model.output_layer.out_features == 10
        assert model.seq_len == 10


# ── AnomalyTrainer ──────────────────────────────────────────────────


class TestAnomalyTrainer:
    def test_training_reduces_loss(
        self,
        small_model: LSTMAutoencoder,
        small_train_data: np.ndarray,
        small_val_data: np.ndarray,
    ) -> None:
        trainer = AnomalyTrainer(small_model, device="cpu")
        history = trainer.train(
            small_train_data,
            small_val_data,
            epochs=10,
            batch_size=8,
            learning_rate=0.01,
            early_stopping_patience=20,
        )
        assert history["train_loss"][-1] < history["train_loss"][0]

    def test_early_stopping(
        self,
        small_model: LSTMAutoencoder,
        small_train_data: np.ndarray,
        small_val_data: np.ndarray,
    ) -> None:
        trainer = AnomalyTrainer(small_model, device="cpu")
        history = trainer.train(
            small_train_data,
            small_val_data,
            epochs=200,
            batch_size=8,
            learning_rate=0.001,
            early_stopping_patience=3,
        )
        assert len(history["train_loss"]) < 200

    def test_history_format(
        self,
        small_model: LSTMAutoencoder,
        small_train_data: np.ndarray,
        small_val_data: np.ndarray,
    ) -> None:
        trainer = AnomalyTrainer(small_model, device="cpu")
        history = trainer.train(
            small_train_data,
            small_val_data,
            epochs=2,
            batch_size=8,
        )
        assert "train_loss" in history
        assert "val_loss" in history
        assert len(history["train_loss"]) == len(
            history["val_loss"]
        )
        assert all(
            isinstance(v, float) for v in history["train_loss"]
        )

    def test_device_auto(
        self, small_model: LSTMAutoencoder
    ) -> None:
        trainer = AnomalyTrainer(small_model, device="auto")
        assert trainer.device in ("cpu", "cuda")


# ── Threshold ────────────────────────────────────────────────────────


class TestThreshold:
    def test_returns_float(
        self,
        small_model: LSTMAutoencoder,
        small_val_data: np.ndarray,
    ) -> None:
        result = calculate_threshold(
            small_model, small_val_data, percentile=95
        )
        assert isinstance(result, float)

    def test_percentile_ordering(
        self,
        small_model: LSTMAutoencoder,
        small_val_data: np.ndarray,
    ) -> None:
        t50 = calculate_threshold(
            small_model, small_val_data, percentile=50
        )
        t95 = calculate_threshold(
            small_model, small_val_data, percentile=95
        )
        assert t95 >= t50

    def test_batch_processing(
        self, small_model: LSTMAutoencoder
    ) -> None:
        np.random.seed(42)
        data = np.random.randn(100, SEQ_LEN, INPUT_DIM).astype(
            np.float32
        )
        result = calculate_threshold(
            small_model, data, batch_size=32
        )
        assert isinstance(result, float)
        assert result > 0


# ── IsolationForestDetector ──────────────────────────────────────────


class TestIsolationForestDetector:
    def test_fit_predict_shape(self) -> None:
        np.random.seed(42)
        x = np.random.randn(50, 15).astype(np.float32)
        detector = IsolationForestDetector()
        detector.fit(x)
        preds = detector.predict(x)
        assert len(preds) == 50

    def test_predict_values(self) -> None:
        np.random.seed(42)
        x = np.random.randn(50, 15).astype(np.float32)
        detector = IsolationForestDetector()
        detector.fit(x)
        preds = detector.predict(x)
        assert set(np.unique(preds)).issubset({-1, 1})

    def test_score_samples_shape(self) -> None:
        np.random.seed(42)
        x = np.random.randn(50, 15).astype(np.float32)
        detector = IsolationForestDetector()
        detector.fit(x)
        scores = detector.score_samples(x)
        assert len(scores) == 50


# ── AnomalyDetector ─────────────────────────────────────────────────


class TestAnomalyDetector:
    def test_score_returns_float(
        self, small_model: LSTMAutoencoder
    ) -> None:
        callback = MagicMock()
        detector = AnomalyDetector(
            model=small_model,
            threshold=1e10,
            on_anomaly=callback,
        )
        np.random.seed(42)
        seq = np.random.randn(SEQ_LEN, INPUT_DIM).astype(
            np.float32
        )
        result = detector.score(seq)
        assert isinstance(result, float)

    def test_callback_fired_on_anomaly(
        self, small_model: LSTMAutoencoder
    ) -> None:
        callback = MagicMock()
        detector = AnomalyDetector(
            model=small_model,
            threshold=0.0,
            on_anomaly=callback,
            affected_services=["frontend"],
        )
        np.random.seed(42)
        seq = np.random.randn(SEQ_LEN, INPUT_DIM).astype(
            np.float32
        )
        detector.score(seq)
        callback.assert_called_once()
        alert = callback.call_args[0][0]
        assert "anomaly_score" in alert
        assert "threshold" in alert
        assert "affected_services" in alert
        assert alert["severity"] == "high"

    def test_callback_not_fired_on_normal(
        self, small_model: LSTMAutoencoder
    ) -> None:
        callback = MagicMock()
        detector = AnomalyDetector(
            model=small_model,
            threshold=1e10,
            on_anomaly=callback,
        )
        np.random.seed(42)
        seq = np.random.randn(SEQ_LEN, INPUT_DIM).astype(
            np.float32
        )
        detector.score(seq)
        callback.assert_not_called()


# ── _load_compatible_weights ─────────────────────────────────────────


class TestLoadCompatibleWeights:
    def test_loads_subset_of_tensors(
        self, tmp_path: Path
    ) -> None:
        torch.manual_seed(42)
        old_model = LSTMAutoencoder(
            input_dim=5,
            embedding_dim=4,
            hidden_dim=8,
            latent_dim=4,
            num_layers=2,
            dropout=0.0,
            seq_len=3,
        )
        ckpt_path = str(tmp_path / "old.pt")
        torch.save(
            {"model_state_dict": old_model.state_dict()},
            ckpt_path,
        )

        torch.manual_seed(99)
        new_model = LSTMAutoencoder(
            input_dim=10,
            embedding_dim=4,
            hidden_dim=8,
            latent_dim=4,
            num_layers=2,
            dropout=0.0,
            seq_len=3,
        )
        pre_encoder_weight = (
            new_model.encoder.weight_ih_l0.clone()
        )

        _load_compatible_weights(new_model, ckpt_path)

        # LSTM encoder weights should have been transferred
        assert not torch.allclose(
            new_model.encoder.weight_ih_l0, pre_encoder_weight
        )
        assert torch.allclose(
            new_model.encoder.weight_ih_l0,
            old_model.encoder.weight_ih_l0,
        )

        # Embedding should NOT match old model (input_dim differs)
        assert (
            new_model.embedding.in_features
            != old_model.embedding.in_features
        )

    def test_handles_wrapped_checkpoint(
        self, tmp_path: Path
    ) -> None:
        torch.manual_seed(42)
        model = LSTMAutoencoder(
            input_dim=5,
            embedding_dim=4,
            hidden_dim=8,
            latent_dim=4,
            num_layers=2,
            dropout=0.0,
            seq_len=3,
        )
        ckpt_path = str(tmp_path / "wrapped.pt")
        torch.save(
            {
                "model_state_dict": model.state_dict(),
                "history": {"train_loss": [0.1]},
            },
            ckpt_path,
        )

        new_model = LSTMAutoencoder(
            input_dim=5,
            embedding_dim=4,
            hidden_dim=8,
            latent_dim=4,
            num_layers=2,
            dropout=0.0,
            seq_len=3,
        )
        # Should not raise
        _load_compatible_weights(new_model, ckpt_path)


# ── One-hot encoding helper ─────────────────────────────────────────


class TestOneHotEncode:
    def test_output_shape(self) -> None:
        seqs = np.array([[0, 1, 2], [1, 0, 2]], dtype=np.int32)
        result = _one_hot_encode(seqs, num_classes=5)
        assert result.shape == (2, 3, 5)

    def test_output_values(self) -> None:
        seqs = np.array([[0, 1]], dtype=np.int32)
        result = _one_hot_encode(seqs, num_classes=3)
        expected_0 = [1.0, 0.0, 0.0]
        expected_1 = [0.0, 1.0, 0.0]
        np.testing.assert_array_almost_equal(
            result[0, 0], expected_0
        )
        np.testing.assert_array_almost_equal(
            result[0, 1], expected_1
        )
