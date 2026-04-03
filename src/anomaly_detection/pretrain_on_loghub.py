"""Two-phase LSTM-AE training: pretrain on LogHub HDFS, fine-tune on OTel Demo.

Phase 1 (pretrain_on_hdfs):
  Parse HDFS.log → extract normal block sequences → one-hot encode → train.
  Learns general log anomaly patterns from a large-scale distributed system.

Phase 2 (finetune_on_otel_demo):
  Load pretrained weights (partial transfer if input_dim differs) → fine-tune
  on OTel Demo feature vectors with a lower learning rate.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as functional
from sklearn.model_selection import train_test_split

from src.anomaly_detection.lstm_autoencoder import LSTMAutoencoder
from src.anomaly_detection.trainer import AnomalyTrainer
from src.preprocessing.log_parser import LogParser
from src.preprocessing.loghub_preprocessor import LogHubHDFSPreprocessor


def pretrain_on_hdfs(
    hdfs_data_path: str,
    model_save_path: str,
    parser: LogParser | None = None,
) -> tuple[LSTMAutoencoder, LogParser]:
    """Pretrain LSTM-AE on LogHub HDFS normal sequences.

    Args:
        hdfs_data_path: Path to directory containing HDFS.log and anomaly_label.csv.
        model_save_path: Output checkpoint path (e.g. models/lstm_autoencoder/pretrained_hdfs.pt).
        parser: Optional shared LogParser instance. Creates new if None.

    Returns:
        Tuple of (trained model, parser) — parser returned for vocabulary reuse.
    """
    # 1. Parse HDFS logs
    if parser is None:
        parser = LogParser()
    preprocessor = LogHubHDFSPreprocessor(
        data_dir=hdfs_data_path, seq_length=10, parser=parser
    )

    print("Parsing HDFS.log (this may take several minutes)...")
    preprocessor.parse()
    n_templates = preprocessor.num_templates
    print(f"  Templates discovered: {n_templates}")

    # 2. Get normal sequences and split
    normal_seqs = preprocessor.get_normal_sequences()  # (N, seq_len) int32
    print(f"  Normal sequences: {normal_seqs.shape[0]}")

    train_seqs, val_seqs = train_test_split(
        normal_seqs, test_size=0.2, random_state=42
    )

    # 3. One-hot encode template IDs → (N, seq_len, n_templates)
    train_data = _one_hot_encode(train_seqs, n_templates)
    val_data = _one_hot_encode(val_seqs, n_templates)
    print(f"  Train: {train_data.shape}, Val: {val_data.shape}")

    # 4. Create model and train
    model = LSTMAutoencoder(input_dim=n_templates)
    trainer = AnomalyTrainer(model)
    history = trainer.train(
        train_sequences=train_data,
        val_sequences=val_data,
        epochs=50,
        batch_size=64,
        learning_rate=0.001,
        early_stopping_patience=5,
    )

    # 5. Save checkpoint
    Path(model_save_path).parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {"model_state_dict": model.state_dict(), "history": history},
        model_save_path,
    )
    print(f"  Checkpoint saved to {model_save_path}")

    return model, parser


def finetune_on_otel_demo(
    pretrained_model_path: str,
    otel_data: dict,
    model_save_path: str,
) -> LSTMAutoencoder:
    """Fine-tune pretrained LSTM-AE on OTel Demo feature vectors.

    Args:
        pretrained_model_path: Path to pretrained_hdfs.pt checkpoint.
        otel_data: Dict with keys ``train`` (np.ndarray), ``val`` (np.ndarray),
            ``input_dim`` (int).
        model_save_path: Output checkpoint path (e.g. models/lstm_autoencoder/finetuned_otel.pt).

    Returns:
        Fine-tuned model.
    """
    input_dim = otel_data["input_dim"]
    model = LSTMAutoencoder(input_dim=input_dim)

    # Load pretrained weights — handle dimension mismatch gracefully
    try:
        checkpoint = torch.load(
            pretrained_model_path, map_location="cpu"
        )
        state_dict = checkpoint
        if isinstance(checkpoint, dict) and "model_state_dict" in checkpoint:
            state_dict = checkpoint["model_state_dict"]
        model.load_state_dict(state_dict)
        print("Loaded full pretrained weights (input_dim matched).")
    except RuntimeError:
        print("Input dimensions differ — loading compatible weights only.")
        _load_compatible_weights(model, pretrained_model_path)

    # Fine-tune with lower learning rate
    trainer = AnomalyTrainer(model)
    history = trainer.train(
        train_sequences=otel_data["train"],
        val_sequences=otel_data["val"],
        epochs=200,
        batch_size=32,
        learning_rate=0.001,
        early_stopping_patience=10,
    )

    Path(model_save_path).parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {"model_state_dict": model.state_dict(), "history": history},
        model_save_path,
    )
    print(f"  Fine-tuned checkpoint saved to {model_save_path}")

    return model


def _load_compatible_weights(
    model: LSTMAutoencoder, checkpoint_path: str
) -> None:
    """Load only LSTM body weights from checkpoint; skip I/O layers if dims differ.

    Transfers encoder/decoder LSTM weights and latent projection weights.
    Skips embedding and output_layer (which depend on input_dim and will
    differ between HDFS pretraining and OTel Demo fine-tuning).
    """
    checkpoint = torch.load(checkpoint_path, map_location="cpu")
    if isinstance(checkpoint, dict) and "model_state_dict" in checkpoint:
        checkpoint = checkpoint["model_state_dict"]

    model_state = model.state_dict()

    compatible = {
        k: v
        for k, v in checkpoint.items()
        if k in model_state
        and "embedding" not in k
        and "output_layer" not in k
        and v.shape == model_state[k].shape
    }

    model_state.update(compatible)
    model.load_state_dict(model_state)

    print(
        f"_load_compatible_weights: loaded {len(compatible)}/{len(checkpoint)} tensors."
    )
    print("  Transferred: encoder, decoder, latent projection weights.")
    print("  Skipped: embedding, output_layer (input_dim mismatch).")


def _one_hot_encode(sequences: np.ndarray, num_classes: int) -> np.ndarray:
    """One-hot encode integer template ID sequences.

    Args:
        sequences: Integer array of shape ``(N, seq_len)``.
        num_classes: Number of template classes (= input_dim).

    Returns:
        Float array of shape ``(N, seq_len, num_classes)``.
    """
    tensor = torch.LongTensor(sequences)
    one_hot = functional.one_hot(tensor, num_classes=num_classes).float()
    result: np.ndarray = one_hot.numpy()
    return result
