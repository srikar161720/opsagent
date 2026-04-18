# OpsAgent — Architecture & Design Reference

> This file is the authoritative reference for system architecture, component interaction,
> data strategy, key design decisions with rationale, scope, and risk mitigations.
> Load this file when making architectural decisions or working on cross-cutting concerns.

---

## 1. Two-Loop Architecture

OpsAgent separates concerns into two distinct processing loops, a pattern common in production AIOps systems. The Fast Loop is always running; the Slow Loop only activates on a positive anomaly signal.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           FAST LOOP (Watchdog)                          │
│                                                                         │
│   ┌──────────┐    ┌──────────┐    ┌──────────────┐    ┌────────────┐    │
│   │  Kafka   │───▶│  Drain3  │───▶│ LSTM-AutoEnc │───▶│  Anomaly   │    │
│   │ (Logs)   │    │ (Parse)  │    │  (Detect)    │    │  Score     │    │
│   └──────────┘    └──────────┘    └──────────────┘    └─────┬──────┘    │
│                                                             │           │
│   ┌──────────┐                                              │           │
│   │Prometheus│──────────────────────────────────────────────┤           │
│   │(Metrics) │                                              │           │
│   └──────────┘                                              │           │
└─────────────────────────────────────────────────────────────┼───────────┘
                                                              │
                                              Trigger if Score > Threshold
                                                              │
                                                              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         SLOW LOOP (Investigator)                        │
│                                                                         │
│   ┌──────────────────────────────────────────────────────────────────┐  │
│   │                      LangGraph Agent                             │  │
│   │                                                                  │  │
│   │   ┌─────────┐    ┌──────────┐    ┌─────────┐    ┌────────────┐   │  │
│   │   │ Receive │───▶│ Analyze  │───▶│Hypothe- │───▶│  Gather    │   │  │
│   │   │  Alert  │    │ Context  │    │  size   │    │  Evidence  │   │  │
│   │   └─────────┘    └──────────┘    └─────────┘    └─────┬──────┘   │  │
│   │                                                       │          │  │
│   │                                        ┌──────────────┘          │  │
│   │                                        │                         │  │
│   │   ┌──────────┐    ┌──────────┐    ┌────▼────┐                    │  │
│   │   │ Generate │◀───│  Refine  │◀───│  Tool   │                    │  │
│   │   │  Report  │    │Hypothesis│    │  Calls  │                    │  │
│   │   └────┬─────┘    └──────────┘    └─────────┘                    │  │
│   │        │                                                         │  │
│   └────────┼─────────────────────────────────────────────────────────┘  │
│            ▼                                                            │
│   ┌─────────────────────────────────────────────────────────────────┐   │
│   │                       RCA Report Output                         │   │
│   │  • Root Cause (with confidence %)  • Causal Graph               │   │
│   │  • Evidence Chain                  • Counterfactual Analysis    │   │
│   │  • Recommended Actions                                          │   │
│   └─────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
```

### Why Two Loops?

Anomaly detection must be fast and lightweight — it runs continuously on every log window. Root cause analysis is expensive (LLM calls, tool invocations, causal graph computation) and only justified when an anomaly is actually detected. Separating the two avoids burning LLM API budget on normal traffic while keeping detection latency low.

---

## 2. Component Interaction Flow

The full end-to-end data flow through the system, step by step:

```
Microservices (OTel Demo)
     │
     ├── Logs ──────▶ Kafka (topic: opsagent-logs)
     │                    │
     │                    ▼
     │               LogConsumer
     │                    │
     │                    ▼
     │               LogParser (Drain3) ──▶ template_id, template_str
     │                    │
     │                    ▼
     │               WindowAggregator (60s windows)
     │                    │
     │                    ▼
     │               FeatureEngineer ──▶ (seq_len=10, feature_dim) tensor
     │                    │
     └── Metrics ─▶ MetricsCollector     │
                    (Prometheus API)     │
                         │               │
                         └───────────────┘
                                         │
                                         ▼
                               LSTMAutoencoder
                               reconstruction_error
                                         │
                               > threshold? ──No──▶ (continue monitoring)
                                         │
                                        Yes
                                         │
                                         ▼
                               AgentExecutor.investigate(alert)
                                         │
                               ┌─────────▼───────────────────────────┐
                               │         LangGraph Agent             │
                               │  analyze_context                    │
                               │    → form_hypothesis                │
                               │      → gather_evidence (tool calls) │
                               │        → analyze_causation          │
                               │          → should_continue?         │
                               │            → generate_report        │
                               └─────────────────────────────────────┘
                                         │
                                         ▼
                               RCA Report ──▶ FastAPI (POST /investigate)
                                         │         ──▶ Streamlit Dashboard
                                         │
                               data/evaluation/reports/<test_id>.md
```

### Agent Tool Call Budget

The agent is capped at **10 tool calls per investigation** to bound LLM API costs and prevent infinite loops. Typical investigation flow:

1. `get_topology` — understand service dependencies (1 call)
2. `query_metrics` × 2–3 — query Prometheus for affected + upstream services
3. `search_logs` × 2–3 — query Loki for error patterns
4. `discover_causation` — run PC algorithm on collected metric time series (1 call)
5. `search_runbooks` — retrieve relevant remediation steps (1 call)
6. Generate report (no tool call)

---

## 3. OTel Demo Service Topology

The target microservice system is the OpenTelemetry Astronomy Shop, reduced to 6 core services. This is the dependency graph `TopologyGraph` encodes as a `networkx.DiGraph`:

```
                    ┌──────────┐
                    │ frontend │ :8080
                    └────┬─────┘
           ┌─────────────┼──────────────┬──────────────┐
           ▼             ▼              ▼              ▼
    ┌───────────┐   ┌─────────┐  ┌────────────┐  ┌────────────┐
    │cartservice│   │currency │  │  product   │  │  checkout  │
    │  :7070    │   │ service │  │  catalog   │  │  service   │
    └─────┬─────┘   │  :7001  │  │ svc :3550  │  │  :5050     │
          │         └─────────┘  └────────────┘  └─────┬──────┘
          ▼                                            ├──────────────┐
      ┌───────┐                                        ▼              ▼
      │ redis │                              ┌────────────┐    ┌───────────┐
      │ :6379 │                              │  payment   │    │ (cart,    │
      └───────┘                              │  service   │    │  catalog, │
	                                         │  :50051    │    │  currency │
                                             └────────────┘    │  again)   │
                                                               └───────────┘
```

**Excluded OTel Demo services** (not deployed to save ~2GB RAM):
`adservice`, `recommendationservice`, `emailservice`, `shippingservice`

**Key topology facts for agent reasoning:**
- `redis` is a leaf node — failures here propagate immediately to `cartservice`
- `frontend` is the entry point — its latency is the symptom most often observed first
- `checkoutservice` calls 4 other services — it is the most likely cascade amplifier
- All inter-service communication uses gRPC

---

## 4. Data Strategy

Three complementary sources, each with a non-overlapping role:

```
┌───────────────────────────────────────────────────────────────────────────┐
│                          OpsAgent Data Strategy                           │
├─────────────────────┬───────────────────────┬─────────────────────────────┤
│   PRETRAINING       │   PRIMARY EVAL        │   CROSS-SYSTEM VALIDATION   │
│                     │                       │                             │
│  LogHub (HDFS)      │  OpenTelemetry Demo   │  RCAEval (RE1 / RE2 / RE3)  │
│  • 11M+ logs        │  • 24h baseline data  │  • RE1: 375 cases (metrics) │
│  • Block-level      │  • 7 fault types      │  • RE2: 271 cases (multi-   │
│    anomaly labels   │  • 5 runs each        │    modal: metrics+logs)     │
│  • ~1GB on disk     │  • 35 test cases      │  • RE3: 90 cases (code-     │
│                     │  • Known ground truth │    level faults)            │
│  Purpose:           │                       │  • 736 total labeled cases  │
│  Transfer learning  │  Purpose:             │                             │
│  for LSTM-AE;       │  Controlled exps.     │  Purpose:                   │
│  benchmark vs.      │  with known fault     │  Real-world generalization; │
│  DeepLog/LogRobust  │  injection ground     │  comparison to 5 published  │
│                     │  truth                │  baselines (BARO, CIRCA,    │
│  DOI: 10.5281/      │                       │  RCD, CausalRCA, MicroHECL) │
│  zenodo.8196385     │  Self-generated by    │                             │
│                     │  running the stack    │  DOI: 10.5281/              │
│                     │  + fault scripts      │  zenodo.14590730            │
└─────────────────────┴───────────────────────┴─────────────────────────────┘
```

**Critical constraint:** RCAEval and LogHub datasets are processed **offline** — they are never run as live Docker services. The ~7GB Docker memory budget is entirely for the OTel Demo + monitoring stack. Dataset processing (pretraining, RCAEval evaluation) runs as standalone Python scripts or in Google Colab Pro.

---

## 5. Key Design Decisions

Every significant choice is recorded here with the rationale, so the same ground is not re-covered during implementation.

| Decision | Choice Made | Alternatives Rejected | Rationale |
|---|---|---|---|
| **Container orchestration** | Docker Compose | Kubernetes (minikube, kind) | 16GB RAM constraint; K8s adds ~2GB overhead with no core value for a local demo |
| **Log aggregation** | Loki + Grafana | ELK Stack (Elasticsearch + Kibana) | ELK requires 4–6GB RAM alone; Loki is much lighter while still queryable via LogQL |
| **Message streaming** | Apache Kafka | Redis Streams, file-tailing | Learning goal; Kafka appears on FAANG job descriptions; enables realistic stream processing pattern |
| **LLM** | Gemini 1.5 Flash | Claude API, GPT-4 | Google AI Studio Tier 1 is cost-effective (free up to generous limits); Flash is fast enough for investigation |
| **Agent framework** | LangGraph | Raw LangChain, AutoGen, CrewAI | Explicit state machine gives full control over agent flow; prevents uncontrolled looping; easier to debug |
| **Vector database** | ChromaDB | Pinecone, Weaviate, Qdrant | Local deployment — zero API costs; sufficient for ~10 runbook documents |
| **Cross-system validation dataset** | RCAEval (RE1/RE2/RE3) | AIOps Challenge, GAIA Dataset | Purpose-built for microservice RCA; 736 labeled cases; MIT license; 5 published baselines for direct quantitative comparison; covers three real microservice systems |
| **Log pretraining dataset** | LogHub HDFS | Thunderbird (211M logs), BGL (4.7M logs), Spirit | Manageable size (~1GB uncompressed); block-level anomaly labels directly usable for supervised benchmarking; best-documented in LogHub collection |
| **Excluded datasets** | AIOps Challenge, GAIA | — | AIOps Challenge focuses on single-node KPI anomaly detection, not causal RCA across services; GAIA has fewer labeled cases; both create scope creep beyond what the timeline supports |
| **Fault injection method** | Scripted bash (docker stop, tc, env vars) | Chaos Mesh, Chaos Monkey | Chaos Mesh requires Kubernetes; scripted injection gives reproducible, deterministic faults with known ground truth |
| **Anomaly detection model** | LSTM-Autoencoder (unsupervised) | Supervised LSTM, Transformer-AE, VAE | Unsupervised fits the setting (normal data abundant, fault labels scarce); LSTM-AE is the standard in log anomaly detection literature; directly comparable to DeepLog/LogRobust baselines |
| **Causal discovery algorithm** | PC Algorithm (causal-learn) | Granger Causality, PCMCI, LiNGAM | PC is the canonical constraint-based algorithm; well-understood; available in `causal-learn`; produces interpretable DAGs; Granger is a stretch goal |
| **Removed components** | Kubernetes, ELK, Slack Bot, MLflow, check_deployments tool, find_similar_incidents tool | — | Per scope reduction: not core value, resource constraints, or timeline |

---

## 6. University Requirements Compliance Mapping

| Requirement | How OpsAgent Addresses It |
|---|---|
| **CRISP-DM methodology** | Full 6-phase implementation: Business Understanding → Data Understanding → Data Preparation → Modeling → Evaluation → Deployment |
| **DS&A Principles** | Unsupervised learning (LSTM-AE), causal inference (PC algorithm), multi-step agent reasoning (LangGraph), information retrieval (ChromaDB RAG) |
| **Data collection & visualization** | Self-generates data via fault injection; real-world data from RCAEval (736 cases) and LogHub (11M+ logs); 10+ visualization types in `notebooks/08` |
| **Task identification** | Anomaly detection, causal discovery, NLG (RCA report), information retrieval, classification (Recall@1/3) |
| **Solution design & implementation** | End-to-end system: streaming pipeline + ML models + agent orchestration + REST API + dashboard |
| **Analysis & evaluation** | Fault injection testing (40 cases), RCAEval cross-validation (736 cases), 3 internal baselines + 5 published baselines, statistical analysis with confidence intervals |
| **Model interpretability** | Agent produces evidence chains + causal DAG + counterfactual confidence scores in every RCA report |

---

## 7. Must-Have vs. Nice-to-Have vs. Out-of-Scope

### Must-Have (MVP — required for university submission and recruiter credibility)

**Infrastructure (6):** Docker Compose stack, OTel Demo (6 services), Prometheus, Grafana, Loki, Kafka

**Data & Datasets (6):** Drain3 parser, Feature engineering pipeline, TopologyGraph, ChromaDB/runbooks, RCAEval adapter (RE1/RE2/RE3), LogHub HDFS preprocessor

**ML/AI (8):** LSTM-AE pretraining on HDFS, LSTM-AE fine-tuning on OTel Demo, Isolation Forest baseline, PC algorithm, Counterfactual confidence, LangGraph agent, 5 agent tools, prompts

**Serving (2):** FastAPI (`POST /investigate`, `GET /health`, `GET /topology`), Streamlit dashboard

**Evaluation (4):** Fault injection suite (7 active types × 5 runs = 35 tests; `cpu_throttling` removed Session 12), RCAEval evaluation runner (736 cases), 3 internal baselines, metrics calculator

**Total MVP effort estimate: 185–235 hours**

### Nice-to-Have (add only if ahead of schedule)

| Priority | Component | Value Added |
|---|---|---|
| 1 | Ensemble scorer (LSTM-AE + Isolation Forest) | Better detection accuracy |
| 2 | LogHub HDFS F1 benchmark vs. DeepLog/LogRobust | Stronger anomaly detection positioning |
| 3 | Granger causality (complement to PC algorithm) | More robust causal analysis |
| 4 | Ablation studies (component contribution analysis) | Stronger evaluation narrative |
| 5 | Jaeger distributed tracing | Richer observability signal |
| 6 | RCAEval RE3 deep-dive analysis | Fine-grained code-level fault validation |

### Out-of-Scope (explicitly excluded)

| Component                     | Reason                                                  |
| ----------------------------- | ------------------------------------------------------- |
| Kubernetes                    | 16GB RAM constraint; adds complexity without core value |
| Elasticsearch + Kibana (ELK)  | Replaced by Loki; would consume 4–6GB RAM               |
| Slack Bot integration         | Not core value; per initial scope decision              |
| MLflow experiment tracking    | Manual tracking sufficient for timeline                 |
| `check_deployments` tool      | Not relevant without a CI/CD system                     |
| `find_similar_incidents` tool | Nice-to-have; cut for timeline                          |
| Chaos Mesh                    | Requires Kubernetes                                     |
| AIOps Challenge dataset       | Less aligned with causal RCA; scope creep               |
| GAIA dataset                  | Fewer labeled cases than RCAEval; scope creep           |

---

## 8. Risk Register & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **Docker resource exhaustion** (OOM, slow containers) | Medium | High | Monitor with `docker stats`; reduce OTel Demo to 4 services if needed; Loki and Grafana are lowest priority to cut |
| **Kafka learning curve** | Low | Medium | Use provided `LogConsumer` template from `context/data_pipeline_specs.md`; focus only on consuming, not broker management |
| **LSTM-AE poor performance without pretraining** | Low | Low | Pretraining is additive — `_load_compatible_weights()` handles dim mismatch; worst case, train from scratch on OTel Demo only |
| **Input dimension mismatch (HDFS vs OTel feature dims)** | High (expected) | Low | `_load_compatible_weights()` loads only shared LSTM layers; input/output layers are reinitialized. This is expected and handled. |
| **LogHub HDFS download failure** | Low | Low | Download during the 24h OTel baseline collection window; use `--loghub` flag independently |
| **RCAEval adapter column name mismatch** | Medium | Low | Test adapter against all three variants in Week 4; metric column normalization (`cpu` → `cpu_usage`, etc.) handles most differences |
| **RCAEval evaluation too slow** | Medium | Medium | RE1 (metrics-only, 375 cases) is fastest — run first; RE2/RE3 can run overnight; no live Docker stack needed |
| **OpsAgent underperforms published RCAEval baselines** | Medium | Low | This is a valid and interesting finding — document the gap and analyze why; the evaluation framework is complete regardless of outcome |
| **PC algorithm produces invalid/sparse graph** | Medium | Medium | Validate on synthetic causal data with known ground truth first (Task 4.9); fall back to correlation-based scoring if PC fails consistently |
| **Agent loops or exceeds tool call budget** | Low | Medium | Hard 10 tool call limit in `AgentExecutor`; comprehensive `try/except` on every tool; agent returns partial report on timeout |
| **LLM API costs spike** | Low | Medium | Gemini Flash pricing is very low; monitor daily via Google AI Studio console; cap max tokens per call |
| **Time overrun in evaluation** | Medium | Medium | Cut RCAEval RE3 (90 cases) if behind schedule — RE1 + RE2 (645 cases) are sufficient for cross-system validation claim |
| **Causal discovery runtime too slow** | Medium | Low | PC algorithm with `max_cond_set_size=3` is fast on 6–10 services; timeout of 30s in `discover_causation` tool |
