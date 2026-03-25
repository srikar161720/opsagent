# Problem Statement

## The Challenge

Modern microservice architectures distribute business logic across dozens of independently deployed services. When an incident occurs, the root cause often lies several hops away from the observable symptoms. A latency spike in a frontend service might originate from a memory-pressured checkout service, a misconfigured currency service, or an exhausted Redis connection pool feeding the cart service.

Today, Site Reliability Engineers (SREs) perform root cause analysis (RCA) manually: they triage alerts, correlate logs and metrics across services, trace request paths through dependency graphs, and reason about causation versus correlation. This process is time-consuming, error-prone, and heavily dependent on institutional knowledge that may not be available during an on-call rotation.

Key pain points in manual RCA include:

- **Alert fatigue:** A single root cause can trigger cascading alerts across multiple downstream services, overwhelming on-call engineers with symptoms rather than causes.
- **Signal fragmentation:** Logs, metrics, and topology information live in separate systems (Loki, Prometheus, service registries), requiring engineers to context-switch between tools and mentally correlate signals.
- **Causal ambiguity:** Temporal correlation does not imply causation. A service that degrades shortly after another may be a downstream victim rather than the root cause. Distinguishing cause from effect requires statistical rigor that is difficult to apply under incident pressure.
- **Knowledge gaps:** Effective RCA depends on understanding service dependencies, historical failure modes, and operational runbooks. This knowledge is often siloed and unavailable to the on-call engineer who needs it most.

## OpsAgent: An Autonomous RCA Agent

OpsAgent addresses these challenges by acting as a virtual SRE that continuously monitors a microservice architecture, detects anomalies across logs and metrics, and autonomously investigates incidents to produce structured RCA reports with quantified confidence scores.

### Two-Loop Architecture

OpsAgent separates real-time detection from deep investigation using a two-loop design:

**Fast Loop (Watchdog)** runs continuously and performs lightweight anomaly detection. Kafka ingests logs from microservices; Drain3 extracts structured log templates; an LSTM-Autoencoder scores log sequences against a learned baseline. Prometheus scrapes metrics in parallel. When the combined anomaly score exceeds the threshold, the Fast Loop fires a trigger to the Slow Loop.

**Slow Loop (Investigator)** activates only on confirmed anomalies. A LangGraph-powered agent conducts a multi-step investigation: it queries metrics and logs via tool calls, retrieves service topology, runs causal discovery (PC algorithm) to build a dependency graph distinguishing cause from effect, scores counterfactual confidence, searches relevant runbooks, and generates a structured RCA report. The report includes an evidence chain, the identified root cause service, a causal graph, and a confidence score.

This separation ensures detection latency remains low (target: < 60 seconds) while reserving expensive LLM reasoning for confirmed incidents only.

### Three-Dataset Strategy

OpsAgent's evaluation and training leverage three complementary data sources:

1. **OpenTelemetry Demo (self-generated):** A reduced 6-service microservice application with controlled fault injection (8 fault types, 40 test cases). Provides the primary training data and evaluation ground truth.

2. **LogHub HDFS (Zenodo DOI: 10.5281/zenodo.8196385):** 11M+ log lines with block-level anomaly labels. Used for LSTM-Autoencoder pretraining via transfer learning, enabling the model to learn general log anomaly patterns before specializing on the target environment.

3. **RCAEval RE1/RE2/RE3 (Zenodo DOI: 10.5281/zenodo.14590730):** 735 labeled failure cases across three real-world microservice systems. Used for cross-system validation against 5 published baselines (BARO, CIRCA, RCD, CausalRCA, MicroHECL), demonstrating generalization beyond the training environment.

## Target Users

- **SRE teams** managing microservice deployments who need faster incident response and reduced mean time to resolution (MTTR).
- **DevOps engineers** seeking automated correlation of logs, metrics, and service topology during outages.
- **Platform teams** looking to augment on-call engineers with AI-assisted root cause analysis that provides explainable, evidence-backed reports rather than opaque anomaly scores.
