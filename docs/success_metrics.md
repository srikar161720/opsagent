# Success Metrics

## Overview

OpsAgent is evaluated across two primary tracks and one optional benchmark. Each metric has a defined target, evaluation source, and rationale.

## Primary Evaluation — OTel Demo Fault Injection (40 Cases)

| Metric | Target | Definition | Rationale |
|--------|--------|------------|-----------|
| **Recall@1** | >= 80% | Fraction of tests where the top-ranked prediction matches the ground truth root cause | The most important metric: does OpsAgent correctly identify the root cause on the first guess? |
| **Recall@3** | >= 95% | Fraction of tests where the ground truth root cause appears in the top-3 predictions | Relaxed target ensuring the correct answer is almost always surfaced even if not ranked first |
| **Precision** | >= 70% | 1 - (false positive rate during 24h normal operation) | Ensures OpsAgent does not generate spurious alerts under healthy conditions |
| **Detection Latency** | < 60 seconds | Time elapsed from fault injection to the first anomaly alert (Fast Loop trigger) | Measures real-time detection speed; incidents should be caught within one minute |
| **MTTR Proxy** | >= 50% reduction | Investigation time compared to rule-based and AD-only baselines | Demonstrates that OpsAgent's autonomous investigation significantly accelerates resolution |
| **Explanation Quality** | >= 4.0 / 5.0 | Average human rating of RCA report usefulness on a 5-point rubric | Validates that reports are actionable and comprehensible, not just statistically correct |

### Explanation Quality Rubric

| Score | Description |
|-------|-------------|
| 5 | Root cause correctly identified; evidence chain is complete and logically sound; remediation steps are actionable |
| 4 | Root cause correctly identified; evidence is mostly complete; minor gaps in reasoning or remediation |
| 3 | Root cause partially correct (e.g., correct service but wrong component); evidence present but incomplete |
| 2 | Root cause incorrect but investigation direction was reasonable; some useful evidence collected |
| 1 | Root cause incorrect; evidence is irrelevant or missing; report provides no diagnostic value |

Scoring procedure: Manually evaluate 25-30 RCA reports selected across all 8 fault types. Scores are logged to `data/evaluation/explanation_quality_scores.csv`.

## Cross-System Validation — RCAEval (735 Cases)

| Metric | Target | Dataset | Definition |
|--------|--------|---------|------------|
| **Recall@1 (RE2)** | Competitive with CIRCA / RCD | RE2 (270 cases, multi-modal) | OpsAgent's top prediction accuracy compared against 5 published baselines |
| **Recall@1 (RE1)** | Reported | RE1 (375 cases, metrics-only) | Baseline comparison on metrics-only data |
| **Recall@1 (RE3)** | Reported | RE3 (90 cases, code-level faults) | Generalization to code-level fault types |

Published baselines for comparison (from RCAEval paper):
- BARO
- CIRCA
- RCD
- CausalRCA
- MicroHECL

## Optional Benchmark — LogHub HDFS Anomaly Detection

| Metric | Target | Definition |
|--------|--------|------------|
| **F1 Score** | >= 0.90 | Block-level anomaly detection F1 on HDFS held-out test set |
| **Comparison** | Report vs. DeepLog (F1=0.941) and LogRobust (F1=0.978) | Contextualizes LSTM-AE performance against published baselines |

This track is designated as nice-to-have and should only be executed after Tracks 1 and 2 are fully complete.

## Internal Baseline Comparisons

OpsAgent's full-system performance is compared against three ablation baselines to demonstrate the value of each component:

| Baseline | Description | What It Tests |
|----------|-------------|---------------|
| **Rule-Based** | Static threshold alerts on individual metrics (CPU > 80%, error rate > 5%, latency P99 > 500ms) | Value of ML-based anomaly detection over simple thresholds |
| **AD-Only** | LSTM-AE anomaly detection without LangGraph agent investigation | Value of autonomous investigation beyond detection |
| **LLM-Without-Tools** | LLM reasoning with alert context but no tool calls (no Prometheus queries, no log search, no causal discovery) | Value of tool-augmented reasoning over pure LLM inference |

## Statistical Analysis

All reported metrics include:
- **95% confidence intervals** computed via bootstrap resampling (1000 iterations)
- **Paired t-tests** comparing OpsAgent against each baseline (significance level alpha = 0.05)
- **Per-fault-type breakdown** for OTel Demo results (Recall@1 by fault type)
- **Per-variant breakdown** for RCAEval results (Recall@1 by RE1/RE2/RE3)
