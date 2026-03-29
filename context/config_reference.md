# Config Reference

**Source:** `01_OpsAgent_Technical_Specifications.md` Appendix

**Config files live in:** `configs/`

| File | Purpose | Load when... |
|---|---|---|
| `configs/model_config.yaml` | LSTM-AE hyperparameters, training phases | Working on anomaly detection or training pipeline |
| `configs/agent_config.yaml` | LLM settings, tool parameters, causal discovery | Working on the LangGraph agent or any of its 5 tools |
| `configs/dataset_config.yaml` | Dataset paths, sequence lengths, variant definitions | Working on data loading, preprocessing, or adapters |
| `configs/evaluation_scenarios.yaml` | Fault injection definitions, evaluation flags | Working on fault injection scripts or the evaluation runner |

> **Usage pattern:** Load configs via `OmegaConf` or `PyYAML` at the top of each module. Never hardcode paths or hyperparameters in `src/`. All tuneable values must live here.

---

## Loading Configs in Python

```python
import yaml
from pathlib import Path

def load_config(config_name: str) -> dict:
    """Load a YAML config file from the configs/ directory."""
    config_path = Path("configs") / config_name
    with open(config_path) as f:
        return yaml.safe_load(f)

# Example usage
model_cfg    = load_config("model_config.yaml")
agent_cfg    = load_config("agent_config.yaml")
dataset_cfg  = load_config("dataset_config.yaml")
eval_cfg     = load_config("evaluation_scenarios.yaml")

# Access values
hidden_dim       = model_cfg["anomaly_detection"]["lstm_autoencoder"]["hidden_dim"]
confidence_floor = agent_cfg["agent"]["investigation"]["confidence_threshold"]
re2_path         = dataset_cfg["rcaeval"]["variants"]["re2"]["path"]
```

> **Tip:** For notebook experiments, load configs at the top of the first cell so all parameters are visible and reproducible.

---

## Cross-Config Constraints

These values must stay consistent across config files — changing one without the other causes silent bugs:

| Constraint | Files Involved | Rule |
|---|---|---|
| Sequence length | `model_config.yaml` ↔ `dataset_config.yaml` | `anomaly_detection.lstm_autoencoder.sequence_length` **must equal** `loghub_hdfs.sequence_length` (both = `10`) |
| HDFS pretraining checkpoint | `model_config.yaml` (two places) | `training.pretrain.checkpoint` **must equal** `training.finetune.pretrained_checkpoint` |
| RCAEval variants | `dataset_config.yaml` ↔ `evaluation_scenarios.yaml` | Variant names in `evaluation_scenarios.yaml: rcaeval_evaluation.variants` must be defined keys in `dataset_config.yaml: rcaeval.variants` |
| PC algorithm alpha | `agent_config.yaml` (two places) | `tools.discover_causation.alpha` should equal `causal_discovery.alpha` unless intentionally different |

---

## 1. `configs/model_config.yaml`

LSTM-Autoencoder architecture and two-phase training configuration. Also defines the Isolation Forest baseline and anomaly threshold strategy.

```yaml
# configs/model_config.yaml
# ML model hyperparameters for OpsAgent anomaly detection.
# Full architecture details: context/anomaly_detection_specs.md

anomaly_detection:
  lstm_autoencoder:
    # input_dim is determined dynamically at training time from Drain3 template count
    # (typically 50–200 unique templates; do not hardcode)
    embedding_dim: 32       # Template embedding dimension
    hidden_dim: 64          # LSTM hidden state size
    latent_dim: 16          # Compressed bottleneck representation
    num_layers: 2           # Encoder and decoder depth (each)
    dropout: 0.2            # Applied between LSTM layers
    sequence_length: 10     # ← MUST match dataset_config.yaml: loghub_hdfs.sequence_length
                            #   10 windows × 60s = 10 minutes of history per sequence

  isolation_forest:
    n_estimators: 100       # Number of trees
    contamination: 0.01     # Expected anomaly fraction during normal operation
    max_samples: 256        # Subsample size per tree

  threshold:
    method: "percentile"    # Options: "percentile", "fixed", "std_dev"
    percentile: 95          # Flag sequences with reconstruction error > 95th percentile
                            # Computed on normal (baseline) training data only

training:
  # ─── Phase 1: Pretraining on LogHub HDFS ────────────────────────────────────
  # Goal: Learn general log anomaly patterns from a large-scale distributed system.
  # Run on Google Colab Pro (T4/L4/A100). See: context/anomaly_detection_specs.md
  pretrain:
    dataset: "loghub_hdfs"
    data_path: "data/LogHub/HDFS/"
    batch_size: 64
    epochs: 50
    early_stopping_patience: 5
    learning_rate: 0.001
    optimizer: "adam"
    loss: "mse"             # Mean Squared Error on reconstruction
    checkpoint: "models/lstm_autoencoder/pretrained_hdfs.pt"  # ← Must match finetune.pretrained_checkpoint

  # ─── Phase 2: Fine-tuning on OpenTelemetry Demo ─────────────────────────────
  # Goal: Specialize pretrained weights to the target microservice environment.
  # Uses lower LR (10× smaller) to preserve general patterns learned in Phase 1.
  finetune:
    dataset: "otel_demo"
    pretrained_checkpoint: "models/lstm_autoencoder/pretrained_hdfs.pt"  # ← Must match pretrain.checkpoint
    batch_size: 32
    epochs: 30
    early_stopping_patience: 10
    learning_rate: 0.0001   # 10× smaller than pretraining to avoid catastrophic forgetting
    optimizer: "adam"
    loss: "mse"
    validation_split: 0.2
    checkpoint: "models/lstm_autoencoder/finetuned_otel.pt"
```

---

## 2. `configs/agent_config.yaml`

LLM backend, investigation loop controls, per-tool parameters, and causal discovery settings for the LangGraph agent.

```yaml
# configs/agent_config.yaml
# Agent configuration for the OpsAgent LangGraph investigation workflow.
# Full tool specs and AgentState definition: context/agent_specs.md

agent:
  llm:
    model: "gemini-1.5-flash"   # Primary LLM for reasoning and report generation
    temperature: 0.1            # Near-deterministic for reproducible RCA reports
    max_output_tokens: 4096     # Sufficient for a complete RCA report
    # API key: loaded from GEMINI_API_KEY environment variable via python-dotenv
    # Never hardcode API keys here

  investigation:
    max_tool_calls: 10          # Hard cap on agent tool use per investigation
                                # Prevents runaway API costs and infinite loops
    confidence_threshold: 0.7   # Stop iterating once counterfactual confidence ≥ 0.7
                                # Prevents over-investigation when root cause is clear
    timeout_seconds: 300        # Abort investigation if it exceeds 5 minutes

  tools:
    # Tool: query_metrics (queries Prometheus)
    query_metrics:
      prometheus_url: "http://localhost:9090"
      default_time_range_minutes: 30     # Look back 30 min from anomaly timestamp
      max_data_points: 500               # Limit response size

    # Tool: search_logs (queries Loki)
    search_logs:
      loki_url: "http://localhost:3100"
      default_limit: 100                 # Max log lines per query
      default_time_range_minutes: 30

    # Tool: get_topology (reads NetworkX graph)
    get_topology:
      topology_file: "data/baseline/topology.json"  # Pre-built service dependency graph

    # Tool: search_runbooks (queries ChromaDB)
    search_runbooks:
      chroma_persist_dir: "data/chromadb/"
      collection_name: "runbooks"
      top_k: 3                           # Return top-3 most relevant runbook chunks
      embedding_model: "all-MiniLM-L6-v2"  # sentence-transformers model

    # Tool: discover_causation (runs PC algorithm via causal-learn)
    discover_causation:
      alpha: 0.05             # Statistical significance threshold for edge inclusion
      lags: [1, 2, 5]         # Time lags (in windows) to include as features
                              # Lag 1 = 1 min ago, lag 2 = 2 min ago, lag 5 = 5 min ago

causal_discovery:
  algorithm: "pc"             # PC (Peter-Clark) algorithm from causal-learn library
  alpha: 0.05                 # Significance threshold (should match tools.discover_causation.alpha)
  max_conditioning_set: 3     # Cap on conditioning set size; limits computational cost
                              # Larger values = more accurate but exponentially slower
```

---

## 3. `configs/dataset_config.yaml`

Paths, file names, sequence parameters, and metadata for all three datasets. The single source of truth for dataset-related paths — never hardcode these in `src/`.

```yaml
# configs/dataset_config.yaml
# Dataset paths and parameters for all three data sources used by OpsAgent.
# Dataset download: scripts/download_datasets.py
# Adapter code:    context/data_pipeline_specs.md

# ─── OpenTelemetry Demo (self-generated) ────────────────────────────────────
# Primary training data. Generated by running the OTel Demo app with fault injection.
# Baseline collection: scripts/generate_training_data.py (24h run)
otel_demo:
  baseline_data_path: "data/baseline/"
  baseline_duration_hours: 24
  fault_injection_path: "data/evaluation/results/"
  services:             # The 6 included services (adservice, etc. excluded — see architecture_and_design.md)
    - "frontend"
    - "cartservice"
    - "checkoutservice"
    - "paymentservice"
    - "productcatalogservice"
    - "currencyservice"

# ─── LogHub HDFS ────────────────────────────────────────────────────────────
# Used for: LSTM-AE pretraining (normal sequences only) + DeepLog/LogRobust benchmark.
# Source:   Zenodo DOI 10.5281/zenodo.8196385
# Install:  Download manually to data/LogHub/HDFS/ (see scripts/download_datasets.py)
loghub_hdfs:
  data_path: "data/LogHub/HDFS/"
  log_file: "HDFS.log"            # ~11.2M raw log lines
  label_file: "anomaly_label.csv" # Block-level anomaly labels (0 = normal, 1 = anomalous)
  sequence_length: 10             # ← MUST match model_config.yaml: lstm_autoencoder.sequence_length
                                  #   Each block grouped into sequences of 10 log events
  anomaly_rate_expected: 0.029    # ~2.9% of blocks are anomalous; sanity-check after loading
  normal_sequences_for_pretrain: true   # Only normal sequences used in pretraining
  # Reference: IEEE ISSRE 2023 — "Large Language Models for Log-based Anomaly Detection"

# ─── RCAEval Benchmark ──────────────────────────────────────────────────────
# Used for: Cross-system RCA validation against 5 published baselines.
# Source:   Zenodo DOI 10.5281/zenodo.14590730 (ACM WWW 2025 / IEEE/ACM ASE 2024)
# Install:  poetry add --group dev "RCAEval[default]", then: poetry run python scripts/download_datasets.py --rcaeval
rcaeval:
  base_path: "data/RCAEval/"
  results_output_path: "data/evaluation/rcaeval_results/"

  variants:
    re1:
      path: "data/RCAEval/re1/"
      modalities: ["metrics"]               # Metrics-only (no logs or traces)
      fault_types: ["cpu", "mem", "disk", "delay", "loss"]
      total_cases: 375
      download_size_gb: 1.5

    re2:
      path: "data/RCAEval/re2/"
      modalities: ["metrics", "logs", "traces"]  # Full multi-modal telemetry
      fault_types: ["cpu", "mem", "disk", "delay", "loss", "socket"]
      total_cases: 271
      download_size_gb: 2.0
      # RE2 is the primary cross-system benchmark target (see evaluation_strategy.md)

    re3:
      path: "data/RCAEval/re3/"
      modalities: ["metrics", "logs", "traces"]
      fault_types: ["code_level"]           # 5 code-level fault types
      total_cases: 90
      download_size_gb: 0.5

  # Metric column name normalization: RCAEval uses different conventions than OpsAgent.
  # The RCAEvalDataAdapter applies these renames automatically — do not rename CSV files.
  column_rename_map:
    cpu:     "cpu_usage"
    mem:     "memory_usage"
    latency: "latency_p99"
    loss:    "error_rate"
```

---

## 4. `configs/evaluation_scenarios.yaml`

Defines all 8 OTel Demo fault injection scenarios and controls the evaluation runner's behavior. Also flags RCAEval and HDFS benchmark execution.

```yaml
# configs/evaluation_scenarios.yaml
# Fault injection scenario definitions and evaluation runner settings.
# Fault scripts: demo_app/fault_scenarios/  (01_service_crash.sh … 08_config_error.sh)
# Runner:        scripts/inject_faults.py
# Full evaluation procedures: context/evaluation_strategy.md

# ─── OTel Demo Fault Scenarios ──────────────────────────────────────────────
# 8 fault types × 5 repetitions = 40 total test cases
# Run order: easy → medium → hard (Days 1-2: easy; Day 3: medium/hard)
fault_types:
  - name: "service_crash"
    script: "demo_app/fault_scenarios/01_service_crash.sh"
    target_service: "cartservice"
    ground_truth: "cartservice"
    difficulty: "easy"
    expected_detection_seconds: 30     # Target: < 30s detection latency
    cooldown_seconds: 120              # Cooldown before next test

  - name: "high_latency"
    script: "demo_app/fault_scenarios/02_high_latency.sh"
    target_service: "paymentservice"
    ground_truth: "paymentservice"
    difficulty: "easy"
    expected_detection_seconds: 60
    cooldown_seconds: 120
    injection_params:
      latency_ms: 500

  - name: "memory_pressure"
    script: "demo_app/fault_scenarios/03_memory_pressure.sh"
    target_service: "checkoutservice"
    ground_truth: "checkoutservice"
    difficulty: "medium"
    expected_detection_seconds: 120
    cooldown_seconds: 180
    injection_params:
      memory_limit_mb: 128

  - name: "cpu_throttling"
    script: "demo_app/fault_scenarios/04_cpu_throttling.sh"
    target_service: "productcatalogservice"
    ground_truth: "productcatalogservice"
    difficulty: "medium"
    expected_detection_seconds: 120
    cooldown_seconds: 180
    injection_params:
      cpus: 0.1

  - name: "connection_exhaustion"
    script: "demo_app/fault_scenarios/05_connection_exhaustion.sh"
    target_service: "redis"           # Fault exhausts Redis connections; cartservice is downstream victim
    ground_truth: "redis"
    difficulty: "medium"
    expected_detection_seconds: 90
    cooldown_seconds: 150
    injection_params:
      max_connections: 5

  - name: "network_partition"
    script: "demo_app/fault_scenarios/06_network_partition.sh"
    target_service: "paymentservice"
    ground_truth: "paymentservice"
    difficulty: "medium"
    expected_detection_seconds: 45
    cooldown_seconds: 120

  - name: "cascading_failure"
    script: "demo_app/fault_scenarios/07_cascading_failure.sh"
    target_service: "cartservice"          # cartservice is killed to trigger the cascade
    ground_truth: "cartservice"            # cartservice is the root cause; checkoutservice/frontend are victims
    difficulty: "hard"
    expected_detection_seconds: 60
    cooldown_seconds: 240                  # Longer cooldown: downstream services must fully recover

  - name: "config_error"
    script: "demo_app/fault_scenarios/08_config_error.sh"
    target_service: "currencyservice"
    ground_truth: "currencyservice"
    difficulty: "hard"
    expected_detection_seconds: 30
    cooldown_seconds: 120
    injection_params:
      env_var: "DATABASE_URL"
      invalid_value: "invalid://bad-url"

# ─── Evaluation Runner Settings ─────────────────────────────────────────────
evaluation:
  repetitions_per_fault: 5            # 8 faults × 5 runs = 40 total OTel Demo test cases
  baseline_duration_hours: 24         # Baseline collection period (normal operation)
  results_output_path: "data/evaluation/results/"
  explanation_quality_scores_path: "data/evaluation/explanation_quality_scores.csv"

  # Metrics to compute for OTel Demo evaluation
  metrics:
    - "recall_at_1"           # Is top prediction the true root cause?
    - "recall_at_3"           # Is true root cause within top-3 predictions?
    - "precision"             # False positive rate during 24h normal operation
    - "detection_latency"     # Seconds from fault injection to first alert
    - "mttr_proxy"            # Investigation time vs. rule-based and AD-only baselines
    - "explanation_quality"   # Human rubric score (1–5 scale; target ≥ 4.0)

  # Baseline comparisons (run via tests/evaluation/baseline_comparison.py)
  baselines:
    - name: "rule_based"       # Threshold alerts on individual metrics only
    - name: "ad_only"          # LSTM-AE detection with no agent reasoning
    - name: "llm_no_tools"     # LLM reasoning without causal discovery or tool use

  # ─── Cross-System Validation (RCAEval) ──────────────────────────────────────
  # Run separately from fault injection; does not require the Docker stack.
  # Script: scripts/run_evaluation.py --rcaeval
  rcaeval_evaluation:
    enabled: true
    variants: ["re1", "re2", "re3"]  # ← Must be defined keys in dataset_config.yaml: rcaeval.variants
    results_output_path: "data/evaluation/rcaeval_results/"
    compare_against_published_baselines: true
    # Published baselines to compare against (from RCAEval paper):
    published_baselines: ["BARO", "CIRCA", "RCD", "CausalRCA", "MicroHECL"]
    # Primary target: RE2 Recall@1 competitive with CIRCA / RCD

  # ─── LogHub HDFS Anomaly Detection Benchmark (Nice-to-Have) ──────────────
  # 3-stage comparison: untrained vs. pretrained vs. fine-tuned LSTM-AE
  # Script: tests/evaluation/loghub_benchmark.py
  # Designated nice-to-have: run only after Tracks 1 and 2 are complete.
  # See: context/anomaly_detection_specs.md §11 for benchmark details.
  loghub_benchmark:
    enabled: false                # Set to true only if running the optional Track 3 benchmark
    stages:
      - name: "untrained"
        checkpoint: null                                          # Random weights
      - name: "pretrained"
        checkpoint: "models/lstm_autoencoder/pretrained_hdfs.pt"
      - name: "finetuned"
        checkpoint: "models/lstm_autoencoder/finetuned_otel.pt"
    # Published comparison targets (from original papers):
    # DeepLog F1 = 0.941  (Du et al., 2017)
    # LogRobust F1 = 0.978 (Zhang et al., 2019)
    target_f1: 0.90     # Minimum acceptable F1 on HDFS held-out test set
```
