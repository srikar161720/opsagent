# Baseline Approaches

## Overview

OpsAgent is evaluated against two categories of baselines: three internal ablation baselines that isolate the contribution of each system component, and five published baselines from the RCAEval benchmark that establish cross-system comparison points.

## Internal Baselines (OTel Demo Evaluation)

These baselines are evaluated on the same 40 OTel Demo fault injection test cases as OpsAgent.

### 1. Rule-Based

**Description:** Static threshold alerts on individual metrics without any ML-based detection or investigation.

**Alert rules:**
- CPU usage > 80% sustained for 60 seconds
- Error rate > 5% sustained for 30 seconds
- Latency P99 > 500ms sustained for 30 seconds
- Memory usage > 90% sustained for 60 seconds

**What it lacks:** No log analysis, no learned anomaly patterns, no cross-service correlation, no causal reasoning, no investigation reports. Alerts identify *that* something is wrong but not *why*.

**What it tests:** The value of ML-based anomaly detection and autonomous investigation over traditional monitoring thresholds.

### 2. AD-Only (Anomaly Detection Only)

**Description:** LSTM-Autoencoder anomaly detection with threshold-based alerting, but without the LangGraph agent investigation loop.

**Behavior:** Detects anomalies using the same trained LSTM-AE model as OpsAgent. When the reconstruction error exceeds the 95th percentile threshold, it reports the service with the highest anomaly score as the predicted root cause. No further investigation is performed.

**What it lacks:** No multi-step reasoning, no Prometheus/Loki queries for additional evidence, no causal discovery (PC algorithm), no runbook retrieval, no structured RCA reports.

**What it tests:** The value of the Slow Loop (LangGraph agent) beyond raw anomaly detection. Demonstrates whether autonomous investigation improves root cause accuracy over simple anomaly ranking.

### 3. LLM-Without-Tools

**Description:** LLM-based reasoning using the same Gemini 1.5 Flash model and system prompt, but with all tool calls disabled. The agent receives the alert context (anomaly scores, affected services, timestamp) and must reason about the root cause from that information alone.

**Behavior:** The LLM generates a hypothesis and RCA report based solely on the initial alert context, service topology knowledge embedded in the system prompt, and its general training knowledge. It cannot query Prometheus for real-time metrics, search Loki for log patterns, run causal discovery, or retrieve runbooks.

**What it lacks:** No real-time data access, no evidence gathering, no causal graph computation, no runbook-augmented remediation.

**What it tests:** The value of tool-augmented reasoning over pure LLM inference. Demonstrates whether grounding the agent in real observability data improves accuracy compared to LLM reasoning from alert summaries alone.

## Published Baselines (RCAEval Cross-System Evaluation)

These baselines are evaluated on the RCAEval benchmark (735 labeled cases across RE1/RE2/RE3). Published results are taken from the RCAEval paper (ACM WWW 2025 / IEEE/ACM ASE 2024).

### 4. BARO

**Type:** Anomaly-ranking method.

**Approach:** Uses Bayesian online changepoint detection to identify anomalous metrics, then ranks services by aggregated anomaly scores. Metrics-focused; does not perform causal reasoning.

### 5. CIRCA

**Type:** Causal inference method.

**Approach:** Constructs a causal graph from metric time series using intervention-based reasoning. Identifies root causes by tracing causal paths from observed symptoms backward to the originating service. One of the stronger published baselines on RE2.

### 6. RCD (Root Cause Discovery)

**Type:** Causal discovery method.

**Approach:** Applies constraint-based causal discovery (similar to the PC algorithm used by OpsAgent) on metric data to build a causal DAG. Ranks root cause candidates by their position in the causal graph. Strong baseline on metrics-only evaluation (RE1).

### 7. CausalRCA

**Type:** Causal inference + structural equation modeling.

**Approach:** Combines causal graph discovery with structural equation models to quantify the causal effect of each service on the observed anomaly. Produces ranked root cause candidates with effect-size scores.

### 8. MicroHECL

**Type:** Heterogeneous causal learning.

**Approach:** Uses heterogeneous data sources (metrics, logs, traces) to learn causal relationships specific to microservice architectures. Designed to leverage multi-modal telemetry, making it most relevant for comparison on RE2 (which includes metrics, logs, and traces).

## Comparison Strategy

| Evaluation Track | OpsAgent vs. | Metric |
|-----------------|-------------|--------|
| OTel Demo (40 cases) | Rule-Based, AD-Only, LLM-Without-Tools | Recall@1, Recall@3, Precision, Detection Latency, MTTR Proxy |
| RCAEval RE1 (375 cases) | BARO, CIRCA, RCD, CausalRCA, MicroHECL | Recall@1 |
| RCAEval RE2 (270 cases) | BARO, CIRCA, RCD, CausalRCA, MicroHECL | Recall@1 (primary cross-system target) |
| RCAEval RE3 (90 cases) | BARO, CIRCA, RCD, CausalRCA, MicroHECL | Recall@1 |
