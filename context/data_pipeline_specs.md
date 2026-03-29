# Data Pipeline Specifications

**Implementation files:**
- `src/data_collection/kafka_consumer.py` — Kafka log ingestion
- `src/preprocessing/log_parser.py` — Drain3 wrapper (shared across datasets)
- `src/preprocessing/windowing.py` — Time-window aggregation
- `src/preprocessing/feature_engineering.py` — Feature vector construction
- `src/preprocessing/loghub_preprocessor.py` — LogHub HDFS parser + sequencer
- `src/preprocessing/rcaeval_adapter.py` — RCAEval → OpsAgent format converter
- `src/data_collection/topology_extractor.py` — Service dependency graph
- `src/knowledge_base/runbook_indexer.py` — ChromaDB runbook indexing + search
- `tests/evaluation/rcaeval_evaluation.py` — Cross-system evaluation runner
- `scripts/download_datasets.py` — Dataset download automation
- `runbooks/*.md` — Custom runbook markdown files

**Data flow overview:**
```
Kafka (logs) ──► Drain3 ──► WindowAggregator ──► FeatureEngineer ──► LSTM-AE
Prometheus (metrics) ──────────────────────────► FeatureEngineer ──► LSTM-AE

LogHub HDFS ──► LogHubHDFSPreprocessor ──► normal sequences ──► LSTM-AE pretraining
RCAEval RE1/RE2/RE3 ──► RCAEvalDataAdapter ──► agent.investigate() ──► evaluation
```

---

## 1. Kafka Configuration & `LogConsumer`

### 1.1 Kafka Configuration

| Parameter | Value | Notes |
|---|---|---|
| Bootstrap servers | `localhost:9092` | Single broker for local development |
| Topic name | `opsagent-logs` | All OTel Demo service logs stream here |
| Partitions | 3 | Parallel consumption across 3 partitions |
| Replication factor | 1 | Single broker; no replication needed locally |
| Retention | 24 hours | Keeps recent logs for replay and analysis |
| Group ID | `opsagent-consumer` | Enables offset tracking for resumable consumption |

```yaml
# docker-compose.yml (Kafka excerpt)
services:
  zookeeper:
    image: confluentinc/cp-zookeeper:7.5.0
    environment:
      ZOOKEEPER_CLIENT_PORT: 2181

  kafka:
    image: confluentinc/cp-kafka:7.5.0
    depends_on: [zookeeper]
    ports:
      - "9092:9092"
    environment:
      KAFKA_BROKER_ID: 1
      KAFKA_ZOOKEEPER_CONNECT: zookeeper:2181
      KAFKA_ADVERTISED_LISTENERS: PLAINTEXT://localhost:9092
      KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR: 1
      KAFKA_LOG_RETENTION_HOURS: 24
      KAFKA_AUTO_CREATE_TOPICS_ENABLE: "true"
```

### 1.2 `LogConsumer` Class

```python
# src/data_collection/kafka_consumer.py
from kafka import KafkaConsumer
from typing import Iterator, Dict, Any
import json


class LogConsumer:
    """Consume log entries from the opsagent-logs Kafka topic."""

    def __init__(
        self,
        bootstrap_servers: str = "localhost:9092",
        topic: str = "opsagent-logs",
        group_id: str = "opsagent-consumer",
    ):
        self.consumer = KafkaConsumer(
            topic,
            bootstrap_servers=bootstrap_servers,
            group_id=group_id,
            auto_offset_reset="earliest",         # Start from beginning if no committed offset
            enable_auto_commit=True,
            auto_commit_interval_ms=1000,
            value_deserializer=lambda m: json.loads(m.decode("utf-8")),
        )

    def consume(self) -> Iterator[Dict[str, Any]]:
        """
        Yield log entries from Kafka indefinitely.

        Each yielded dict contains:
            timestamp:  int   — Unix epoch milliseconds (Kafka message timestamp)
            partition:  int   — Kafka partition number
            offset:     int   — Message offset within partition
            value:      dict  — Deserialized log payload:
                                {timestamp, service, level, message, trace_id}
        """
        for message in self.consumer:
            yield {
                "timestamp": message.timestamp,
                "partition": message.partition,
                "offset": message.offset,
                "value": message.value,
            }

    def close(self):
        self.consumer.close()
```

> **Note:** `kafka-python` is the Python client (managed via `pyproject.toml`). The OTel Demo services must be configured to emit logs to this Kafka topic — a Kafka log appender is wired in as part of the Docker Compose setup.

---

## 2. Drain3 Log Parser

### 2.1 Drain3 Configuration

| Parameter | Value | Rationale |
|---|---|---|
| `drain_depth` | 4 | Parse tree depth; 4 balances specificity vs. generalization |
| `drain_sim_th` | 0.4 | Similarity threshold; below this → new template created |
| `drain_max_children` | 100 | Max children per parse-tree node; prevents memory explosion |
| `parametrize_numeric_tokens` | `True` | Replaces numbers with `<*>` for better template reuse |
| Persistence | `FilePersistence("models/drain3/drain3_state.bin")` | Saves learned templates across restarts |
| Input | **Unstructured portion only** | Strip timestamps/hostname/severity before passing to Drain3 |

> **Drain3 best practice:** Feed only the free-text message portion (without structured headers like timestamps or log levels). This significantly improves template mining accuracy and reduces template explosion.

### 2.2 `LogParser` Class

```python
# src/preprocessing/log_parser.py
from drain3 import TemplateMiner
from drain3.template_miner_config import TemplateMinerConfig
from drain3.file_persistence import FilePersistence
from pathlib import Path
from typing import Dict, Tuple


class LogParser:
    """
    Drain3 wrapper for online log template extraction.

    This single class is reused across ALL datasets:
      - OTel Demo logs (primary pipeline, real-time via Kafka)
      - LogHub HDFS logs (pretraining preprocessing)

    Sharing one LogParser instance builds a unified template vocabulary,
    ensuring template IDs are consistent between pretraining (HDFS)
    and fine-tuning (OTel Demo). If input_dim mismatches, the
    _load_compatible_weights() helper in pretrain_on_loghub.py handles it.
    """

    def __init__(self, persistence_path: str = "models/drain3/"):
        Path(persistence_path).mkdir(parents=True, exist_ok=True)

        config = TemplateMinerConfig()
        config.drain_depth = 4
        config.drain_sim_th = 0.4
        config.drain_max_children = 100
        config.parametrize_numeric_tokens = True

        persistence = FilePersistence(f"{persistence_path}/drain3_state.bin")
        self.template_miner = TemplateMiner(persistence, config)

        # Internal bi-directional mappings: template string ↔ integer ID
        self.template_to_id: Dict[str, int] = {}
        self.id_to_template: Dict[int, str] = {}
        self._next_id: int = 0

    def parse(self, log_line: str) -> Tuple[int, str]:
        """
        Parse a raw log line and return its template ID and template string.

        Args:
            log_line: Raw log message. Strip timestamps/severity first for best results.
                      Example: "Failed to connect to redis:6379 after 3 retries"

        Returns:
            (template_id, template_string)
            Example: (7, "Failed to connect to <*> after <*> retries")

        Note:
            add_log_message() returns a dict with keys:
              - change_type: "created" | "updated" | "none"
              - cluster_id:  Drain3's internal cluster ID (not used; we assign our own int IDs)
              - template_mined: The extracted template string
        """
        result = self.template_miner.add_log_message(log_line)
        template = result["template_mined"]

        if template not in self.template_to_id:
            self.template_to_id[template] = self._next_id
            self.id_to_template[self._next_id] = template
            self._next_id += 1

        template_id = self.template_to_id[template]
        return template_id, template

    def match(self, log_line: str) -> Tuple[int, str]:
        """
        Match a log line to an existing template without updating the miner.

        Used during inference (real-time Fast Loop and fine-tuning evaluation)
        to assign template IDs without growing the template vocabulary.

        Args:
            log_line: Raw log message (strip timestamps/severity first).

        Returns:
            (template_id, template_string)
            Returns (-1, "UNKNOWN") if no matching template is found.
            -1 is used rather than 0 because template IDs start at 0;
            using 0 would silently misattribute unknown logs to the first
            real template. -1 is naturally excluded by FeatureEngineer's
            `for tid in range(num_templates)` loop.

        Note:
            Unlike parse(), this method calls TemplateMiner.match() (read-only),
            which does NOT create new templates or update Drain3's internal state.
        """
        cluster = self.template_miner.match(log_line)
        if cluster is None:
            return -1, "UNKNOWN"

        template = cluster.get_template()
        if template not in self.template_to_id:
            return -1, "UNKNOWN"

        template_id = self.template_to_id[template]
        return template_id, template

    def get_template(self, template_id: int) -> str:
        """Reverse lookup: template ID → template string."""
        return self.id_to_template.get(template_id, "UNKNOWN")

    @property
    def num_templates(self) -> int:
        """Total number of unique templates discovered so far."""
        return len(self.template_to_id)

    def save(self):
        """Persist current Drain3 state to disk (called after bulk ingestion)."""
        self.template_miner.save_state("Drain3 template miner snapshot")
```

> **Key insight:** `cluster_id` returned by Drain3 is an internal alphanumeric ID (e.g., `"A0042"`). We assign our own monotonically increasing integer IDs (`template_id`) for use as embedding indices in the LSTM-Autoencoder. This mapping is stored in `template_to_id` and is what gets serialized and reloaded via `FilePersistence`.

---

## 3. Window Aggregation

### 3.1 Window Configuration

| Parameter | Value | Rationale |
|---|---|---|
| Window size | 60 seconds | Balances anomaly detection speed vs. noise reduction |
| Sequence length | 10 windows (10 minutes) | Input shape for LSTM-AE: `(10, feature_dim)` |
| Overlap | None (non-overlapping) | Simplicity; prevents data leakage |
| Aggregation | Log template count vector per window | Standard for log-based anomaly detection |

### 3.2 `WindowAggregator` Class

```python
# src/preprocessing/windowing.py
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import numpy as np


class WindowAggregator:
    """
    Aggregate streaming log events and metrics into fixed-size time windows.

    Used in the real-time Fast Loop pipeline. Each completed window is
    passed to FeatureEngineer to compute the feature vector fed to LSTM-AE.
    """

    def __init__(self, window_size_seconds: int = 60):
        self.window_size = timedelta(seconds=window_size_seconds)
        self.current_window_start: Optional[datetime] = None
        self.current_window_logs: List[Dict] = []
        self.current_window_metrics: Dict[str, List[float]] = defaultdict(list)

    def add_log(
        self,
        timestamp: datetime,
        template_id: int,
        service: str,
    ) -> Optional[Dict]:
        """
        Add a parsed log event to the current window.

        Returns the completed window dict if the timestamp crosses a window
        boundary, otherwise returns None (window still accumulating).
        """
        if self.current_window_start is None:
            self.current_window_start = self._floor_to_window(timestamp)

        window_start = self._floor_to_window(timestamp)

        if window_start != self.current_window_start:
            # Emit the completed window before rolling forward
            completed = self._finalize_window()
            self.current_window_start = window_start
            self.current_window_logs = []
            self.current_window_metrics = defaultdict(list)
            return completed

        self.current_window_logs.append(
            {"timestamp": timestamp, "template_id": template_id, "service": service}
        )
        return None

    def add_metric(self, metric_name: str, value: float):
        """Add a metric observation to the current window."""
        self.current_window_metrics[metric_name].append(value)

    def _floor_to_window(self, timestamp: datetime) -> datetime:
        """Floor a timestamp to the start of its window boundary."""
        epoch = datetime(1970, 1, 1)
        total_seconds = int((timestamp - epoch).total_seconds())
        window_seconds = int(self.window_size.total_seconds())
        floored = (total_seconds // window_seconds) * window_seconds
        return epoch + timedelta(seconds=floored)

    def _finalize_window(self) -> Dict:
        """Package the current window data into a dict for FeatureEngineer."""
        return {
            "window_start": self.current_window_start,
            "window_end": self.current_window_start + self.window_size,
            "logs": list(self.current_window_logs),
            "metrics": dict(self.current_window_metrics),
        }
```

---

## 4. Feature Engineering

### 4.1 Feature Tables

#### Log Template Features (per window, per service)

| Feature | Description | Dimension |
|---|---|---|
| Template count vector | Raw count of each template ID seen | `num_templates` |
| Template frequency vector | Normalized counts (count / total_logs) | `num_templates` |
| Error template ratio | Count of error-level templates / total | 1 |
| Unique template count | Number of distinct template IDs in window | 1 |

**Total log features per window:** `num_templates × 2 + 2`

#### Metric Features (per window, per service)

| Feature | Description |
|---|---|
| Mean | Average metric value over the window |
| Std | Standard deviation |
| Min | Minimum observed value |
| Max | Maximum observed value |
| P50 | 50th percentile |
| P99 | 99th percentile |
| Delta | Change from previous window (current mean − prior mean) |

**Total metric features per service per metric:** 7 values

### 4.2 `FeatureEngineer` Class

> **Scope:** `FeatureEngineer` is used exclusively in the **OTel Demo pipeline** (log templates +
> Prometheus metric stats → LSTM-AE input). It is **not** used during HDFS pretraining — the
> `LogHubHDFSPreprocessor` produces one-hot encoded sequences directly from template IDs,
> bypassing the feature engineering step entirely.

```python
# src/preprocessing/feature_engineering.py
from __future__ import annotations
from collections import Counter
from typing import TYPE_CHECKING, Dict, List, Optional
import numpy as np

if TYPE_CHECKING:
    from src.preprocessing.log_parser import LogParser


class FeatureEngineer:
    """
    Compute fixed-length feature vectors from windowed log + metric data.

    The feature vector is the input to the LSTM-Autoencoder.
    Shape of a single window vector: (feature_dim,)
    Shape of a sequence fed to LSTM-AE: (sequence_length, feature_dim)
    """

    # Keywords used to classify a Drain3 template as error-level
    ERROR_KEYWORDS: frozenset = frozenset({"error", "exception", "fail", "fatal", "critical", "severe"})

    def __init__(
        self,
        num_templates: int,
        services: List[str],
        metrics: List[str],
        parser: Optional["LogParser"] = None,
    ):
        self.num_templates = num_templates
        self.services = services
        self.metrics = metrics
        self.parser = parser                              # Optional; needed for error template ratio
        self._prev_metric_means: Dict[str, float] = {}

    def compute_features(self, window: Dict) -> np.ndarray:
        """
        Compute the feature vector for a single time window.

        Args:
            window: Dict from WindowAggregator._finalize_window()
                    Keys: window_start, window_end, logs, metrics

        Returns:
            1D numpy array of shape (feature_dim,)
        """
        features: List[float] = []
        logs = window.get("logs", [])
        metrics = window.get("metrics", {})

        # ── Log template features ────────────────────────────────────────
        template_ids = [log["template_id"] for log in logs]
        template_counts = Counter(template_ids)
        total_logs = max(len(logs), 1)

        for tid in range(self.num_templates):
            count = template_counts.get(tid, 0)
            features.append(float(count))                    # raw count
            features.append(float(count) / total_logs)      # normalized frequency

        # Error ratio: count templates whose text contains error-level keywords.
        # Requires parser reference for template string lookup; falls back to 0 if unavailable.
        if self.parser is not None:
            error_count = sum(
                count for tid, count in template_counts.items()
                if any(kw in self.parser.get_template(tid).lower() for kw in self.ERROR_KEYWORDS)
            )
        else:
            error_count = 0  # Wire in parser at construction time to enable this feature
        features.append(float(error_count) / total_logs)
        features.append(float(len(template_counts)))         # unique templates

        # ── Metric features ──────────────────────────────────────────────
        for metric_name in self.metrics:
            values = metrics.get(metric_name, [])
            if values:
                arr = np.array(values, dtype=float)
                mean_val = float(np.mean(arr))
                features.extend([
                    mean_val,
                    float(np.std(arr)),
                    float(np.min(arr)),
                    float(np.max(arr)),
                    float(np.percentile(arr, 50)),
                    float(np.percentile(arr, 99)),
                    mean_val - self._prev_metric_means.get(metric_name, mean_val),  # delta
                ])
                self._prev_metric_means[metric_name] = mean_val
            else:
                features.extend([0.0] * 7)

        return np.array(features, dtype=np.float32)

    def build_sequence(self, windows: List[Dict], sequence_length: int = 10) -> np.ndarray:
        """
        Build the (sequence_length, feature_dim) input tensor for LSTM-AE.

        Takes the last `sequence_length` windows from the provided list.

        Args:
            windows:         List of window dicts, ordered oldest → newest.
            sequence_length: Number of windows per LSTM-AE sequence (default 10).

        Returns:
            np.ndarray of shape (sequence_length, feature_dim)
        """
        if len(windows) < sequence_length:
            raise ValueError(
                f"Need at least {sequence_length} windows; got {len(windows)}"
            )
        feature_vecs = [self.compute_features(w) for w in windows[-sequence_length:]]
        return np.stack(feature_vecs)   # shape: (sequence_length, feature_dim)

    @property
    def feature_dim(self) -> int:
        """Total feature vector dimension for a single window."""
        log_features = self.num_templates * 2 + 2
        metric_features = len(self.metrics) * 7
        return log_features + metric_features
```

---

## 5. LogHub HDFS Preprocessor

### 5.1 Dataset Parameters

| Parameter | Value | Notes |
|---|---|---|
| Source | LogHub (IEEE ISSRE 2023) | Zenodo DOI: `10.5281/zenodo.8196385` |
| Download path | `data/LogHub/HDFS/` | Contains `HDFS.log` and `anomaly_label.csv` |
| Total log lines | ~11.2 million | Raw HDFS daemon log messages |
| Anomaly rate | ~2.9% at block level | Expected; verify during EDA |
| Grouping unit | Block ID (`blk_<id>`) extracted via regex | Each block becomes one sequence group |
| Sequence length | 10 log events per block | Matches OTel Demo window sequence length |
| Drain3 instance | **Shared `LogParser`** | Same instance as the main pipeline — critical for vocabulary alignment |

### 5.2 Drain3 Vocabulary Compatibility Note

The `LogHubHDFSPreprocessor` **reuses the same `LogParser` instance** as the main OTel Demo pipeline. This is intentional and critical:

- Template IDs assigned during HDFS pretraining are carried forward as-is into OTel Demo fine-tuning
- When OTel Demo introduces new templates not seen in HDFS, `num_templates` grows → `input_dim` of the LSTM-AE embedding layer changes
- The `_load_compatible_weights()` helper in `pretrain_on_loghub.py` handles this by reloading only the LSTM encoder/decoder weights while reinitializing the embedding and output projection layers

### 5.3 `LogHubHDFSPreprocessor` Full Class

```python
# src/preprocessing/loghub_preprocessor.py
from __future__ import annotations
import re
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split

from src.preprocessing.log_parser import LogParser


class LogHubHDFSPreprocessor:
    """
    Preprocess LogHub HDFS dataset for LSTM-Autoencoder pretraining.

    Workflow:
      1. Parse HDFS.log line-by-line using the shared Drain3 LogParser.
      2. Group extracted template IDs by block ID (blk_<id>).
      3. Load block-level anomaly labels from anomaly_label.csv.
      4. Convert block template-ID lists into fixed-length integer sequences.
      5. Expose normal sequences for pretraining; labeled sequences for benchmarking.
    """

    BLOCK_PATTERN = re.compile(r"(blk_-?\d+)")

    def __init__(
        self,
        data_dir: str,
        seq_length: int = 10,
        parser: Optional[LogParser] = None,
    ):
        """
        Args:
            data_dir:   Path to directory containing HDFS.log and anomaly_label.csv.
            seq_length: Fixed length of each output sequence (zero-padded if shorter).
            parser:     Shared LogParser instance. If None, a new one is created
                        (NOT recommended — pass the shared instance to maintain vocabulary).
        """
        self.data_dir = Path(data_dir)
        self.seq_length = seq_length
        self.parser = parser if parser is not None else LogParser()

        self._block_sequences: Dict[str, List[int]] = {}   # block_id → template_id list
        self._labels: Dict[str, int] = {}                  # block_id → 0 (normal) / 1 (anomaly)
        self._parsed: bool = False

    # ── Public API ───────────────────────────────────────────────────────

    def parse(self) -> None:
        """Parse HDFS.log and load labels. Must be called once before get_* methods."""
        self._parse_logs()
        self._load_labels()
        self._parsed = True

    def get_normal_sequences(self) -> np.ndarray:
        """
        Return fixed-length sequences from normal (non-anomalous) blocks.

        Returns:
            np.ndarray of shape (N, seq_length), dtype int32
        """
        self._ensure_parsed()
        return self._build_sequences(label_filter=0)

    def get_anomalous_sequences(self) -> np.ndarray:
        """
        Return fixed-length sequences from anomalous blocks.
        Used for optional threshold calibration or HDFS benchmark evaluation.

        Returns:
            np.ndarray of shape (M, seq_length), dtype int32
        """
        self._ensure_parsed()
        return self._build_sequences(label_filter=1)

    def get_labeled_sequences(self) -> Tuple[np.ndarray, np.ndarray]:
        """
        Return ALL sequences with binary labels for HDFS benchmark evaluation.

        Returns:
            Tuple of:
              sequences: np.ndarray shape (N, seq_length)
              labels:    np.ndarray shape (N,)  — 0=normal, 1=anomaly
        """
        self._ensure_parsed()
        normal = self._build_sequences(label_filter=0)
        anomalous = self._build_sequences(label_filter=1)
        sequences = np.vstack([normal, anomalous])
        labels = np.concatenate([
            np.zeros(len(normal), dtype=int),
            np.ones(len(anomalous), dtype=int),
        ])
        return sequences, labels

    @property
    def num_templates(self) -> int:
        """Number of unique Drain3 templates discovered across HDFS logs."""
        self._ensure_parsed()
        return self.parser.num_templates

    # ── Internal helpers ─────────────────────────────────────────────────

    def _parse_logs(self) -> None:
        """Read HDFS.log line-by-line; group template IDs by block ID."""
        log_path = self.data_dir / "HDFS.log"
        block_logs: Dict[str, List[int]] = defaultdict(list)

        # HDFS log format: "081109 203615 148 INFO dfs.DataNode$PacketResponder: blk_-..."
        # Strip the structured header (date, time, PID, severity, component) before
        # passing to Drain3; keep the original line for block ID extraction.
        HDFS_HEADER_PATTERN = re.compile(
            r"^\d{6}\s+\d{6}\s+\d+\s+\w+\s+[\w.$]+:\s*"
        )

        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue

                # Extract block ID from the original line before header stripping
                block_ids = self.BLOCK_PATTERN.findall(line)
                # Strip structured header; feed only free-text message to Drain3
                clean_line = HDFS_HEADER_PATTERN.sub("", line)
                template_id, _ = self.parser.parse(clean_line)

                for blk_id in block_ids:
                    block_logs[blk_id].append(template_id)

        self._block_sequences = dict(block_logs)

    def _load_labels(self) -> None:
        """
        Load block-level anomaly labels from anomaly_label.csv.

        Expected CSV columns: BlockId, Label
        Label values: "Normal" → 0, "Anomaly" → 1
        """
        label_path = self.data_dir / "anomaly_label.csv"
        df = pd.read_csv(label_path)

        # Handle column naming variations across LogHub dataset versions
        label_col = "Label" if "Label" in df.columns else df.columns[-1]
        id_col = "BlockId" if "BlockId" in df.columns else df.columns[0]

        self._labels = {
            row[id_col]: 0 if str(row[label_col]).strip() == "Normal" else 1
            for _, row in df.iterrows()
        }

    def _build_sequences(self, label_filter: int) -> np.ndarray:
        """
        Convert block template-ID lists into fixed-length sequences.

        Blocks shorter than seq_length are zero-padded on the LEFT.
        Blocks longer than seq_length are chunked; the last chunk is padded.
        """
        sequences = []
        for block_id, template_ids in self._block_sequences.items():
            if self._labels.get(block_id, 0) != label_filter:
                continue

            for start in range(0, max(1, len(template_ids)), self.seq_length):
                chunk = template_ids[start: start + self.seq_length]
                if len(chunk) < self.seq_length:
                    chunk = [0] * (self.seq_length - len(chunk)) + chunk  # left-pad
                sequences.append(chunk)

        return np.array(sequences, dtype=np.int32)

    def _ensure_parsed(self) -> None:
        if not self._parsed:
            raise RuntimeError("Call parse() before accessing sequences.")


# ── Dataset split helpers ─────────────────────────────────────────────────

def create_hdfs_splits(preprocessor: LogHubHDFSPreprocessor, val_ratio: float = 0.2) -> dict:
    """
    Create train/val split from LogHub HDFS normal sequences for LSTM-AE pretraining.

    Only NORMAL sequences are used for unsupervised pretraining.
    Anomalous sequences are withheld for optional benchmarking.

    Returns:
        dict with keys: train, val, input_dim
    """
    normal_seqs = preprocessor.get_normal_sequences()
    train_seqs, val_seqs = train_test_split(normal_seqs, test_size=val_ratio, random_state=42)
    return {
        "train": train_seqs,
        "val": val_seqs,
        "input_dim": preprocessor.num_templates,
    }


def create_otel_splits(baseline_windows: List, val_ratio: float = 0.20) -> dict:
    """
    Create train/val splits from OTel Demo baseline window data.

    Args:
        baseline_windows: List of window dicts from WindowAggregator (normal operation only).
        val_ratio: Fraction of data to hold out for validation (default 0.20).

    Returns:
        dict with keys: train, val (each a list of window dicts).

    Note:
        The test set is the fault injection evaluation suite — not a held-out split.
    """
    np.random.seed(42)
    indices = np.random.permutation(len(baseline_windows))

    n_train = int(len(indices) * (1.0 - val_ratio))

    train_idx = indices[:n_train]
    val_idx   = indices[n_train:]

    return {
        "train": [baseline_windows[i] for i in train_idx],
        "val":   [baseline_windows[i] for i in val_idx],
    }
```

---

## 6. RCAEval Data Adapter

### 6.1 Dataset Summary

| Variant | Modalities | Fault Types | Cases | Notes |
|---|---|---|---|---|
| **RE1** | Metrics only | CPU, MEM, DISK, DELAY, LOSS | 375 | No logs/traces; metrics-only evaluation |
| **RE2** | Metrics + Logs + Traces | CPU, MEM, DISK, DELAY, LOSS, SOCKET | 271 | Multi-modal; primary cross-system track (RE2-OB has 91 cases) |
| **RE3** | Metrics + Logs + Traces | 5 code-level faults | 90 | Fine-grained code-level fault analysis |

**Total:** 736 labeled failure cases across 3 independent microservice systems (Online Boutique, Sock Shop, Train Ticket)

### 6.2 Case Directory Structure

> **Note:** The actual directory structure differs from the original RCAEval documentation. Each variant contains system subdirectories (e.g., `RE1-OB/`, `RE2-SS/`), and cases are organized as `{service}_{fault_type}/{run_number}/`.

```
data/RCAEval/re1/
├── RE1-OB/                         # Online Boutique system
│   ├── adservice_cpu/
│   │   ├── 1/
│   │   │   ├── data.csv            # RE1-OB uses simple naming: 51 cols, {service}_{metric}
│   │   │   └── inject_time.txt     # Unix timestamp of fault injection
│   │   ├── 2/ ...
│   │   └── 5/
│   ├── adservice_delay/ ...
│   └── ...
├── RE1-SS/                         # Sock Shop — data.csv but container-metric naming (439 cols)
└── RE1-TT/                         # Train Ticket — data.csv but container-metric naming (1246 cols)

data/RCAEval/re2/
├── RE2-OB/
│   ├── checkoutservice_cpu/
│   │   ├── 1/
│   │   │   ├── metrics.csv         # RE2/RE3 use metrics.csv, container-level naming (421-1574 cols)
│   │   │   ├── logs.csv
│   │   │   ├── traces.csv
│   │   │   ├── inject_time.txt
│   │   │   ├── simple_metrics.csv
│   │   │   └── cluster_info.json
│   │   └── ...
│   └── ...
├── RE2-SS/
└── RE2-TT/
```

**Key file format differences (three naming conventions, not two):**
- **RE1-OB only:** `data.csv` with simple `{service}_{metric}` columns (e.g., `adservice_cpu`, `cartservice_mem`) — 51 columns, 5 metric types (cpu, mem, load, latency, error)
- **RE1-SS, RE1-TT:** `data.csv` but with container-metric naming (e.g., `carts_container-cpu-system-seconds-total`) — 439-1246 columns, same format as RE2/RE3
- **All RE2/RE3:** `metrics.csv` with `{service}_{container-metric-name}` columns — 389-1574 columns, 50 metric types
- **Service naming varies by system:** OB: `adservice`, `cartservice`; SS: `carts`, `catalogue`, `orders`; TT: `ts-auth-service`, `ts-order-service`
- **Infrastructure noise in columns:** GKE node names (`gke-gke-cluster-*`), AWS IPs (`ip-192-168-*`), and `istio-init` appear as service prefixes — the adapter must filter these out

### 6.3 Installation & Download

> **Important:** The pip-installed `RCAEval` package is a stub — it only contains `is_ok()`. The `RCAEval.utility` module does not exist in the pip package. Download datasets directly from Zenodo.

```bash
# Download all datasets from Zenodo API
poetry run python scripts/download_datasets.py --all

# Or download individually
poetry run python scripts/download_datasets.py --rcaeval
poetry run python scripts/download_datasets.py --loghub

# Check download status
poetry run python scripts/download_datasets.py --status
```

### 6.4 `RCAEvalDataAdapter` Full Class

```python
# src/preprocessing/rcaeval_adapter.py
from __future__ import annotations
import json
import pandas as pd
from pathlib import Path
from typing import Dict, Iterator, List, Optional


class RCAEvalDataAdapter:
    """
    Convert RCAEval failure cases (RE1/RE2/RE3) into OpsAgent investigation input format.

    Output format per case:
        {
            "case_id":           str,
            "metrics":           dict[str, pd.DataFrame],  # service_name → per-service metrics DataFrame
            "logs":              pd.DataFrame | None,       # None for RE1
            "anomaly_timestamp": str,                       # ISO-8601; from metadata.json
            "ground_truth": {
                "root_cause_service":   str,
                "root_cause_indicator": str,
                "fault_type":           str,
            }
        }
    """

    # RCAEval column prefixes → OpsAgent canonical metric names
    METRIC_RENAME: Dict[str, str] = {
        "cpu":     "cpu_usage",
        "mem":     "memory_usage",
        "latency": "latency_p99",
        "loss":    "error_rate",
    }

    def __init__(self, dataset_path: str):
        """
        Args:
            dataset_path: Path to a single variant directory, e.g. "data/RCAEval/re2"
        """
        self.dataset_path = Path(dataset_path)
        self._case_ids: Optional[List[str]] = None

    # ── Public API ───────────────────────────────────────────────────────

    def list_cases(self) -> List[str]:
        """Return sorted list of all valid case IDs (dirs containing metadata.json)."""
        if self._case_ids is None:
            self._case_ids = sorted(
                d.name for d in self.dataset_path.iterdir()
                if d.is_dir() and (d / "metadata.json").exists()
            )
        return self._case_ids

    def load_case(self, case_id: str) -> dict:
        """
        Load and convert a single failure case to OpsAgent input format.

        Args:
            case_id: Directory name of the case (e.g. "cpu_cartservice_001").

        Returns:
            OpsAgent-compatible investigation input dict. `metrics` is returned as a
            dict[service_name → pd.DataFrame] for direct use by AgentExecutor.investigate().
        """
        case_dir = self.dataset_path / case_id
        metrics_flat = self._load_metrics(case_dir)
        logs = self._load_logs(case_dir)
        ground_truth = self._load_ground_truth(case_dir)
        # Use anomaly_timestamp from metadata.json as primary source;
        # fall back to inferring from the last row of metrics.csv if absent.
        anomaly_timestamp = (
            ground_truth.pop("anomaly_timestamp")
            or self._extract_anomaly_timestamp(metrics_flat)
        )

        return {
            "case_id":           case_id,
            "metrics":           self._split_metrics_by_service(metrics_flat),
            "logs":              logs,
            "anomaly_timestamp": anomaly_timestamp,
            "ground_truth":      ground_truth,
        }

    def iter_cases(self) -> Iterator[dict]:
        """Iterate over all cases, yielding one converted dict at a time."""
        for case_id in self.list_cases():
            try:
                yield self.load_case(case_id)
            except Exception as e:
                print(f"[RCAEvalAdapter] Skipping case {case_id}: {e}")

    # ── Internal helpers ─────────────────────────────────────────────────

    def _load_metrics(self, case_dir: Path) -> pd.DataFrame:
        """Load metrics.csv and normalize column names to OpsAgent conventions."""
        df = pd.read_csv(case_dir / "metrics.csv")
        df.columns = [self._normalize_metric_col(c) for c in df.columns]
        return df

    def _load_logs(self, case_dir: Path) -> Optional[pd.DataFrame]:
        """Load logs.csv if present (RE2, RE3 only). Returns None for RE1."""
        log_path = case_dir / "logs.csv"
        if not log_path.exists():
            return None
        return pd.read_csv(log_path)

    def _load_ground_truth(self, case_dir: Path) -> dict:
        """
        Parse metadata.json to extract ground truth labels and anomaly timestamp.

        Returns a dict that includes `anomaly_timestamp` so load_case() can use
        the authoritative value from metadata.json before falling back to inference.
        load_case() pops `anomaly_timestamp` before including this dict in the output.
        """
        with open(case_dir / "metadata.json") as f:
            meta = json.load(f)
        return {
            "root_cause_service":   meta.get("root_cause_service", "unknown"),
            "root_cause_indicator": meta.get("root_cause_indicator", "unknown"),
            "fault_type":           meta.get("fault_type", "unknown"),
            "anomaly_timestamp":    meta.get("anomaly_timestamp", None),   # popped by load_case()
        }

    def _extract_anomaly_timestamp(self, metrics_df: pd.DataFrame) -> str:
        """Infer anomaly timestamp from the last timestamp row in metrics.csv."""
        if "timestamp" in metrics_df.columns:
            return str(metrics_df["timestamp"].max())
        return metrics_df.index[-1].isoformat() if hasattr(metrics_df.index, "isoformat") else ""

    def _split_metrics_by_service(self, metrics_df: pd.DataFrame) -> Dict[str, pd.DataFrame]:
        """
        Convert flat metrics DataFrame into per-service DataFrames.

        RCAEval stores all service metrics in a wide table with combined column names
        such as `cartservice_cpu_usage`, `frontend_memory_usage`, etc. (after column
        normalization by _normalize_metric_col). AgentExecutor.investigate() expects
        metrics as dict[service_name → DataFrame] where each DataFrame's columns hold
        only that service's metric values.

        Args:
            metrics_df: Normalized flat DataFrame from _load_metrics().

        Returns:
            Dict mapping service_name → DataFrame of that service's metrics,
            with the service prefix stripped from column names.
        """
        time_col = "timestamp" if "timestamp" in metrics_df.columns else None
        non_time_cols = [c for c in metrics_df.columns if c != time_col]

        # Identify unique service prefixes by matching known canonical metric suffixes
        service_names: set = set()
        for col in non_time_cols:
            for suffix in self.METRIC_RENAME.values():
                if col.endswith(f"_{suffix}"):
                    service_names.add(col[: -(len(suffix) + 1)])
                    break

        service_dfs: Dict[str, pd.DataFrame] = {}
        for svc in service_names:
            svc_cols = [c for c in non_time_cols if c.startswith(f"{svc}_")]
            if not svc_cols:
                continue
            df = metrics_df[svc_cols].copy()
            df.columns = [c[len(svc) + 1:] for c in svc_cols]   # strip service prefix
            if time_col:
                df.insert(0, "timestamp", metrics_df[time_col].values)
            service_dfs[svc] = df

        return service_dfs

    def _normalize_metric_col(self, col: str) -> str:
        """Map RCAEval metric column prefix to OpsAgent canonical name."""
        lower = col.lower()
        for prefix, canonical in self.METRIC_RENAME.items():
            if prefix in lower:
                parts = lower.split("_")
                service_part = "_".join(p for p in parts if p != prefix)
                return f"{service_part}_{canonical}" if service_part else canonical
        return col
```

**Smoke test (run before committing):**
```python
from src.preprocessing.rcaeval_adapter import RCAEvalDataAdapter

for variant in ["re1", "re2", "re3"]:
    adapter = RCAEvalDataAdapter(f"data/RCAEval/{variant}/")
    cases = adapter.list_cases()
    first = adapter.load_case(cases[0])
    print(f"{variant.upper()}: {len(cases)} cases")
    print(f"  Services with metrics: {list(first['metrics'].keys())}")
    print(f"  Logs: {'present' if first['logs'] is not None else 'None (expected for RE1)'}")
    print(f"  Ground truth: {first['ground_truth']}")
```

---

## 7. RCAEval Evaluation Runner

### 7.1 Published Baseline Comparison

| Baseline | Method | Modalities | Notes |
|---|---|---|---|
| **BARO** | Correlation-based ranking | Metrics | Univariate anomaly scoring per service |
| **CIRCA** | Causal inference (PC-based) | Metrics | Most comparable to OpsAgent's causal discovery |
| **RCD** | Randomized conditional independence | Metrics | Similar to PC but randomized |
| **CausalRCA** | Causal graph + propagation | Metrics | Multi-hop causal attribution |
| **MicroHECL** | Graph neural network | Metrics | Service dependency GNN |
| **E-Diagnosis** | Ensemble | Metrics | Combines multiple scoring signals |
| **Nezha** | Trace-based | Metrics + Traces | RE2/RE3 only; requires trace availability |

### 7.2 `evaluate_on_rcaeval()` and `run_all_rcaeval_variants()`

```python
# tests/evaluation/rcaeval_evaluation.py
from __future__ import annotations
import json
from pathlib import Path

from src.preprocessing.rcaeval_adapter import RCAEvalDataAdapter
from tests.evaluation.metrics_calculator import calculate_metrics


def evaluate_on_rcaeval(
    agent,
    dataset_path: str,
    results_output_dir: str = "data/evaluation/rcaeval_results/",
) -> dict:
    """
    Evaluate OpsAgent on all cases in one RCAEval variant (RE1, RE2, or RE3).

    Args:
        agent:               AgentExecutor instance with an .investigate() method.
        dataset_path:        Path to one dataset variant (e.g. "data/RCAEval/re2").
        results_output_dir:  Directory for per-case JSON output files.

    Returns:
        Summary dict: {dataset, total_cases, recall_at_1, recall_at_3, recall_by_fault}
    """
    adapter = RCAEvalDataAdapter(dataset_path)
    output_dir = Path(results_output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for case in adapter.iter_cases():
        # Offline mode: construct a minimal alert from case metadata, pass pre-loaded metrics.
        alert = {
            "affected_services": list(case["metrics"].keys()),
            "anomaly_score":     1.0,   # pre-labeled failure case
            "timestamp":         case["anomaly_timestamp"],
        }
        prediction = agent.investigate(
            alert=alert,                                # required first parameter
            metrics=case["metrics"],                    # offline: dict[service → DataFrame]
            logs=case["logs"],
            anomaly_timestamp=case["anomaly_timestamp"],
        )

        record = {
            "case_id":              case["case_id"],
            "dataset":              Path(dataset_path).name,
            "fault_type":           case["ground_truth"]["fault_type"],
            "ground_truth":         case["ground_truth"]["root_cause_service"],
            "predicted_root_cause": prediction.get("root_cause"),
            "top_3_predictions":    prediction.get("top_3_predictions", []),
            "confidence":           prediction.get("confidence", 0.0),
            "is_correct": (
                prediction.get("root_cause")
                == case["ground_truth"]["root_cause_service"]
            ),
        }
        results.append(record)

        with open(output_dir / f"{case['case_id']}.json", "w") as f:
            json.dump(record, f, indent=2)

    metrics = calculate_metrics(results)
    summary = {
        "dataset":         Path(dataset_path).name,
        "total_cases":     len(results),
        "recall_at_1":     metrics.recall_at_1,
        "recall_at_3":     metrics.recall_at_3,
        "recall_by_fault": metrics.recall_by_fault,
    }

    with open(output_dir / "summary.json", "w") as f:
        json.dump(summary, f, indent=2)

    print(f"\nRCAEval [{Path(dataset_path).name}] Results:")
    print(f"  Recall@1:    {summary['recall_at_1']:.1%}")
    print(f"  Recall@3:    {summary['recall_at_3']:.1%}")
    print(f"  Total cases: {summary['total_cases']}")
    return summary


def run_all_rcaeval_variants(agent, base_path: str = "data/RCAEval/") -> dict:
    """Run evaluation sequentially on RE1, RE2, and RE3."""
    all_results = {}
    for variant in ["re1", "re2", "re3"]:
        print(f"\n{'='*60}\nEvaluating on RCAEval {variant.upper()}\n{'='*60}")
        all_results[variant] = evaluate_on_rcaeval(
            agent=agent,
            dataset_path=f"{base_path}/{variant}",
            results_output_dir=f"data/evaluation/rcaeval_results/{variant}/",
        )
    return all_results
```

**Running CIRCA/BARO/RCD baselines for comparison:**
```python
from RCAEval.baselines import CIRCA, BARO, RCD
from src.preprocessing.rcaeval_adapter import RCAEvalDataAdapter
from tests.evaluation.metrics_calculator import recall_at_1

for variant in ["re1", "re2"]:
    adapter = RCAEvalDataAdapter(f"data/RCAEval/{variant}/")
    for name, cls in [("CIRCA", CIRCA), ("BARO", BARO), ("RCD", RCD)]:
        baseline = cls()
        preds, truths = [], []
        for case in adapter.iter_cases():
            pred = baseline.predict(metrics=case["metrics"], ground_truth=case["ground_truth"])
            preds.append(pred.get("root_cause"))
            truths.append(case["ground_truth"]["root_cause_service"])
        print(f"{name} {variant.upper()} Recall@1: {recall_at_1(preds, truths):.1%}")
```

**Per-case result JSON template:**
```json
{
  "case_id": "cpu_cartservice_001",
  "dataset": "re2",
  "fault_type": "cpu",
  "ground_truth": "cartservice",
  "predicted_root_cause": "cartservice",
  "top_3_predictions": ["cartservice", "frontend", "checkoutservice"],
  "confidence": 0.83,
  "is_correct": true,
  "notes": ""
}
```

---

## 8. Service Topology Graph

```python
# src/data_collection/topology_extractor.py
import json
import networkx as nx
from typing import Dict, List, Optional


class TopologyGraph:
    """
    Directed service dependency graph for the OTel Demo microservice system.

    Used by the agent's get_topology tool to determine upstream/downstream
    relationships when forming root cause hypotheses.
    An edge A → B means "A is called by B" (A is a dependency of B).
    Therefore: upstream services of B = {A} (potential root causes when B is affected).
    """

    # OTel Astronomy Shop service dependencies (7 nodes: 6 core services + redis)
    # Excluded from reduced stack: emailservice, shippingservice, recommendationservice, adservice
    # Format: (dependency, dependent) — dependency is upstream of dependent
    KNOWN_EDGES = [
        ("redis",                 "cartservice"),
        ("cartservice",           "checkoutservice"),
        ("productcatalogservice", "checkoutservice"),
        ("currencyservice",       "checkoutservice"),
        ("paymentservice",        "checkoutservice"),
        ("cartservice",           "frontend"),
        ("productcatalogservice", "frontend"),
        ("checkoutservice",       "frontend"),
        ("currencyservice",       "frontend"),
    ]

    def __init__(self):
        self.graph = nx.DiGraph()
        self._init_topology()

    def _init_topology(self):
        """Populate the graph with known OTel Demo service dependencies."""
        for dep, svc in self.KNOWN_EDGES:
            self.graph.add_edge(dep, svc, protocol="gRPC", avg_latency_ms=0.0)

    def get_subgraph(self, service_name: str) -> dict:
        """Return the subgraph centered on a given service."""
        return {
            "nodes": [
                {"name": n, **self.graph.nodes[n]}
                for n in self.graph.nodes()
                if n == service_name
                or service_name in self.graph.predecessors(n)
                or service_name in self.graph.successors(n)
            ],
            "edges": [
                {"source": u, "target": v, **self.graph.edges[u, v]}
                for u, v in self.graph.edges()
                if service_name in (u, v)
            ],
            "upstream":   list(self.graph.predecessors(service_name)),
            "downstream": list(self.graph.successors(service_name)),
        }

    def to_json(self) -> str:
        """Serialize the full topology to JSON (used by get_topology agent tool)."""
        return json.dumps({
            "nodes": [{"name": n, **self.graph.nodes[n]} for n in self.graph.nodes()],
            "edges": [
                {"source": u, "target": v, **self.graph.edges[u, v]}
                for u, v in self.graph.edges()
            ],
        }, indent=2)
```

---

## 9. Runbook Indexer

### 9.1 `RunbookIndexer` Class

```python
# src/knowledge_base/runbook_indexer.py
import hashlib
from pathlib import Path
from typing import Dict, List

import chromadb
from chromadb.utils import embedding_functions


class RunbookIndexer:
    """
    Index runbook markdown files into ChromaDB for vector similarity search.

    Used by the agent's search_runbooks tool to retrieve relevant
    troubleshooting documentation given a natural language issue description.

    Embedding model: all-MiniLM-L6-v2 (fast, 384-dim, strong for short texts)
    ChromaDB persistence: data/chromadb/ (survives restarts)
    """

    def __init__(self, persist_directory: str = "data/chromadb"):
        self.client = chromadb.PersistentClient(path=persist_directory)
        self.embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name="all-MiniLM-L6-v2"
        )
        self.collection = self.client.get_or_create_collection(
            name="runbooks",
            embedding_function=self.embedding_fn,
            metadata={"description": "OpsAgent runbook knowledge base"},
        )

    def index_file(self, file_path: str, chunk_size: int = 500) -> int:
        """
        Index a markdown runbook file, split into paragraph-level chunks.

        Args:
            file_path:  Path to the .md runbook file.
            chunk_size: Target character size per chunk (soft limit).

        Returns:
            Number of chunks indexed.
        """
        path = Path(file_path)
        content = path.read_text(encoding="utf-8")
        chunks = self._chunk_content(content, chunk_size)

        for i, chunk in enumerate(chunks):
            doc_id = hashlib.md5(f"{path.name}_{i}".encode()).hexdigest()
            self.collection.upsert(
                ids=[doc_id],
                documents=[chunk],
                metadatas=[{
                    "source": path.name,
                    "chunk_index": i,
                    "total_chunks": len(chunks),
                }],
            )
        return len(chunks)

    def index_directory(self, directory: str) -> int:
        """Index all .md files in a directory recursively."""
        total = 0
        for md_file in Path(directory).glob("**/*.md"):
            n = self.index_file(str(md_file))
            print(f"Indexed {md_file.name}: {n} chunks")
            total += n
        return total

    def search(self, query: str, top_k: int = 3) -> List[Dict]:
        """
        Retrieve the most relevant runbook chunks for a query.

        Returns:
            List of dicts: {content, source, relevance_score}
            Sorted by relevance_score descending.
        """
        results = self.collection.query(query_texts=[query], n_results=top_k)
        return [
            {
                "content":         doc,
                "source":          meta["source"],
                "relevance_score": round(1.0 - dist, 4),   # distance → similarity
            }
            for doc, meta, dist in zip(
                results["documents"][0],
                results["metadatas"][0],
                results["distances"][0],
            )
        ]

    def _chunk_content(self, content: str, chunk_size: int) -> List[str]:
        """Split markdown content at paragraph boundaries, respecting chunk_size."""
        paragraphs = content.split("\n\n")
        chunks, current = [], ""
        for para in paragraphs:
            if len(current) + len(para) < chunk_size:
                current += para + "\n\n"
            else:
                if current:
                    chunks.append(current.strip())
                current = para + "\n\n"
        if current:
            chunks.append(current.strip())
        return chunks
```

**One-time setup (run before first agent invocation):**
```python
from src.knowledge_base.runbook_indexer import RunbookIndexer

indexer = RunbookIndexer(persist_directory="data/chromadb")
total = indexer.index_directory("runbooks/")
print(f"Total chunks indexed: {total}")
```

### 9.2 Custom Runbook Templates

Create the following files in `runbooks/`. Each will be chunked and embedded by `RunbookIndexer`.

**`runbooks/connection_exhaustion.md`**
```markdown
# Connection Pool Exhaustion

## Symptoms
- Service logs: "connection timeout", "pool exhausted", "too many connections"
- connection_count metric at or near maximum configured value
- Downstream service latency spikes within 1–2 minutes

## Root Cause
Connection pool fully utilized; new requests cannot acquire connections.
Common triggers: slow queries, connection leaks, traffic spikes.

## Investigation Steps
1. Check `connection_count` metric for the affected service
2. Search logs for "timeout" and "exhausted" patterns
3. Look for long-running queries or slow upstream calls holding connections
4. Verify connection pool size in service configuration

## Remediation

### Immediate
- Restart the affected service to flush hung connections:
  `docker restart <service-name>`
- Manually clear idle connections: `redis-cli CLIENT KILL TYPE normal`

### Long-term
- Increase connection pool max size in service config
- Add circuit breaker on all callers of the affected service
- Alert at 80% connection utilization (before exhaustion)
```

**`runbooks/cascading_failure.md`**
```markdown
# Cascading Failure

## Symptoms
- Multiple services degrading in sequence (timestamps staggered 30–120s apart)
- Error rates climbing downstream from the original failure point
- Causal graph shows a single root service with high fan-out

## Root Cause
An upstream service failure propagates to all dependents via synchronous call chains.

## Investigation Steps
1. Use get_topology to identify the full upstream dependency chain
2. Check the EARLIEST degrading service — that is the root cause candidate
3. Run discover_causation on the chain to confirm causal direction
4. Look for retries amplifying load on an already-degraded service

## Remediation

### Immediate
- Restart the root cause service first, then dependent services in dependency order

### Long-term
- Implement exponential backoff + jitter in all service-to-service callers
- Add circuit breakers to prevent retry amplification
- Introduce bulkhead pattern to isolate failure domains
```

**`runbooks/memory_pressure.md`**
```markdown
# Memory Pressure / OOM

## Symptoms
- memory_usage metric climbing steadily over 30+ minutes
- Service restarts or OOMKilled events in logs
- Gradual latency increase preceding crash

## Root Cause
Memory leak, large object accumulation, or insufficient memory allocation.

## Investigation Steps
1. Query memory_usage over a 60-minute window to check growth trend
2. Search logs for "OOMKilled", "out of memory", "GC overhead"
3. Check for object caching without TTL or bounded size

## Remediation

### Immediate
- Restart the affected service to reclaim memory
- Reduce traffic temporarily using load balancer weights

### Long-term
- Profile memory usage to identify the leak source
- Add memory usage alert at 85% threshold
- Enforce JVM/container memory limits with headroom
```

**`runbooks/high_latency.md`**
```markdown
# High Latency (Non-Cascading)

## Symptoms
- latency_p99 elevated on one service while upstream dependencies appear healthy
- error_rate may be low (slow, not failing)
- CPU or memory usage elevated on the affected service

## Root Cause
Slow internal processing: CPU saturation, inefficient queries, GC pauses, I/O wait.

## Investigation Steps
1. Check CPU and memory for the affected service specifically
2. Search logs for "slow", "timeout", "GC pause", "blocked"
3. Compare latency_p50 vs latency_p99 — large gap suggests tail latency from GC or lock contention

## Remediation

### Immediate
- Horizontal scale (add more replicas) if CPU-bound
- Restart to clear GC pressure if memory-bound

### Long-term
- Profile with async-profiler to identify CPU hot paths
- Tune JVM GC settings
- Add database query timeout enforcement
```

---

## 10. Dataset Download Script

> **Important:** The pip-installed `RCAEval` package is a stub — `RCAEval.utility` does not exist. The implemented `scripts/download_datasets.py` downloads directly from the Zenodo API.

```bash
# Download all datasets from Zenodo
poetry run python scripts/download_datasets.py --all

# Download individually
poetry run python scripts/download_datasets.py --rcaeval   # RE1/RE2/RE3 from Zenodo record 14590730
poetry run python scripts/download_datasets.py --loghub    # HDFS_v1.zip from Zenodo record 8196385

# Check download status
poetry run python scripts/download_datasets.py --status
```

The script downloads 9 ZIP files for RCAEval (3 systems × 3 variants) and `HDFS_v1.zip` for LogHub, extracts them to the correct directories, and verifies case counts.

**Expected disk usage:**

| Dataset | Path | Size |
|---|---|---|
| RCAEval RE1 | `data/RCAEval/re1/` | ~1.5 GB |
| RCAEval RE2 | `data/RCAEval/re2/` | ~2.5 GB |
| RCAEval RE3 | `data/RCAEval/re3/` | ~1 GB |
| LogHub HDFS | `data/LogHub/HDFS/` | ~1 GB |
| OTel Demo baseline | `data/baseline/` | ~500 MB (self-generated) |
| **Total** | | **~6.5 GB** |
