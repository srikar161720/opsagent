# OpsAgent — Anomaly Detection Specifications

> Load this file when implementing anything in `src/anomaly_detection/`,
> `src/preprocessing/log_parser.py`, `src/preprocessing/loghub_preprocessor.py`,
> or `notebooks/05_anomaly_detection_dev.ipynb`.

---

## 1. End-to-End Anomaly Detection Pipeline

```
OTel Demo Logs (Kafka)
        │
        ▼
  LogConsumer.consume()          ← src/data_collection/kafka_consumer.py
        │
        ▼ raw log string
  LogParser.parse(line)          ← src/preprocessing/log_parser.py (Drain3 wrapper)
        │
        ▼ (template_id: int, template_str: str)
  WindowAggregator.add_log()     ← src/preprocessing/windowing.py
        │  (60s non-overlapping windows)
        ▼ window dict
  FeatureEngineer.compute_sequence()  ← src/preprocessing/feature_engineering.py
        │  (last 10 windows → shape: [10, feature_dim])
        ▼ np.ndarray
  LSTMAutoencoder.get_reconstruction_error()   ← src/anomaly_detection/lstm_autoencoder.py
        │  MSE of reconstructed vs. input
        ▼ float
  score > threshold?
  ├── No  → continue monitoring
  └── Yes → trigger AgentExecutor.investigate(alert)

Prometheus Metrics run in parallel, feeding into FeatureEngineer metric features
and later into the agent's query_metrics tool.
```

**Key invariant:** The `LogParser` instance is shared across the entire pipeline. The same Drain3 `TemplateMiner` object that parsed HDFS logs during pretraining must be the same object used for OTel Demo fine-tuning and inference. This ensures template IDs are consistent across both data sources.

---

## 2. Drain3 Log Parser (`src/preprocessing/log_parser.py`)

### What Drain3 Does

Drain3 is an online log template miner built on a fixed-depth prefix tree. It takes raw log lines and produces stable integer **template IDs** suitable as discrete vocabulary tokens for the LSTM-AE. It runs in two modes:
- **Training mode** (`add_log_message()`): learns new templates on the fly; updates existing ones when log patterns evolve
- **Inference mode** (`match()`): matches against already-learned templates without creating new clusters — use this after pretraining is complete to prevent template drift during fine-tuning

### Configuration Parameters

| Parameter | Value | Notes |
|---|---|---|
| `drain_depth` | 4 | Fixed prefix tree depth; higher = more specific templates, more clusters |
| `drain_sim_th` | 0.4 | Similarity threshold to merge into an existing cluster; lower = more templates |
| `drain_max_children` | 100 | Max children per tree node; prevents overly broad clusters |
| `parametrize_numeric_tokens` | `True` | Replaces all numeric tokens with `<*>` before clustering |
| Persistence | `FilePersistence` | State saved to `models/drain3/drain3_state.bin` between sessions |

### Pre-processing Advice (from Drain3 docs)

Drain3 mining accuracy improves significantly if you **strip structured headers** (timestamp, hostname, log level, PID) before passing the free-text portion. For HDFS logs this means removing the date/time prefix; for OTel Demo logs this means removing the gRPC metadata prefix.

```python
# Example: strip HDFS structured header before passing to Drain3
# Raw: "081109 204113 3 INFO dfs.DataNode$DataXceiver: Receiving block blk_-1608999687919862906"
# Strip everything up to the log body
import re
HEADER_PATTERN = re.compile(r"^\d{6}\s+\d{6}\s+\d+\s+\w+\s+[\w.$]+:\s+")
clean_line = HEADER_PATTERN.sub("", raw_line).strip()
template_id, template_str = parser.parse(clean_line)
```

### `LogParser` Class — Authoritative Reference

> **Full class implementation:** `context/data_pipeline_specs.md` §2.2 `LogParser` Class.
> The class is defined once there to avoid duplication. Key API summary:

| Method | Mode | Returns | Notes |
|---|---|---|---|
| `parse(log_line)` | Training | `(template_id, template_str)` | Learns new templates; grows `num_templates` |
| `match(log_line)` | Inference | `(template_id, template_str)` | Read-only; returns `(-1, "UNKNOWN")` for unrecognized lines |
| `num_templates` | Property | `int` | Finalized only after full corpus parsed |
| `save()` | Persist | `None` | Writes Drain3 state to `models/drain3/drain3_state.bin` |

> **Why `-1` for unknown templates:** Template IDs start at 0 (`_next_id = 0`), so returning 0 for unknowns would silently misattribute unknown logs to the first real template. `-1` is naturally excluded by `FeatureEngineer`'s `for tid in range(num_templates)` loop, correctly contributing zero signal to the feature vector.

**Gotcha:** `num_templates` is the `input_dim` for `LSTMAutoencoder`. It grows as new templates are seen and is finalized only after parsing the entire training corpus. Do not create the model until after all training data has been parsed. Expected range: **50–200 unique templates** for OTel Demo; **20–50 unique templates** for HDFS (its log format is much simpler).

---

## 3. Window & Feature Configuration

### Windowing Parameters

| Parameter | Value | Notes |
|---|---|---|
| Window size | 60 seconds | Balances detection speed vs. noise smoothing |
| Sequence length | 10 windows (10 minutes) | Input shape to LSTM-AE: `(batch, 10, feature_dim)` |
| Overlap | None (non-overlapping windows) | Simplicity; avoids leakage between windows |
| Aggregation | Log template count vector + metric stats | See feature breakdown below |

### Feature Vector Composition (`FeatureEngineer`)

Per window (system-wide, across all services and all active metrics):

| Feature group | Size | Description |
|---|---|---|
| Log template count | `num_templates` | Raw occurrence count of each template ID in the window |
| Log template frequency | `num_templates` | Normalized: `count / max(total_logs, 1)` |
| Error template ratio | 1 | Count of error-indicating templates / total logs |
| Unique template count | 1 | Number of distinct template IDs observed in the window |
| Metric mean | 1 per metric | Mean of Prometheus metric value over the window |
| Metric std | 1 per metric | Standard deviation |
| Metric min | 1 per metric | Minimum observed value |
| Metric max | 1 per metric | Maximum observed value |
| Metric p50 | 1 per metric | 50th percentile |
| Metric p99 | 1 per metric | 99th percentile |
| Metric delta | 1 per metric | Change from previous window (current mean − prior mean) |

**Total feature_dim** = `(num_templates × 2 + 2) + (num_metrics × 7)`

For a typical run with 100 templates and 5 metrics:
`(100 × 2 + 2) + (5 × 7) = 202 + 35 = 237`

> **Design note:** Features are computed system-wide rather than replicated per service. The LSTM-AE's role is binary — detect "something is wrong" and trigger the agent. The agent then uses its tools (`query_metrics`, `search_logs`, `discover_causation`) to pinpoint the specific service. Per-service feature replication would produce a ~1,300-dim vector that is unnecessarily large for 24h of training data.

This is the `input_dim` passed to `LSTMAutoencoder` during OTel Demo fine-tuning. It will almost certainly differ from the `input_dim` during HDFS pretraining (which is `num_templates` only — no metric features, no extra log features).

---

## 4. LSTM-Autoencoder Architecture (`src/anomaly_detection/lstm_autoencoder.py`)

### Design Rationale

Unsupervised reconstruction-based anomaly detection is appropriate here because:
- Normal operation data is abundant (24h baseline); fault-labeled data is deliberately rare (only from injection)
- The model trains on normal sequences only; anomalies produce high reconstruction error because the model has never seen those patterns
- Directly comparable to DeepLog (LSTM-based) and contrast with LogRobust (supervised Bi-LSTM) for the HDFS benchmark

### Architecture

```
Input Sequence  →  (batch, seq_len=10, input_dim)
        │
        ▼
Embedding Layer  →  Linear(input_dim, 32)           # project log template one-hot to dense
        │
        ▼
LSTM Encoder (2 layers, hidden=64, dropout=0.2)      # seq_len=10 → last hidden state
        │
        ▼
Latent Projection  →  Linear(64, 16)                 # bottleneck
        │
        ▼
Repeat Vector  →  unsqueeze + expand to (batch, 10, 64)
        │
        ▼
LSTM Decoder (2 layers, hidden=64, dropout=0.2)      # reconstruct sequence
        │
        ▼
Output Layer  →  Linear(64, input_dim)               # back to original feature space
        │
        ▼
Reconstructed Sequence  →  (batch, seq_len=10, input_dim)
```

### Model Parameters

| Parameter | Value | Rationale |
|---|---|---|
| `input_dim` | Dynamic (num_templates or feature_dim) | Set from `LogParser.num_templates` after corpus parse |
| `embedding_dim` | 32 | Sufficient for template ID encoding |
| `hidden_dim` | 64 | Balance between capacity and training speed |
| `latent_dim` | 16 | Compressed representation; bottleneck forces generalization |
| `num_layers` | 2 | Standard for sequence modeling |
| `dropout` | 0.2 | Applied between LSTM layers; disabled during `model.eval()` |
| `seq_len` | 10 | Must match windowing `sequence_length` |

### Full Class Implementation

```python
# src/anomaly_detection/lstm_autoencoder.py
import torch
import torch.nn as nn

class LSTMAutoencoder(nn.Module):
    def __init__(
        self,
        input_dim: int,
        embedding_dim: int = 32,
        hidden_dim: int = 64,
        latent_dim: int = 16,
        num_layers: int = 2,
        dropout: float = 0.2,
        seq_len: int = 10,
    ):
        super().__init__()
        self.seq_len = seq_len
        self.embedding = nn.Linear(input_dim, embedding_dim)
        self.encoder = nn.LSTM(
            input_size=embedding_dim, hidden_size=hidden_dim,
            num_layers=num_layers, batch_first=True, dropout=dropout,
        )
        self.to_latent = nn.Linear(hidden_dim, latent_dim)
        self.from_latent = nn.Linear(latent_dim, hidden_dim)
        self.decoder = nn.LSTM(
            input_size=hidden_dim, hidden_size=hidden_dim,
            num_layers=num_layers, batch_first=True, dropout=dropout,
        )
        self.output_layer = nn.Linear(hidden_dim, input_dim)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x: (batch, seq_len, input_dim)
        x = self.embedding(x)                           # → (batch, seq_len, 32)
        _, (hidden, _) = self.encoder(x)               # hidden: (num_layers, batch, 64)
        latent = self.to_latent(hidden[-1])             # → (batch, 16)
        dec_in = self.from_latent(latent)               # → (batch, 64)
        dec_in = dec_in.unsqueeze(1).repeat(1, self.seq_len, 1)  # → (batch, seq_len, 64)
        dec_out, _ = self.decoder(dec_in)               # → (batch, seq_len, 64)
        return self.output_layer(dec_out)               # → (batch, seq_len, input_dim)

    def get_reconstruction_error(self, x: torch.Tensor) -> torch.Tensor:
        """Mean squared error per sequence — scalar per batch item."""
        reconstructed = self.forward(x)
        return torch.mean((x - reconstructed) ** 2, dim=(1, 2))  # → (batch,)
```

---

## 5. Two-Phase Training Strategy (`src/anomaly_detection/pretrain_on_loghub.py`)

### Why Two Phases?

HDFS has ~11M log lines — far more data than 24h of OTel Demo baseline. Pretraining on HDFS lets the model learn robust sequence representations from a large, well-labeled distributed system log corpus before specializing to the OTel Demo environment with a smaller fine-tuning dataset. This mirrors standard transfer learning practice and provides a concrete, quantifiable benefit via the HDFS F1 benchmark.

### Phase 1 — Pretraining on LogHub HDFS

| Parameter | Value |
|---|---|
| Dataset | `data/LogHub/HDFS/` (normal blocks only via `get_normal_sequences()`) |
| Data split | 80% train / 20% val of normal sequences (`random_state=42`) |
| Loss | MSE |
| Optimizer | Adam, lr=0.001 |
| Batch size | 64 |
| Epochs | 50 (early stopping patience=5) |
| Platform | Google Colab Pro (T4/L4 GPU) |
| Output | `models/lstm_autoencoder/pretrained_hdfs.pt` |
| Expected convergence | Val loss plateaus within 20–40 epochs |

### Phase 2 — Fine-tuning on OTel Demo

| Parameter | Value |
|---|---|
| Dataset | `data/baseline/` windows (normal operation only) |
| Data split | 80% train / 20% val (`random_state=42`) — test set is the fault injection evaluation suite |
| Initialization | Loads `pretrained_hdfs.pt`; falls back to `_load_compatible_weights()` on dim mismatch |
| Loss | MSE |
| Optimizer | Adam, lr=0.0001 (10× lower than pretraining to preserve representations) |
| Batch size | 32 |
| Epochs | 30 (early stopping patience=10) |
| Platform | Google Colab Pro |
| Output | `models/lstm_autoencoder/finetuned_otel.pt` |

### The `_load_compatible_weights()` Function — Critical Implementation Detail

The HDFS and OTel Demo datasets will almost certainly have **different `input_dim`** values because:
- HDFS `input_dim` = `num_templates` from HDFS parsing (~20–50)
- OTel Demo `input_dim` = full `feature_dim` from `FeatureEngineer` (~150–450; see §3 formula)

When dimensions differ, PyTorch's `load_state_dict()` throws `RuntimeError`. The fix: load only the LSTM encoder/decoder weights (which are dimension-agnostic once the embedding layer maps to `hidden_dim=64`) and reinitialize the embedding and output layers with the new `input_dim`.

```python
def _load_compatible_weights(model: LSTMAutoencoder, checkpoint_path: str) -> None:
    """Load only LSTM body weights from checkpoint; skip I/O layers if dims differ."""
    checkpoint = torch.load(checkpoint_path, map_location="cpu")
    # Handle both raw state_dict and wrapped {'model_state_dict': ..., 'history': ...} formats
    if isinstance(checkpoint, dict) and "model_state_dict" in checkpoint:
        checkpoint = checkpoint["model_state_dict"]
    model_state = model.state_dict()

    compatible = {
        k: v for k, v in checkpoint.items()
        if k in model_state
        and "embedding" not in k      # skip: input_dim dependent
        and "output_layer" not in k   # skip: input_dim dependent
        and v.shape == model_state[k].shape
    }

    model_state.update(compatible)
    model.load_state_dict(model_state)
    print(f"_load_compatible_weights: loaded {len(compatible)} tensors from checkpoint.")
    print(f"  Skipped: embedding, output_layer (input_dim mismatch expected).")
    print(f"  LSTM encoder/decoder weights successfully transferred.")
```

**Expected output:** ~8–12 tensors loaded (LSTM weight matrices + biases for encoder and decoder, latent projections). The embedding and output layers are randomly reinitialized and trained from scratch on OTel Demo data.

### Full Training Pipeline Code

```python
# src/anomaly_detection/pretrain_on_loghub.py
import torch.nn.functional as F

def pretrain_on_hdfs(
    hdfs_data_path: str,
    model_save_path: str,
    parser: LogParser | None = None,
) -> tuple[LSTMAutoencoder, LogParser]:
    """
    Pretrain LSTM-AE on LogHub HDFS normal sequences.

    Returns:
        (model, parser) — parser is returned so the SAME instance can be passed
        to LogHubHDFSPreprocessor during OTel Demo fine-tuning and live inference,
        ensuring consistent template IDs across all three stages.
    """
    if parser is None:
        parser = LogParser()

    preprocessor = LogHubHDFSPreprocessor(hdfs_data_path, parser=parser)
    preprocessor.parse()
    normal_seqs = preprocessor.get_normal_sequences()  # shape: (N, seq_len) — integer IDs

    train_seqs, val_seqs = train_test_split(normal_seqs, test_size=0.2, random_state=42)

    n_templates = preprocessor.num_templates
    # One-hot encode integer template IDs → (N, seq_len, n_templates) float tensors
    train_enc = F.one_hot(torch.LongTensor(train_seqs), num_classes=n_templates).float().numpy()
    val_enc   = F.one_hot(torch.LongTensor(val_seqs),   num_classes=n_templates).float().numpy()

    model = LSTMAutoencoder(input_dim=n_templates)
    trainer = AnomalyTrainer(model)
    trainer.train(train_enc, val_enc, epochs=50, batch_size=64,
                  learning_rate=0.001, early_stopping_patience=5)

    torch.save(model.state_dict(), model_save_path)
    return model, parser


def finetune_on_otel_demo(
    pretrained_model_path: str, otel_data: dict, model_save_path: str
) -> LSTMAutoencoder:
    model = LSTMAutoencoder(input_dim=otel_data["input_dim"])

    try:
        model.load_state_dict(torch.load(pretrained_model_path, map_location="cpu"))
        print("Full weight transfer succeeded (same input_dim).")
    except RuntimeError:
        print("Input dim mismatch — partial weight transfer via _load_compatible_weights.")
        _load_compatible_weights(model, pretrained_model_path)

    trainer = AnomalyTrainer(model)
    trainer.train(otel_data["train"], otel_data["val"], epochs=30, batch_size=32,
                  learning_rate=0.0001, early_stopping_patience=10)

    torch.save(model.state_dict(), model_save_path)
    return model
```

---

## 6. Anomaly Threshold Calculation (`src/anomaly_detection/threshold.py`)

```python
# src/anomaly_detection/threshold.py
import torch
import numpy as np
from torch.utils.data import DataLoader, TensorDataset

def calculate_threshold(
    model: torch.nn.Module,
    baseline_sequences: np.ndarray,
    percentile: int = 95,
    batch_size: int = 256,
    device: str = "cpu",
) -> float:
    """
    Calculate anomaly detection threshold from normal baseline reconstruction errors.

    Uses the 95th percentile of reconstruction error on the OTel Demo 24h baseline.
    Sequences above this threshold during inference are flagged as anomalous.

    Args:
        model: Trained LSTMAutoencoder (finetuned_otel.pt loaded)
        baseline_sequences: Normal operation windows, shape (N, seq_len, feature_dim)
        percentile: Threshold percentile (95 → 5% false positive rate on baseline)

    Returns:
        Threshold float value; save to configs/model_config.yaml for reproducibility
    """
    model.eval()
    model.to(device)

    dataset = TensorDataset(torch.FloatTensor(baseline_sequences))
    loader = DataLoader(dataset, batch_size=batch_size)

    errors = []
    with torch.no_grad():
        for (batch,) in loader:
            batch = batch.to(device)
            error = model.get_reconstruction_error(batch)
            errors.extend(error.cpu().numpy())

    threshold = float(np.percentile(errors, percentile))
    print(f"Threshold ({percentile}th percentile): {threshold:.6f}")
    print(f"Error distribution: mean={np.mean(errors):.6f}, std={np.std(errors):.6f}")
    return threshold
```

**Threshold calibration for HDFS benchmark (Stage 3):** When evaluating the fine-tuned OTel model on HDFS sequences, the OTel-calibrated threshold won't apply. Recalibrate using a held-out 20% of HDFS normal sequences (not seen during pretraining), following the same 95th percentile method.

---

## 7. Isolation Forest Baseline (`src/anomaly_detection/isolation_forest.py`)

The Isolation Forest is the "Anomaly Detection Only" baseline — it detects anomalies without any investigation phase, used to demonstrate OpsAgent's full-pipeline advantage.

```python
# src/anomaly_detection/isolation_forest.py
from sklearn.ensemble import IsolationForest
import numpy as np

class IsolationForestDetector:
    def __init__(self, n_estimators: int = 100, contamination: float = 0.01,
                 max_samples: int = 256, random_state: int = 42):
        self.model = IsolationForest(
            n_estimators=n_estimators,
            contamination=contamination,   # ~1% anomaly rate expected during eval
            max_samples=max_samples,
            random_state=random_state,
            n_jobs=-1,
        )

    def fit(self, X: np.ndarray) -> None:
        """X shape: (N, feature_dim) — flattened sequence vectors."""
        self.model.fit(X)

    def predict(self, X: np.ndarray) -> np.ndarray:
        """Returns +1 (normal) or -1 (anomaly). Convert to bool: preds == -1."""
        return self.model.predict(X)

    def score_samples(self, X: np.ndarray) -> np.ndarray:
        """Anomaly scores: more negative = more anomalous."""
        return self.model.score_samples(X)
```

**Note on input shape:** Isolation Forest takes 2D input. Flatten the `(N, seq_len, feature_dim)` tensors to `(N, seq_len * feature_dim)` before fitting.

---

## 8. `AnomalyDetector` — Fast Loop / Slow Loop Bridge (`src/anomaly_detection/detector.py`)

`AnomalyDetector` is the **critical bridging component** between the two loops. It runs continuously, scoring each incoming window sequence against the threshold. When the score exceeds the threshold, it fires an alert dict to `AgentExecutor.investigate()`, triggering the Slow Loop.

```python
# src/anomaly_detection/detector.py
import time
from datetime import datetime, timezone
from typing import Callable

import numpy as np
import torch

from src.anomaly_detection.lstm_autoencoder import LSTMAutoencoder


class AnomalyDetector:
    """
    Real-time anomaly detection service.

    Wraps the fine-tuned LSTM-AE and threshold. Scores each incoming window
    sequence; fires an alert callback when reconstruction error exceeds threshold.

    Usage:
        detector = AnomalyDetector(model, threshold=0.042, on_anomaly=agent.investigate)
        detector.score(sequence)   # call per window from the main inference loop
    """

    def __init__(
        self,
        model: LSTMAutoencoder,
        threshold: float,
        on_anomaly: Callable[[dict], None],
        affected_services: list[str] | None = None,
        device: str = "cpu",
    ):
        self.model = model.to(device)
        self.model.eval()
        self.threshold = threshold
        self.on_anomaly = on_anomaly          # callback → AgentExecutor.investigate()
        self.affected_services = affected_services or []
        self.device = device

    def score(self, sequence: np.ndarray) -> float:
        """
        Score one window sequence. Returns reconstruction error (float).
        Fires on_anomaly callback if score > threshold.

        Args:
            sequence: np.ndarray of shape (seq_len, feature_dim) — one window sequence.
        """
        tensor = torch.FloatTensor(sequence).unsqueeze(0).to(self.device)  # (1, seq_len, feat)
        with torch.no_grad():
            error = self.model.get_reconstruction_error(tensor).item()

        if error > self.threshold:
            alert = {
                "title":             "LSTM-AE Anomaly Detected",
                "severity":          "high",
                "timestamp":         datetime.now(timezone.utc).isoformat(),
                "affected_services": self.affected_services,
                "anomaly_score":     round(error, 6),
                "threshold":         round(self.threshold, 6),
            }
            self.on_anomaly(alert)

        return error
```

**Key integration points:**
- **Instantiated by:** `src/serving/api.py` at startup — loads `finetuned_otel.pt` and threshold from `configs/model_config.yaml`
- **`on_anomaly` callback:** bound to `AgentExecutor.investigate()` — the alert dict is the initial state for the LangGraph agent
- **`anomaly_score` key:** always included in the alert dict so `_format_alert` in `AgentExecutor` can surface it to the LLM
- **Called per window by:** the main data collection loop in `detector.py`'s `run()` method (not shown above — implement as a `while True` polling loop over `FeatureEngineer.build_sequence()` output)

---

## 9. LogHub HDFS Preprocessor (`src/preprocessing/loghub_preprocessor.py`)

### Dataset Facts

| Property | Value |
|---|---|
| Source | IEEE ISSRE 2023 / Zenodo DOI: `10.5281/zenodo.8196385` |
| Files required | `data/LogHub/HDFS/HDFS.log` (~1GB), `data/LogHub/HDFS/anomaly_label.csv` (~475KB) |
| Total log lines | ~11.2 million |
| Block labeling unit | `blk_<id>` — each line belongs to one or more blocks |
| Block anomaly rate | ~2.9% (blocks marked "Anomaly" in labels file) |
| Expected templates | 20–50 unique Drain3 templates (HDFS log format is very structured) |
| Sequence length | 10 template IDs per block-window (matches OTel Demo `seq_len`) |

### Block-to-Sequence Logic

HDFS logs are labeled at the **block level**, not at the line level. The preprocessor:
1. Parses each log line through Drain3 → gets `template_id`
2. Extracts all `blk_<id>` references in the line via regex
3. Groups `template_id` values by `block_id`
4. Reads `anomaly_label.csv` → maps `block_id → 0 (Normal) or 1 (Anomaly)`
5. Converts block logs to fixed-length sequences: chunk by `seq_length=10`, zero-pad short blocks

> **Full implementation:** `context/data_pipeline_specs.md` — §5 `LogHubHDFSPreprocessor`.
> Load that file when implementing `src/preprocessing/loghub_preprocessor.py`.
>
> **Critical:** `LogHubHDFSPreprocessor` accepts a `parser: LogParser` parameter. Always pass
> the same shared `LogParser` instance used by the OTel Demo pipeline — never create a new one
> inside the class. This is the same rule documented in the CLAUDE.md gotchas table.

### Validation Checks (Run Before Proceeding to Training)

```python
from src.preprocessing.log_parser import LogParser

parser = LogParser()  # shared instance — also used by OTel Demo pipeline and live inference
preprocessor = LogHubHDFSPreprocessor("data/LogHub/HDFS/", parser=parser, seq_length=10)
preprocessor.parse()

normal = preprocessor.get_normal_sequences()
anomalous = preprocessor.get_anomalous_sequences()

print(f"Normal sequences:    {normal.shape}")      # expected: (~540k, 10)
print(f"Anomalous sequences: {anomalous.shape}")   # expected: (~16k, 10)
print(f"Anomaly rate: {len(anomalous)/(len(normal)+len(anomalous)):.1%}")  # ~2.9%
print(f"Unique templates:    {preprocessor.num_templates}")   # expected: 20-50
```

---

## 10. AnomalyTrainer (`src/anomaly_detection/trainer.py`)

Key training behaviors to implement correctly:

1. **Training mode vs eval mode:** Call `model.train()` before training loop, `model.eval()` before validation. Dropout is active only in training mode.
2. **Loss on reconstructed vs input:** `criterion(output, batch)` — both have same shape `(batch, seq_len, input_dim)`.
3. **Early stopping saves the best checkpoint, not the final one.** Load `best_model.pt` after training finishes.
4. **Checkpoint format:**
   ```python
   torch.save({'model_state_dict': model.state_dict(), 'history': trainer.history}, path)
   # Load: checkpoint = torch.load(path); model.load_state_dict(checkpoint['model_state_dict'])
   ```

```python
# src/anomaly_detection/trainer.py (condensed)
class AnomalyTrainer:
    def __init__(self, model, device="cuda" if torch.cuda.is_available() else "cpu"):
        self.model = model.to(device)
        self.device = device
        self.history = {"train_loss": [], "val_loss": []}

    def train(self, train_sequences, val_sequences, epochs=100, batch_size=32,
              learning_rate=0.001, early_stopping_patience=10):
        train_loader = DataLoader(TensorDataset(torch.FloatTensor(train_sequences)),
                                  batch_size=batch_size, shuffle=True)
        val_loader = DataLoader(TensorDataset(torch.FloatTensor(val_sequences)),
                                batch_size=batch_size)
        optimizer = torch.optim.Adam(self.model.parameters(), lr=learning_rate)
        criterion = torch.nn.MSELoss()
        best_val_loss, patience_counter = float("inf"), 0

        for epoch in range(epochs):
            self.model.train()
            train_loss = sum(
                self._step(batch, optimizer, criterion, training=True)
                for (batch,) in train_loader
            ) / len(train_loader)

            self.model.eval()
            with torch.no_grad():
                val_loss = sum(
                    criterion(self.model(b.to(self.device)), b.to(self.device)).item()
                    for (b,) in val_loader
                ) / len(val_loader)

            self.history["train_loss"].append(train_loss)
            self.history["val_loss"].append(val_loss)
            print(f"Epoch {epoch+1}/{epochs} — train: {train_loss:.6f}, val: {val_loss:.6f}")

            if val_loss < best_val_loss:
                best_val_loss, patience_counter = val_loss, 0
                self.save_checkpoint("models/lstm_autoencoder/best_model.pt")
            else:
                patience_counter += 1
                if patience_counter >= early_stopping_patience:
                    print(f"Early stopping at epoch {epoch+1}")
                    break

        return self.history
```

---

## 11. HDFS Anomaly Detection Benchmark

This is a **nice-to-have** evaluation run from `notebooks/05_anomaly_detection_dev.ipynb`. It validates the pretraining step by comparing reconstruction-based F1 scores at three training stages against published baselines on the same HDFS dataset.

### Three Benchmark Stages

| Stage | Model State | Threshold Calibration |
|---|---|---|
| 1 — Untrained | Random init | 95th percentile of HDFS normal hold-out |
| 2 — Pretrained | `pretrained_hdfs.pt` | 95th percentile of HDFS normal hold-out |
| 3 — Fine-tuned | `finetuned_otel.pt` | Recalibrate on held-out HDFS normal (OTel threshold does not transfer) |

### Published Baselines for Comparison (HDFS Dataset)

These F1 scores come from the LogHub benchmark paper (IEEE ISSRE 2023). The OpsAgent target is ≥ 0.90.

| Method | Approach | F1 (HDFS) | Notes |
|---|---|---|---|
| **DeepLog** (Du et al. 2017) | LSTM next-event forecasting (unsupervised) | 0.941 | Most-cited LSTM baseline; does not use semantic embeddings |
| **LogRobust** (Zhang et al. 2019) | Attention Bi-LSTM + FastText embeddings (supervised) | 0.978 | Requires anomaly labels at training time; stronger but not directly comparable to OpsAgent LSTM-AE |
| **LogBERT** (Guo et al. 2021) | BERT masked language modeling (semi-supervised) | 0.960 | Transformer-based; much larger model |
| **OpsAgent LSTM-AE** | Reconstruction-based LSTM-AE (unsupervised) | **target ≥ 0.90** | Two-phase training; general-purpose (not HDFS-specialized) |

**Important framing distinction:** DeepLog is the most directly comparable baseline — both are unsupervised LSTM approaches on template ID sequences. LogRobust is supervised (uses anomaly labels during training), making it a stronger but less directly comparable baseline. Reaching 0.90 F1 is a strong result for an unsupervised LSTM-AE that is also fine-tuned on a different system.

### Benchmark Code (in `notebooks/05_anomaly_detection_dev.ipynb`)

```python
from sklearn.model_selection import train_test_split
from sklearn.metrics import precision_score, recall_score, f1_score

import torch.nn.functional as F

parser = LogParser()  # shared instance — also passed to OTel pipeline
preprocessor = LogHubHDFSPreprocessor("data/LogHub/HDFS/", parser=parser, seq_length=10)
sequences, labels = preprocessor.get_labeled_sequences()  # sequences shape: (N, 10) int32

# Hold out 20% of normal sequences for threshold calibration (not seen during pretraining)
normal_mask = labels == 0
normal_seqs = sequences[normal_mask]
_, threshold_seqs = train_test_split(normal_seqs, test_size=0.2, random_state=42)

def evaluate_model(model_path: str, input_dim: int) -> dict:
    model = LSTMAutoencoder(input_dim=input_dim)
    model.load_state_dict(torch.load(model_path, map_location="cpu"))
    model.eval()

    # One-hot encode integer template IDs: (N, 10) → (N, 10, input_dim) float
    threshold_enc = F.one_hot(
        torch.LongTensor(threshold_seqs), num_classes=input_dim
    ).float().numpy()
    sequences_enc = F.one_hot(
        torch.LongTensor(sequences), num_classes=input_dim
    ).float()

    threshold = calculate_threshold(model, baseline_sequences=threshold_enc, percentile=95)

    errors = []
    with torch.no_grad():
        for seq in sequences_enc:
            err = model.get_reconstruction_error(seq.unsqueeze(0))
            errors.append(err.item())

    y_pred = (np.array(errors) > threshold).astype(int)
    return {
        "precision": precision_score(labels, y_pred),
        "recall":    recall_score(labels, y_pred),
        "f1":        f1_score(labels, y_pred),
    }

stage2 = evaluate_model("models/lstm_autoencoder/pretrained_hdfs.pt", preprocessor.num_templates)
print("Stage 2 (Pretrained):", stage2)
# Stage 3: load finetuned_otel.pt; input_dim will differ (OTel templates ≫ HDFS templates)
# Recalibrate threshold from held-out HDFS normal sequences using same one-hot pattern above.
```

---

## 12. Source Files Reference

| File | Class / Function | Description |
|---|---|---|
| `src/preprocessing/log_parser.py` | `LogParser` | Drain3 wrapper; shared across pipeline |
| `src/preprocessing/windowing.py` | `WindowAggregator` | 60s non-overlapping windows |
| `src/preprocessing/feature_engineering.py` | `FeatureEngineer` | Log + metric feature vectors |
| `src/preprocessing/loghub_preprocessor.py` | `LogHubHDFSPreprocessor` | HDFS block sequencing |
| `src/anomaly_detection/lstm_autoencoder.py` | `LSTMAutoencoder` | Model definition |
| `src/anomaly_detection/trainer.py` | `AnomalyTrainer` | Training loop with early stopping |
| `src/anomaly_detection/pretrain_on_loghub.py` | `pretrain_on_hdfs`, `finetune_on_otel_demo`, `_load_compatible_weights` | Two-phase workflow |
| `src/anomaly_detection/threshold.py` | `calculate_threshold` | 95th percentile threshold |
| `src/anomaly_detection/isolation_forest.py` | `IsolationForestDetector` | Baseline model |
| `src/anomaly_detection/detector.py` | `AnomalyDetector` | Fast Loop / Slow Loop bridge — scores each window, fires alert to `AgentExecutor` |
| `models/lstm_autoencoder/pretrained_hdfs.pt` | — | Phase 1 checkpoint |
| `models/lstm_autoencoder/finetuned_otel.pt` | — | Phase 2 checkpoint (used at inference) |
| `notebooks/05_anomaly_detection_dev.ipynb` | — | All training + benchmark runs (Colab) |

## 13. Common Gotchas

| Gotcha | Symptom | Fix |
|---|---|---|
| `LogParser` instance not shared | Template IDs inconsistent between HDFS pretraining and OTel fine-tuning | Pass the same `parser` object to both `LogHubHDFSPreprocessor` and the main pipeline |
| Model created before corpus parse complete | `input_dim` too small; `KeyError` on template IDs above the initial dim | Always call `preprocessor.parse()` first, then `preprocessor.num_templates` → `LSTMAutoencoder(input_dim=...)` |
| `RuntimeError` on `load_state_dict` | Input dim mismatch between HDFS (~20–50) and OTel (~150–450) | This is expected and handled by `_load_compatible_weights()` — not a bug |
| LSTM dropout warning on single layer | PyTorch warns if `num_layers=1` and `dropout>0` | Use `num_layers=2` as configured; do not reduce layers |
| Threshold too low | Too many false positives during normal operation | Use `percentile=95` not `percentile=50`; recalculate on fresh baseline data |
| `model.eval()` forgotten at inference | Dropout active during inference → noisy reconstruction errors → unstable threshold | Always set `model.eval()` and wrap in `torch.no_grad()` during threshold calculation and inference |
| HDFS download stalls | ~1GB download can timeout on slow connections | Use `scripts/download_datasets.py --loghub`; download during the 24h OTel baseline collection window |
