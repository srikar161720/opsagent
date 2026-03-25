# Evaluation Strategy

**Source:** `03_OpsAgent_Evaluation_Guide.md` (primary) · `01_OpsAgent_Technical_Specifications.md` Section 4.5 · `02_OpsAgent_Implementation_Guide.md` Phase 5

**Implementation files:**
- `tests/evaluation/metrics_calculator.py` — All metric computations
- `tests/evaluation/rcaeval_evaluation.py` — Cross-system evaluation runner
- `tests/evaluation/fault_injection_suite.py` — OTel Demo automated runner
- `tests/evaluation/loghub_benchmark.py` — HDFS anomaly detection benchmark
- `tests/evaluation/baseline_comparison.py` — Runs all baseline methods
- `notebooks/08_evaluation_analysis.ipynb` — All 11 required visualizations
- `data/evaluation/results/` — OTel Demo per-test JSONs
- `data/evaluation/rcaeval_results/` — RCAEval per-case JSONs
- `data/evaluation/explanation_quality_scores.csv` — Manual quality scores
- `docs/evaluation_results.md` — Final compiled results document

---

## 1. Overview: Three Evaluation Tracks

OpsAgent is evaluated across three independent tracks that collectively answer different research questions.

| Track | Dataset | Cases | Primary Question |
|---|---|---|---|
| **Track 1: Primary (Fault Injection)** | OpenTelemetry Demo | 40 (8 types × 5 runs) | Does OpsAgent correctly identify root causes in its target environment? |
| **Track 2: Cross-System Validation** | RCAEval RE1/RE2/RE3 | 735 total | Does OpsAgent generalize beyond its training environment? |
| **Track 3: AD Benchmark** *(Nice-to-Have)* | LogHub HDFS | ~558K block sequences | How accurately does the LSTM-AE detect anomalies on a labeled benchmark? |

### 1.1 Target Outcomes

| Metric | Target | Track | Rationale |
|---|---|---|---|
| **Recall@1 (OTel Demo)** | ≥ 80% | Primary | Correct root cause in top prediction |
| **Recall@3 (OTel Demo)** | ≥ 95% | Primary | Root cause within top 3 predictions |
| **Precision (OTel Demo)** | ≥ 70% | Primary | Low false alarm rate during normal operation |
| **Detection Latency** | < 60 seconds | Primary | Time from fault injection to first alert |
| **MTTR Reduction** | ≥ 50% | Primary | vs. rule-based and AD-only baselines |
| **Explanation Quality** | ≥ 4.0 / 5.0 | Primary | Human rating of RCA report usefulness |
| **Recall@1 (RCAEval RE2)** | Competitive with CIRCA/RCD | Cross-System | Generalization on multi-modal real-world data |
| **LSTM-AE F1 (HDFS)** *(Nice-to-Have)* | ≥ 0.90 | AD Benchmark | Standard anomaly detection benchmark threshold — run only if ahead of schedule |

### 1.2 Evaluation Timeline

| Week | Activities |
|---|---|
| Week 9 | Implement fault injection scripts; run all 40 OTel Demo tests (8 fault types × 5 runs, Days 1–4); false positive test under 24h normal operation (Day 5); evaluate 3 internal baselines |
| Week 10 Days 1–2 | Run OpsAgent against RCAEval RE1 (375 cases), RE2 (270 cases), RE3 (90 cases) |
| Week 10 Days 3–5 | Calculate all metrics; manually score 25–30 RCA reports; create Visualizations 1–9; run statistical analysis; draft `docs/evaluation_results.md`; *(nice-to-have: run HDFS benchmark + Visualizations 10–11 if time permits)* |

---

## 2. Track 1 — OTel Demo Fault Injection (Primary)

### 2.1 Fault Type Summary

| Fault | Target | Ground Truth | Difficulty | Detection Target |
|---|---|---|---|---|
| service_crash | cartservice | cartservice | Easy | < 30 s |
| high_latency | paymentservice | paymentservice | Easy | < 60 s |
| memory_pressure | checkoutservice | checkoutservice | Medium | < 120 s |
| cpu_throttling | productcatalogservice | productcatalogservice | Medium | < 120 s |
| connection_exhaustion | redis | redis | Medium | < 90 s |
| network_partition | paymentservice | paymentservice | Medium | < 45 s |
| cascading_failure | cartservice | cartservice | **Hard** | < 60 s |
| config_error | currencyservice | currencyservice | **Hard** | < 30 s |

> **Key RCA challenge:** `cascading_failure` is the hardest case — multiple downstream services degrade sequentially, but the root cause is always the single upstream service that failed first. This is where causal discovery (PC algorithm) differentiates OpsAgent from symptom-chasing baselines.

### 2.2 Test Schedule

| Day | Fault Types | Runs Each | Tests |
|---|---|---|---|
| Day 1 | service_crash, high_latency | 5 | 10 |
| Day 2 | memory_pressure, cpu_throttling | 5 | 10 |
| Day 3 | connection_exhaustion, network_partition | 5 | 10 |
| Day 4 | cascading_failure, config_error | 5 | 10 |
| Day 5 | **False positive test** (24h normal operation) | — | — |
| **Total** | 8 types | 5 each | **40 tests** |

**Cooldown between tests:** Per-fault, as defined in `configs/evaluation_scenarios.yaml` (120–240 seconds). Cascading failures use the longest cooldown (240s) to allow full downstream recovery; simple crashes use the shortest (120s). The `_load_per_fault_cooldowns()` helper in the test runner reads these values automatically.

### 2.3 Automated Test Runner

```python
# tests/evaluation/fault_injection_suite.py
import subprocess
import json
import time
import yaml
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
import argparse

if TYPE_CHECKING:
    from src.agent.executor import AgentExecutor

FAULT_SCRIPTS = {
    "service_crash":         "demo_app/fault_scenarios/01_service_crash.sh",
    "high_latency":          "demo_app/fault_scenarios/02_high_latency.sh",
    "memory_pressure":       "demo_app/fault_scenarios/03_memory_pressure.sh",
    "cpu_throttling":        "demo_app/fault_scenarios/04_cpu_throttling.sh",
    "connection_exhaustion": "demo_app/fault_scenarios/05_connection_exhaustion.sh",
    "network_partition":     "demo_app/fault_scenarios/06_network_partition.sh",
    "cascading_failure":     "demo_app/fault_scenarios/07_cascading_failure.sh",
    "config_error":          "demo_app/fault_scenarios/08_config_error.sh",
}

GROUND_TRUTH = {
    "service_crash":         "cartservice",
    "high_latency":          "paymentservice",
    "memory_pressure":       "checkoutservice",
    "cpu_throttling":        "productcatalogservice",
    "connection_exhaustion": "redis",
    "network_partition":     "paymentservice",
    "cascading_failure":     "cartservice",
    "config_error":          "currencyservice",
}


def run_fault_injection(
    fault_type: str,
    run_id: int,
    output_dir: Path,
    agent: "AgentExecutor",
    max_wait_seconds: int = 120,
) -> dict:
    """Execute one fault injection test, call agent investigation, and return the result dict."""
    record = {
        "test_id":          f"{fault_type}_run_{run_id}",
        "fault_type":       fault_type,
        "run_id":           run_id,
        "ground_truth":     GROUND_TRUTH[fault_type],
        "fault_start_time": datetime.now().isoformat(),
        "status":           "running",
    }

    try:
        subprocess.run(["bash", FAULT_SCRIPTS[fault_type]], check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        record["status"] = "failed"
        record["error"]  = str(e)
        record["fault_end_time"] = datetime.now().isoformat()
        out_file = output_dir / f"{fault_type}_run_{run_id}.json"
        with open(out_file, "w") as f:
            json.dump(record, f, indent=2)
        return record

    record["fault_end_time"] = datetime.now().isoformat()

    # Wait for the Fast Loop to detect an anomaly, then trigger the agent.
    # In production the AnomalyDetector fires investigate() automatically via callback;
    # here we poll and call it explicitly for evaluation control.
    print(f"  Waiting up to {max_wait_seconds}s for anomaly detection...")
    alert_time = None
    deadline = time.time() + max_wait_seconds
    while time.time() < deadline:
        # Simple proxy: wait a fixed interval then trigger investigation
        time.sleep(10)
        alert_time = datetime.now().isoformat()
        break  # In production, replace with actual alert queue check

    if alert_time is None:
        record["status"]     = "no_detection"
        record["is_correct"] = False
    else:
        record["alert_time"] = alert_time
        alert = {
            "title":     f"Fault Injection Evaluation — {fault_type}",
            "severity":  "high",
            "timestamp": alert_time,
            "anomaly_score": 1.0,  # synthetic: fault was manually injected
        }
        # Live mode: pass only the alert; agent queries Prometheus/Loki via tools
        report = agent.investigate(alert=alert)

        record["investigation_complete_time"]    = datetime.now().isoformat()
        record["detection_latency_seconds"]      = (
            datetime.fromisoformat(record["alert_time"]) -
            datetime.fromisoformat(record["fault_start_time"])
        ).total_seconds()
        record["investigation_duration_seconds"] = (
            datetime.fromisoformat(record["investigation_complete_time"]) -
            datetime.fromisoformat(record["alert_time"])
        ).total_seconds()
        record["predicted_root_cause"] = report.get("root_cause")
        record["top_3_predictions"]    = report.get("top_3_predictions", [])
        record["confidence"]           = report.get("confidence", 0.0)
        record["is_correct"] = record["predicted_root_cause"] == record["ground_truth"]
        record["status"]     = "completed"

    out_file = output_dir / f"{fault_type}_run_{run_id}.json"
    with open(out_file, "w") as f:
        json.dump(record, f, indent=2)

    return record


def _load_per_fault_cooldowns(config_path: str = "configs/evaluation_scenarios.yaml") -> dict:
    """Load per-fault cooldown_seconds from evaluation_scenarios.yaml. Returns empty dict on error."""
    try:
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        # evaluation_scenarios.yaml uses top-level key "fault_types" with "name" and "cooldown_seconds"
        fault_types = cfg.get("fault_types", [])
        return {s["name"]: s.get("cooldown_seconds", 300) for s in fault_types}
    except (FileNotFoundError, KeyError):
        return {}


def main():
    parser = argparse.ArgumentParser(description="Run OTel Demo fault injection suite")
    parser.add_argument("--fault",       help="Single fault type to run (default: all)")
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--output",      default="data/evaluation/results/")
    parser.add_argument("--cooldown",    type=int, default=None,
                        help="Global cooldown override (seconds between tests). "
                             "If not set, per-fault cooldown_seconds from evaluation_scenarios.yaml is used.")
    parser.add_argument("--max-wait",    type=int, default=120,
                        help="Max seconds to wait for anomaly detection per test (default: 120)")
    args = parser.parse_args()

    from src.agent.executor import AgentExecutor
    agent = AgentExecutor.from_config("configs/agent_config.yaml")

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    faults = [args.fault] if args.fault else list(FAULT_SCRIPTS.keys())

    # Per-fault cooldowns from config; CLI --cooldown overrides all
    per_fault_cooldown = _load_per_fault_cooldowns()

    for fault_type in faults:
        for run_id in range(1, args.repetitions + 1):
            print(f"\n{'='*60}")
            print(f"Running: {fault_type} (Run {run_id}/{args.repetitions})")
            print(f"{'='*60}")
            result = run_fault_injection(
                fault_type, run_id, output_dir,
                agent=agent, max_wait_seconds=args.max_wait,
            )
            print(f"Status: {result['status']}")
            if result.get("is_correct") is not None:
                print(f"Correct: {result['is_correct']}  "
                      f"(predicted={result.get('predicted_root_cause')}, "
                      f"truth={result.get('ground_truth')})")

            # Cooldown before next test (skip after last test)
            if not (run_id == args.repetitions and fault_type == faults[-1]):
                cooldown = args.cooldown if args.cooldown is not None \
                    else per_fault_cooldown.get(fault_type, 300)
                print(f"Cooldown: {cooldown}s...")
                time.sleep(cooldown)

    print("\nFault injection suite complete.")


if __name__ == "__main__":
    main()
```

**Run the suite:**
```bash
# All faults, 5 runs each (recommended sequence: easy → hard)
python tests/evaluation/fault_injection_suite.py --output data/evaluation/results/

# Single fault type (for debugging)
python tests/evaluation/fault_injection_suite.py --fault cascading_failure --repetitions 1
```

### 2.4 Per-Test Result JSON Template

```json
{
  "test_id":                    "cascading_failure_run_3",
  "fault_type":                 "cascading_failure",
  "run_id":                     3,
  "ground_truth":               "cartservice",
  "fault_start_time":           "2025-03-25T10:15:00Z",
  "alert_time":                 "2025-03-25T10:15:42Z",
  "investigation_complete_time":"2025-03-25T10:17:23Z",
  "detection_latency_seconds":  42,
  "investigation_duration_seconds": 101,
  "predicted_root_cause":       "cartservice",
  "top_3_predictions":          ["cartservice", "checkoutservice", "frontend"],
  "confidence":                 0.87,
  "is_correct":                 true,
  "rca_report_file":            "data/evaluation/reports/cascading_failure_run_3.md",
  "explanation_quality":        null,
  "notes":                      ""
}
```

### 2.5 Pre-Evaluation Checklist

- [ ] All Docker services healthy (`docker compose ps`)
- [ ] ≥ 24h since last fault injection (system in steady state)
- [ ] LSTM-AE model loaded (`finetuned_otel.pt`) and anomaly threshold set
- [ ] All 5 agent tools verified (return valid responses on test queries)
- [ ] RCAEval adapter smoke-tested against all three variants
- [ ] `data/evaluation/results/` directory exists and is empty
- [ ] Logging configured with millisecond timestamps
- [ ] `/tmp/fault_log.txt` cleared

---

## 3. Track 2 — RCAEval Cross-System Validation

### 3.1 Dataset Overview

| Variant | Modalities | Fault Types | Cases | Purpose |
|---|---|---|---|---|
| **RE1** | Metrics only | CPU, MEM, DISK, DELAY, LOSS | 375 | Metrics-only generalization; run first |
| **RE2** | Metrics + Logs + Traces | CPU, MEM, DISK, DELAY, LOSS, SOCKET | 270 | Multi-modal; primary comparison track |
| **RE3** | Metrics + Logs + Traces | 5 code-level faults | 90 | Code-level fault generalization |

> **RE1 can run before full deployment:** RE1 requires only the `query_metrics` agent tool (no Loki), so it can run as soon as the LSTM-AE and PC algorithm are working, before the full infrastructure stack is complete.

### 3.2 Running the Cross-System Evaluation

```python
# scripts/run_evaluation.py
from src.agent.executor import AgentExecutor
from tests.evaluation.rcaeval_evaluation import run_all_rcaeval_variants

agent = AgentExecutor.from_config("configs/agent_config.yaml")

all_results = run_all_rcaeval_variants(agent=agent, base_path="data/RCAEval/")

for variant, summary in all_results.items():
    print(f"\n{variant.upper()}:")
    print(f"  Recall@1:    {summary['recall_at_1']:.1%}")
    print(f"  Recall@3:    {summary['recall_at_3']:.1%}")
    print(f"  Total cases: {summary['total_cases']}")
```

### 3.3 Published Baselines for Comparison

All baselines below are importable from the `RCAEval` package and use the exact same cases that OpsAgent is evaluated on.

| Baseline | Method | Modalities | Primary Comparison |
|---|---|---|---|
| **BARO** | Correlation-based ranking | Metrics | RE1, RE2 |
| **CIRCA** | PC-based causal scoring | Metrics | RE1, RE2 (most comparable to OpsAgent) |
| **RCD** | Randomized conditional independence | Metrics | RE1, RE2 |
| **CausalRCA** | Causal graph + propagation | Metrics | RE1, RE2 |
| **MicroHECL** | Service dependency GNN | Metrics | RE1, RE2 |
| **E-Diagnosis** | Ensemble scoring | Metrics | RE1, RE2 |
| **Nezha** | Trace-based RCA | Metrics + Traces | RE2, RE3 only |

```python
# tests/evaluation/baseline_comparison.py
from RCAEval.baselines import CIRCA, BARO, RCD
from src.preprocessing.rcaeval_adapter import RCAEvalDataAdapter
from tests.evaluation.metrics_calculator import recall_at_1

BASELINES = {"CIRCA": CIRCA, "BARO": BARO, "RCD": RCD}

for variant in ["re1", "re2"]:
    adapter = RCAEvalDataAdapter(f"data/RCAEval/{variant}/")
    for name, cls in BASELINES.items():
        baseline = cls()
        preds, truths = [], []
        for case in adapter.iter_cases():
            pred = baseline.predict(
                metrics=case["metrics"],
                ground_truth=case["ground_truth"],
            )
            preds.append(pred.get("root_cause"))
            truths.append(case["ground_truth"]["root_cause_service"])
        r1 = recall_at_1(preds, truths)
        print(f"{name} {variant.upper()} Recall@1: {r1:.1%}")
```

> **Interpretation note:** OpsAgent uses LLM reasoning + tool use, whereas published baselines are purely algorithmic. Direct numerical comparison is informative but differences should be contextualized: OpsAgent pays in LLM API cost what it gains in investigative depth (evidence chains, runbook retrieval, counterfactual analysis).

---

## 4. Track 3 — LogHub HDFS Anomaly Detection Benchmark *(Nice-to-Have)*

> **This track is designated nice-to-have.** Complete it only after Tracks 1 and 2 are fully done. HDFS is always used for LSTM-AE pretraining (required), but the benchmark evaluation below is optional. See `context/anomaly_detection_specs.md` §11 for full details.

### 4.1 Purpose and Scope

This track evaluates the LSTM-Autoencoder **in isolation** (independent of the agent pipeline) on a large-scale labeled benchmark. It provides a quantitative ML metric comparable to published methods (DeepLog, LogRobust, LogBERT).

**Evaluation stages:**
1. **Stage 2 (Pretrained on HDFS):** Evaluate LSTM-AE directly after HDFS pretraining, before any OTel fine-tuning. Baseline for transfer learning benefit.
2. **Stage 3 (Fine-tuned on OTel Demo):** Evaluate LSTM-AE after fine-tuning on OTel Demo normal sequences, then re-tested on HDFS labeled sequences.

### 4.2 Benchmark Code

```python
# tests/evaluation/loghub_benchmark.py
"""
Evaluate LSTM-Autoencoder on LogHub HDFS labeled sequences.
Run at two stages: after HDFS pretraining, and after OTel fine-tuning.
"""
import numpy as np
import torch
from sklearn.metrics import f1_score, precision_score, recall_score

from src.preprocessing.loghub_preprocessor import LogHubHDFSPreprocessor
from src.anomaly_detection.lstm_autoencoder import LSTMAutoencoder


def evaluate_on_hdfs(
    model_checkpoint: str,
    data_dir: str = "data/LogHub/HDFS/",
    seq_length: int = 10,
    threshold_percentile: int = 95,
) -> dict:
    """
    Evaluate LSTM-AE on LogHub HDFS labeled block sequences.

    Args:
        model_checkpoint:     Path to saved model weights (.pt file).
        data_dir:             Path to directory with HDFS.log + anomaly_label.csv.
        seq_length:           Sequence length (must match training config).
        threshold_percentile: Percentile of normal reconstruction errors to use
                              as anomaly threshold.

    Returns:
        dict with precision, recall, f1, threshold.
    """
    # Load and label all sequences
    preprocessor = LogHubHDFSPreprocessor(data_dir, seq_length=seq_length)
    preprocessor.parse()
    sequences, labels = preprocessor.get_labeled_sequences()

    # Load model and set to eval mode
    # input_dim is derived from num_templates after parse() completes
    model = LSTMAutoencoder(input_dim=preprocessor.num_templates)
    state_dict = torch.load(model_checkpoint, map_location="cpu")
    if isinstance(state_dict, dict) and "model_state_dict" in state_dict:
        state_dict = state_dict["model_state_dict"]
    model.load_state_dict(state_dict)
    model.eval()

    # Compute reconstruction errors
    with torch.no_grad():
        errors = []
        batch_size = 512
        for i in range(0, len(sequences), batch_size):
            batch = torch.FloatTensor(sequences[i:i + batch_size])
            recon_error = model.get_reconstruction_error(batch)
            errors.extend(recon_error.cpu().numpy().tolist())

    errors = np.array(errors)

    # Set threshold from normal sequences only (labels==0)
    normal_errors = errors[labels == 0]
    threshold = float(np.percentile(normal_errors, threshold_percentile))

    # Predict
    y_pred = (errors > threshold).astype(int)

    return {
        "precision": float(precision_score(labels, y_pred)),
        "recall":    float(recall_score(labels, y_pred)),
        "f1":        float(f1_score(labels, y_pred)),
        "threshold": threshold,
        "n_normal":  int((labels == 0).sum()),
        "n_anomaly": int((labels == 1).sum()),
        "anomaly_rate": float(labels.mean()),
    }


if __name__ == "__main__":
    print("Stage 2: Pretrained on HDFS (before OTel fine-tuning)")
    stage2 = evaluate_on_hdfs("models/lstm_autoencoder/pretrained_hdfs.pt")
    print(f"  F1={stage2['f1']:.3f}  Precision={stage2['precision']:.3f}  Recall={stage2['recall']:.3f}")

    print("\nStage 3: Fine-tuned on OTel Demo (after transfer)")
    stage3 = evaluate_on_hdfs("models/lstm_autoencoder/finetuned_otel.pt")
    print(f"  F1={stage3['f1']:.3f}  Precision={stage3['precision']:.3f}  Recall={stage3['recall']:.3f}")
```

### 4.3 Published Method Comparison

| Method | HDFS F1 | Notes |
|---|---|---|
| **DeepLog** | 0.941 | LSTM next-event prediction (sequence model) |
| **LogRobust** | 0.978 | TF-IDF + BiLSTM with robustness to log instability |
| **LogBERT** | 0.960 | BERT-based masked log event prediction |
| **OpsAgent LSTM-AE (target)** | **≥ 0.90** | Reconstruction-based; generalist (not HDFS-specialist) |

> **Expected gap is acceptable:** OpsAgent's LSTM-AE is designed as a generalist anomaly detector across multiple datasets, not a HDFS specialist. A gap vs. DeepLog (HDFS-specialized) is expected and should be discussed as a deliberate trade-off in the final report.

---

## 5. Metric Definitions and Implementations

### 5.1 `metrics_calculator.py`

```python
# tests/evaluation/metrics_calculator.py
from __future__ import annotations
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import scipy.stats as stats


@dataclass
class EvaluationResults:
    recall_at_1:            float
    recall_at_3:            float
    precision:              float  # Set separately from false-positive test
    avg_detection_latency:  float
    avg_mttr_proxy:         float
    avg_explanation_quality:float
    recall_by_fault:        Dict[str, float] = field(default_factory=dict)
    latency_by_fault:       Dict[str, List[float]] = field(default_factory=dict)  # raw latency lists for box plot
    ci_recall_at_1:         Optional[Tuple[float, float]] = None


def load_results(results_dir: str) -> List[dict]:
    """Load all per-test JSON files from a directory."""
    results = []
    for f in Path(results_dir).glob("*.json"):
        with open(f) as fp:
            results.append(json.load(fp))
    return results


def recall_at_1(predictions: List[str], ground_truths: List[str]) -> float:
    """Proportion of cases where top prediction == ground truth."""
    if not predictions:
        return 0.0
    correct = sum(p == t for p, t in zip(predictions, ground_truths))
    return correct / len(predictions)


def recall_at_3(
    top3_predictions: List[List[str]],
    ground_truths: List[str],
) -> float:
    """Proportion of cases where ground truth is in the top 3 predictions."""
    if not top3_predictions:
        return 0.0
    correct = sum(t in preds[:3] for preds, t in zip(top3_predictions, ground_truths))
    return correct / len(top3_predictions)


def precision(true_positives: int, false_positives: int) -> float:
    """
    Precision from the 24-hour false positive test.
    true_positives:  alerts fired during actual fault injections
    false_positives: alerts fired during the 24h normal operation window
    """
    total = true_positives + false_positives
    return 1.0 if total == 0 else true_positives / total


def detection_latency(fault_start: str, alert_time: str) -> float:
    """Seconds from fault injection start to first alert (ISO-8601 strings)."""
    from datetime import datetime
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return (datetime.strptime(alert_time, fmt) - datetime.strptime(fault_start, fmt)).total_seconds()


def mttr_proxy(alert_time: str, rca_complete: str, is_correct: bool) -> Optional[float]:
    """Seconds from alert to RCA completion. None if root cause was incorrect."""
    if not is_correct:
        return None
    from datetime import datetime
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return (datetime.strptime(rca_complete, fmt) - datetime.strptime(alert_time, fmt)).total_seconds()


def confidence_interval(data: List[float], confidence: float = 0.95) -> Tuple[float, float]:
    """95% CI for the mean using a t-distribution (appropriate for n < 40)."""
    arr = np.array(data)
    n = len(arr)
    if n < 2:
        return (float(arr.mean()), float(arr.mean()))
    se = stats.sem(arr)
    h  = se * stats.t.ppf((1 + confidence) / 2, df=n - 1)
    return (float(arr.mean() - h), float(arr.mean() + h))


def calculate_metrics(results: List[dict]) -> EvaluationResults:
    """Compute all metrics from a list of result dicts."""
    preds    = [r.get("predicted_root_cause") for r in results]
    top3     = [r.get("top_3_predictions", []) for r in results]
    truths   = [r.get("ground_truth") for r in results]

    r1 = recall_at_1(preds, truths)
    r3 = recall_at_3(top3, truths)

    # Detection latency (OTel Demo results have this field)
    latencies = [r["detection_latency_seconds"] for r in results if "detection_latency_seconds" in r]
    avg_latency = float(np.mean(latencies)) if latencies else 0.0  # scalar mean for EvaluationResults.avg_detection_latency

    # MTTR proxy — only for correct predictions
    mttr_vals = [
        r["investigation_duration_seconds"]
        for r in results
        if r.get("is_correct") and "investigation_duration_seconds" in r
    ]
    avg_mttr = float(np.mean(mttr_vals)) if mttr_vals else 0.0

    # Explanation quality (manually scored subset only)
    quality = [r["explanation_quality"] for r in results if r.get("explanation_quality") is not None]
    avg_quality = float(np.mean(quality)) if quality else 0.0

    # Per-fault-type breakdown
    fault_types = sorted(set(r.get("fault_type", "unknown") for r in results))
    recall_by_fault, latency_by_fault = {}, {}

    for fault in fault_types:
        subset = [r for r in results if r.get("fault_type") == fault]
        correct = sum(1 for r in subset if r.get("is_correct"))
        recall_by_fault[fault] = correct / len(subset)
        lat = [r["detection_latency_seconds"] for r in subset if "detection_latency_seconds" in r]
        latency_by_fault[fault] = lat  # raw list — used by plot_latency_distribution for box plot

    # Confidence interval for Recall@1
    binary = [1 if r.get("is_correct") else 0 for r in results]
    ci = confidence_interval(binary)

    return EvaluationResults(
        recall_at_1=r1,
        recall_at_3=r3,
        precision=0.0,          # Computed separately from 24h false positive test
        avg_detection_latency=avg_latency,
        avg_mttr_proxy=avg_mttr,
        avg_explanation_quality=avg_quality,
        recall_by_fault=recall_by_fault,
        latency_by_fault=latency_by_fault,
        ci_recall_at_1=ci,
    )
```

---

## 6. Baseline Comparisons (OTel Demo Track)

Three internal baselines are evaluated against OTel Demo fault injection results to quantify OpsAgent's added value at each system layer.

### 6.1 Baseline 1 — Rule-Based Alerting

**Description:** Static threshold alerts with no investigation. Root cause is inferred as the first service to breach threshold.

| Threshold | Metric |
|---|---|
| Error rate > 5% | `error_rate` |
| Latency P99 > 500 ms | `latency_p99` |
| CPU usage > 85% | `cpu_usage` |
| Memory usage > 85% | `memory_usage` |

**Expected Recall@1:** ~30–40% (poor on cascading failures, config errors — misidentifies downstream symptoms as root cause).

### 6.2 Baseline 2 — Anomaly Detection Only

**Description:** LSTM-AE detects anomalous services; top anomaly score = predicted root cause. No agent investigation, no causal discovery.

**Expected Recall@1:** ~50–60% (better than rule-based, but still confuses downstream degradation with root cause in cascading failures).

### 6.3 Baseline 3 — LLM Without Tools

**Description:** Raw alert + static metric snapshot fed directly to Gemini 1.5 Flash. No tool calls, no causal discovery — pure LLM reasoning from the initial context window.

**Expected Recall@1:** ~60–70% (shows LLM adds value over pure anomaly detection; establishes the benefit of adding tools).

### 6.4 Expected Performance Table

| Metric | Rule-Based | AD-Only | LLM-No-Tools | **OpsAgent** |
|---|---|---|---|---|
| Recall@1 | ~35% | ~55% | ~65% | **≥ 80%** |
| Recall@3 | ~55% | ~75% | ~82% | **≥ 95%** |
| Precision | ~45% | ~65% | ~70% | **≥ 70%** |
| Cascading Fault R@1 | ~15% | ~35% | ~55% | **≥ 80%** |
| Avg MTTR proxy | N/A | N/A | ~120s | **< 90s** |

---

## 7. Explanation Quality Scoring

### 7.1 Scope and Sample Size

- **Minimum:** 25 reports manually scored (OTel Demo only)
- **Recommended:** 30 reports covering all 8 fault types (≥ 3 per type)
- RCAEval reports are **not** manually scored — only Recall@1/Recall@3 apply there

### 7.2 Scoring Rubric (1–5 Scale)

| Score | Label | Criteria |
|---|---|---|
| **5 — Excellent** | Correct root cause; clear evidence chain; accurate causal graph; specific, actionable recommendations; well-formatted |
| **4 — Good** | Correct root cause; most evidence relevant; minor formatting issues; helpful recommendations |
| **3 — Adequate** | Root cause partially correct OR missing key evidence; generic recommendations |
| **2 — Poor** | Wrong root cause OR missing most evidence; confusing structure; unhelpful recommendations |
| **1 — Very Poor** | Completely wrong analysis; evidence contradicts conclusion; would mislead investigation |

### 7.3 Weighted Scoring Breakdown

| Criterion | Weight | Evaluation Question |
|---|---|---|
| **Root Cause Accuracy** | 30% | Is the identified root cause correct? |
| **Evidence Quality** | 25% | Is the evidence chain logical, relevant, and sufficient? |
| **Causal Analysis** | 20% | Does the causal graph correctly reflect service relationships? |
| **Recommendations** | 15% | Are remediation actions specific, useful, and prioritized? |
| **Presentation** | 10% | Is the report clearly structured and readable? |

**Overall score = Σ(criterion_score × weight)**

### 7.4 Scoring Spreadsheet

`data/evaluation/explanation_quality_scores.csv`:
```csv
test_id,fault_type,root_cause_accuracy,evidence_quality,causal_analysis,recommendations,presentation,overall_score,notes
cascading_failure_run_1,cascading_failure,5,4,5,4,5,4.6,"Excellent causal graph; recommendations could be more specific"
service_crash_run_1,service_crash,5,5,4,5,4,4.7,"Clear evidence chain; causal graph simple but correct"
config_error_run_2,config_error,4,3,4,4,4,3.75,"Root cause correct but evidence chain thin"
```

---

## 8. Statistical Analysis

### 8.1 Confidence Intervals

Use a **t-distribution** (not z-distribution) for all confidence intervals given small sample sizes (n = 40 OTel Demo tests, n < 100 per RCAEval fault type).

```python
import scipy.stats as stats
import numpy as np
from typing import List, Tuple

def confidence_interval_95(data: List[float]) -> Tuple[float, float]:
    """95% CI for the mean. Uses t-distribution (appropriate for n < 40)."""
    arr = np.array(data)
    n   = len(arr)
    se  = stats.sem(arr)
    h   = se * stats.t.ppf(0.975, df=n - 1)
    return float(arr.mean() - h), float(arr.mean() + h)

# Report as: "Recall@1: 82% ± 5% (95% CI: [77%, 87%])"
binary_correct = [1 if r["is_correct"] else 0 for r in results]
lo, hi = confidence_interval_95(binary_correct)
mean   = np.mean(binary_correct)
print(f"Recall@1: {mean:.0%} (95% CI: [{lo:.0%}, {hi:.0%}])")
```

### 8.2 Statistical Significance Testing

Use **McNemar's test** when comparing paired binary outcomes (is_correct per test) between OpsAgent and each baseline.

```python
from statsmodels.stats.contingency_tables import mcnemar

def mcnemar_test(opsagent_correct: List[bool], baseline_correct: List[bool]) -> dict:
    """
    Paired comparison of correct/incorrect classifications.
    Tests: H₀ = OpsAgent and baseline have equal accuracy.

    Returns p-value and whether result is significant at α=0.05.
    """
    # Contingency table: [[both correct, opsagent wrong/baseline right],
    #                      [opsagent right/baseline wrong, both wrong]]
    n01 = sum(1 for a, b in zip(opsagent_correct, baseline_correct) if not a and b)
    n10 = sum(1 for a, b in zip(opsagent_correct, baseline_correct) if a and not b)
    table = [[0, n01], [n10, 0]]
    result = mcnemar(table, exact=True)
    return {"p_value": result.pvalue, "significant": result.pvalue < 0.05}
```

### 8.3 Results Table Format

```
| Metric          | OpsAgent     | Rule-Based   | AD-Only      | LLM-No-Tools |
|-----------------|-------------|-------------|-------------|-------------|
| Recall@1        | 82% ± 5%    | 35% ± 7%    | 55% ± 6%    | 65% ± 5%    |
| p-val vs OpsAgt | —           | < 0.001 **  | 0.003 **    | 0.021 *     |
```

---

## 9. Required Visualizations

All 11 visualizations are created in `notebooks/08_evaluation_analysis.ipynb`.

| # | Visualization | Type | Track |
|---|---|---|---|
| 1 | Recall@1 by OTel Demo fault type | Bar chart (color-coded vs. 80% target) | Primary |
| 2 | OTel Demo baseline comparison (R@1, R@3, Precision) | Grouped bar chart | Primary |
| 3 | Detection latency distribution by fault type | Box plot | Primary |
| 4 | Confusion matrix (predicted vs. actual root cause) | Seaborn heatmap | Primary |
| 5 | Causal graph examples (2–3 sample RCA reports) | NetworkX diagrams | Primary |
| 6 | Agent tool usage distribution | Pie chart | Primary |
| 7 | Explanation quality score distribution | Histogram | Primary |
| 8 | RCAEval Recall@1 by variant — OpsAgent vs. BARO/CIRCA/RCD | Grouped bar chart | Cross-System |
| 9 | RCAEval Recall@1 by fault type (RE2 breakdown) | Bar chart | Cross-System |
| 10 *(nice-to-have)* | HDFS F1 comparison — Stage 2 vs. Stage 3 vs. DeepLog | Bar chart | AD Benchmark |
| 11 *(nice-to-have)* | Training loss curves — HDFS pretraining + OTel fine-tuning | Line chart | AD Benchmark |

### 9.1 Visualization Code

```python
# notebooks/08_evaluation_analysis.ipynb
import json
from pathlib import Path
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import seaborn as sns
import numpy as np
import pandas as pd

plt.rcParams.update({"figure.dpi": 150, "font.size": 11})
SAVE_DIR = Path("docs/images/evaluation_charts/")
SAVE_DIR.mkdir(parents=True, exist_ok=True)


# ── Visualization 1: Recall@1 by Fault Type ────────────────────────────────

def plot_recall_by_fault(recall_by_fault: dict):
    fig, ax = plt.subplots(figsize=(11, 5))
    faults  = list(recall_by_fault.keys())
    recalls = [recall_by_fault[f] for f in faults]
    colors  = ["#2ecc71" if r >= 0.8 else "#e67e22" if r >= 0.6 else "#e74c3c" for r in recalls]

    ax.bar(faults, recalls, color=colors, edgecolor="white", linewidth=0.5)
    ax.axhline(y=0.8, color="#27ae60", linestyle="--", linewidth=1.5, label="Target 80%")
    ax.set_xlabel("Fault Type")
    ax.set_ylabel("Recall@1")
    ax.set_title("Root Cause Accuracy by Fault Type — OTel Demo")
    ax.set_ylim(0, 1.05)
    ax.set_xticklabels(faults, rotation=35, ha="right")
    ax.legend()
    fig.tight_layout()
    fig.savefig(SAVE_DIR / "01_recall_by_fault.png")
    plt.close()


# ── Visualization 2: Baseline Comparison ───────────────────────────────────

def plot_baseline_comparison(data: dict):
    """data = {"OpsAgent": [r1, r3, prec], "Rule-Based": [...], ...}"""
    methods = list(data.keys())
    metrics = ["Recall@1", "Recall@3", "Precision"]
    x = np.arange(len(metrics))
    width = 0.2

    fig, ax = plt.subplots(figsize=(10, 6))
    for i, method in enumerate(methods):
        vals = data[method]
        ax.bar(x + i * width, vals, width, label=method)

    ax.set_xticks(x + width * (len(methods) - 1) / 2)
    ax.set_xticklabels(metrics)
    ax.set_ylabel("Score")
    ax.set_ylim(0, 1.05)
    ax.set_title("OTel Demo — OpsAgent vs. Baselines")
    ax.legend()
    fig.tight_layout()
    fig.savefig(SAVE_DIR / "02_baseline_comparison.png")
    plt.close()


# ── Visualization 3: Detection Latency Box Plot ─────────────────────────────

def plot_latency_distribution(latency_by_fault: dict):
    fig, ax = plt.subplots(figsize=(11, 5))
    ax.boxplot(
        [latency_by_fault[f] for f in latency_by_fault],
        labels=list(latency_by_fault.keys()),
        patch_artist=True,
    )
    ax.axhline(y=60, color="red", linestyle="--", label="Target < 60s")
    ax.set_ylabel("Detection Latency (seconds)")
    ax.set_title("Detection Latency Distribution by Fault Type")
    ax.set_xticklabels(list(latency_by_fault.keys()), rotation=35, ha="right")
    ax.legend()
    fig.tight_layout()
    fig.savefig(SAVE_DIR / "03_detection_latency.png")
    plt.close()


# ── Visualization 4: Confusion Matrix ──────────────────────────────────────

def plot_confusion_matrix(results: list):
    services = sorted(set(
        r.get("ground_truth") for r in results
    ) | set(r.get("predicted_root_cause") for r in results if r.get("predicted_root_cause")))

    matrix = np.zeros((len(services), len(services)), dtype=int)
    idx = {s: i for i, s in enumerate(services)}

    for r in results:
        true_svc = r.get("ground_truth")
        pred_svc = r.get("predicted_root_cause")
        if true_svc in idx and pred_svc in idx:
            matrix[idx[true_svc]][idx[pred_svc]] += 1

    fig, ax = plt.subplots(figsize=(9, 7))
    sns.heatmap(
        matrix, annot=True, fmt="d", cmap="Blues",
        xticklabels=services, yticklabels=services, ax=ax,
    )
    ax.set_xlabel("Predicted Root Cause")
    ax.set_ylabel("Actual Root Cause")
    ax.set_title("Root Cause Confusion Matrix — OTel Demo")
    ax.set_xticklabels(services, rotation=35, ha="right")
    fig.tight_layout()
    fig.savefig(SAVE_DIR / "04_confusion_matrix.png")
    plt.close()


# ── Visualization 8: RCAEval Cross-System Comparison ───────────────────────

def plot_rcaeval_comparison(opsagent: dict, baselines: dict):
    """
    opsagent  = {"re1": 0.72, "re2": 0.68, "re3": 0.61}
    baselines = {"CIRCA": {"re1": 0.68, "re2": 0.65}, ...}
    """
    variants = ["RE1", "RE2", "RE3"]
    methods  = ["OpsAgent"] + list(baselines.keys())
    x = np.arange(len(variants))
    width = 0.15

    fig, ax = plt.subplots(figsize=(11, 6))
    for i, method in enumerate(methods):
        if method == "OpsAgent":
            vals = [opsagent.get(v.lower(), 0) for v in variants]
        else:
            vals = [baselines[method].get(v.lower(), 0) for v in variants]
        ax.bar(x + i * width, vals, width, label=method)

    ax.set_xticks(x + width * (len(methods) - 1) / 2)
    ax.set_xticklabels(variants)
    ax.set_ylabel("Recall@1")
    ax.set_ylim(0, 1.0)
    ax.set_title("RCAEval Cross-System Validation — Recall@1 by Variant")
    ax.legend()
    fig.tight_layout()
    fig.savefig(SAVE_DIR / "08_rcaeval_comparison.png")
    plt.close()


# ── Visualization 10: HDFS F1 Comparison ───────────────────────────────────

def plot_hdfs_benchmark(stage2_f1: float, stage3_f1: float):
    methods = ["Pretrained\n(HDFS only)", "Fine-tuned\n(+ OTel Demo)", "DeepLog\n(published)", "LogRobust\n(published)"]
    f1s     = [stage2_f1, stage3_f1, 0.941, 0.978]
    colors  = ["#3498db", "#2ecc71", "#95a5a6", "#95a5a6"]

    fig, ax = plt.subplots(figsize=(9, 5))
    bars = ax.bar(methods, f1s, color=colors, edgecolor="white")
    ax.axhline(y=0.90, color="red", linestyle="--", label="Target F1 ≥ 0.90")
    ax.set_ylim(0.5, 1.05)
    ax.set_ylabel("F1 Score (Block Level)")
    ax.set_title("LSTM-AE Anomaly Detection — LogHub HDFS Benchmark")
    for bar, val in zip(bars, f1s):
        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.005,
                f"{val:.3f}", ha="center", fontsize=10)
    ax.legend()
    fig.tight_layout()
    fig.savefig(SAVE_DIR / "10_hdfs_benchmark.png")
    plt.close()
```

---

## 10. Results Document Template

`docs/evaluation_results.md` — fill in actual values during Week 10.

```markdown
# OpsAgent Evaluation Results

## Executive Summary

| Track | Key Result |
|---|---|
| OTel Demo (Primary) | Recall@1: X%, Precision: X%, Avg Detection Latency: Xs |
| RCAEval RE1 (Cross-System) | Recall@1: X% vs. CIRCA X%, BARO X% |
| RCAEval RE2 (Cross-System) | Recall@1: X% vs. CIRCA X%, Nezha X% |
| RCAEval RE3 (Cross-System) | Recall@1: X% |
| HDFS Benchmark (AD) | Pretrained F1: X.XXX, Fine-tuned F1: X.XXX |
| Explanation Quality | Avg: X.X / 5.0 (n=30 reports) |

## 1. OTel Demo Primary Evaluation

### 1.1 Recall@1 by Fault Type
[Visualization 1]

| Fault Type | R@1 | R@3 | Avg Latency |
|---|---|---|---|
| service_crash | | | |
| high_latency | | | |
| memory_pressure | | | |
| cpu_throttling | | | |
| connection_exhaustion | | | |
| network_partition | | | |
| cascading_failure | | | |
| config_error | | | |
| **Overall** | **X%** | **X%** | **Xs** |

### 1.2 Baseline Comparison
[Visualization 2]

| Metric | OpsAgent | Rule-Based | AD-Only | LLM-No-Tools |
|---|---|---|---|---|
| Recall@1 | | | | |
| Recall@3 | | | | |
| Precision | | | | |
| p-value (McNemar vs. OpsAgent) | — | | | |

### 1.3 Precision (False Positive Test)
24-hour false positive test results: X false positives out of Y total alerts.
Precision = X%

### 1.4 Detection Latency
[Visualization 3]
Mean: Xs · Median: Xs · Max: Xs

### 1.5 Confusion Matrix
[Visualization 4]
Common misclassifications: ...

### 1.6 Explanation Quality
[Visualization 7]
Mean overall score: X.X / 5.0 (n=30 reports)
Lowest-scoring criterion: [criterion] (X.X average)

## 2. RCAEval Cross-System Validation

### 2.1 Summary
[Visualization 8]

| Variant | OpsAgent R@1 | CIRCA R@1 | BARO R@1 | RCD R@1 |
|---|---|---|---|---|
| RE1 (375 cases, metrics-only) | | | | |
| RE2 (270 cases, multi-modal) | | | | |
| RE3 (90 cases, code-level) | | | | |

### 2.2 Failure Analysis
Top failure patterns:
1. Downstream symptom misidentification (cascading faults)
2. Low-confidence cases — tool budget exhausted before threshold reached
3. Cross-system vocabulary mismatch (service names not in OTel Demo training)

### 2.3 Modality Impact
RE1 → RE2 Recall@1 delta: +X% (access to logs improves accuracy)

## 3. LogHub HDFS Anomaly Detection Benchmark

[Visualization 10, 11]

| Stage | F1 | Precision | Recall |
|---|---|---|---|
| Stage 2: Pretrained on HDFS | | | |
| Stage 3: Fine-tuned on OTel Demo | | | |
| DeepLog (published, HDFS specialist) | 0.941 | — | — |
| LogRobust (published) | 0.978 | — | — |

Discussion: The gap vs. published HDFS-specialist methods (DeepLog, LogRobust) is
expected. OpsAgent's LSTM-AE is a generalist detector designed for transfer across
datasets, not a single-dataset specialist. The transfer benefit (Stage 2 → Stage 3)
is the relevant finding for this project.

## 4. Key Findings and Discussion

1. Causal discovery significantly improves cascading failure detection vs. all baselines.
2. Multi-step agent reasoning improves Recall@3 most (root cause in candidates even when not #1).
3. Cross-system generalization is [strong/moderate/limited] — discuss gap vs. published baselines.
4. LSTM-AE pretraining on LogHub accelerates convergence on OTel Demo (loss curve evidence).
```

---

## 11. Evaluation Deliverables Checklist

### Track 1 — OTel Demo
- [ ] All 8 fault injection scripts working and tested
- [ ] 40 fault injection tests completed (5 × 8 fault types)
- [ ] 24h false positive test completed
- [ ] Per-test JSONs in `data/evaluation/results/`
- [ ] 25–30 explanation quality scores in `explanation_quality_scores.csv`
- [ ] `metrics_calculator.py` run; all metrics computed
- [ ] Visualizations 1–7 created in `notebooks/08_evaluation_analysis.ipynb`

### Track 2 — RCAEval
- [ ] RCAEval adapter smoke-tested on all three variants
- [ ] RE1 evaluation complete (375 cases)
- [ ] RE2 evaluation complete (270 cases)
- [ ] RE3 evaluation complete (90 cases)
- [ ] BARO, CIRCA, RCD baselines run on RE1 + RE2
- [ ] Per-case JSONs in `data/evaluation/rcaeval_results/<variant>/`
- [ ] Summary JSONs generated for each variant
- [ ] Visualizations 8–9 created

### Track 3 — LogHub HDFS Benchmark *(Nice-to-Have — complete only if ahead of schedule)*
- [ ] `loghub_benchmark.py` run at Stage 2 (pretrained checkpoint) — record F1, Precision, Recall
- [ ] `loghub_benchmark.py` run at Stage 3 (fine-tuned checkpoint) — record F1, Precision, Recall
- [ ] Visualizations 10–11 created in `notebooks/08_evaluation_analysis.ipynb`
- [ ] Track 3 results documented in `docs/evaluation_results.md` Section 3

### Documentation
- [ ] `docs/evaluation_results.md` filled in with actual values
- [ ] Advisor check-in completed (Week 10)
- [ ] Statistical significance tests run for all OTel Demo baseline comparisons
