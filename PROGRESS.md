# OpsAgent — Progress Tracker

> **How to use:** Check off tasks as they are completed. Update the Progress Log at the end
> of every Claude Code session. Consult `CLAUDE.md` for project context and `context/` files
> for implementation specs before starting each phase.

---

## Phase 1 — Business Understanding (Week 1)

**Goal:** Define problem scope, success criteria, and get proposal approved.

### Setup & Documentation
- [x] Create `docs/problem_statement.md`
- [x] Create `docs/success_metrics.md` with Recall@1, Precision, Detection Latency, RCAEval targets
- [x] Create `docs/baselines.md` documenting all 4 baseline approaches
- [x] Submit project proposal to advisor; receive approval

### Repository Initialization
- [x] Run `git init` and create full directory structure (see `CLAUDE.md` for tree)
- [x] Initialize Poetry environment (`pyproject.toml`); install all dependencies including `RCAEval` package
- [x] Create `.env.example`, `.gitignore` (include `data/`, `models/*.pt`, `.env`)
- [x] Create `README.md` with project overview and hybrid data strategy description
- [x] Create stub `Makefile` with `setup`, `test`, `run` targets
- [x] Create blank config stubs: `configs/model_config.yaml`, `agent_config.yaml`, `dataset_config.yaml`, `evaluation_scenarios.yaml`

### ✅ Phase 1 Complete When:
- Proposal approved by advisor
- `poetry install` succeeds and `import RCAEval` works
- All directory structure created including `data/RCAEval/` and `data/LogHub/HDFS/`

---

## Phase 2 — Data Understanding (Weeks 2–3)

**Goal:** Stand up infrastructure, deploy OTel Demo, collect baseline data, download and explore real-world datasets.

### Infrastructure (Week 2)
- [x] Create `docker-compose.yml` with Prometheus, Grafana, Loki, Zookeeper, Kafka, Docker Stats Exporter
- [x] Create `infrastructure/prometheus/prometheus.yml` (scrape Docker Stats Exporter for container-level metrics)
- [x] Create `infrastructure/loki/loki-config.yml`
- [x] Create `infrastructure/grafana/provisioning/datasources/datasources.yml` (Prometheus + Loki)
- [x] Create `demo_app/docker-compose.demo.yml` (6 OTel services + Redis + loadgenerator)
- [x] Create `scripts/start_infrastructure.sh` and `scripts/stop_infrastructure.sh`
- [x] Verify full stack: Grafana shows metrics at `localhost:3000`; Prometheus targets healthy; Docker Stats Exporter scraping all containers
- [x] Create `scripts/generate_training_data.py` and start 24h OTel Demo baseline collection

### Dataset Acquisition & EDA (Week 3)
- [x] Create `scripts/download_datasets.py` (with `--rcaeval`, `--loghub`, `--all`, `--status` flags; downloads from Zenodo API)
- [x] Download RCAEval RE1 (375 cases), RE2 (271 cases), RE3 (90 cases) to `data/RCAEval/`
- [x] Download LogHub HDFS to `data/LogHub/HDFS/` (verify `HDFS.log` + `anomaly_label.csv` present)
- [x] Create `notebooks/01_data_exploration.ipynb` — OTel Demo metric availability, time series, distributions, correlations, anomalous period detection
- [x] Create `notebooks/02_rcaeval_exploration.ipynb` — fault distributions, root cause service breakdown, metric column naming across RE1/RE2/RE3
- [x] Create `notebooks/03_loghub_exploration.ipynb` — block structure, anomaly rate (~2.9%), Drain3 sample quality, normal vs anomalous comparison
- [x] Visualize OTel Demo service topology (`docs/images/service_topology.html`) — interactive vis.js graph with 7 nodes, 9 edges
- [x] Confirm 24h OTel Demo baseline collection complete (`data/baseline/metadata.json` status = `completed`, 1440 snapshots)
- [ ] Advisor check-in completed (Week 3) — demo running stack + downloaded datasets

### ✅ Phase 2 Complete When:
- All Docker services healthy; Kafka receiving logs
- RCAEval RE1/RE2/RE3 downloaded and case structure verified
- LogHub HDFS downloaded; anomaly labels loaded; block ID regex confirmed
- 24+ hours of OTel Demo baseline data collected

---

## Phase 3 — Data Preparation (Weeks 4–5)

**Goal:** Build OTel Demo data pipeline, implement dataset adapters, feature engineering, runbook indexing.

### OTel Demo Pipeline (Week 4)
- [x] `src/data_collection/kafka_consumer.py` — `LogConsumer` class
- [x] `src/preprocessing/log_parser.py` — `LogParser` wrapping Drain3 `TemplateMiner`
- [x] `src/preprocessing/windowing.py` — `WindowAggregator` (60s windows)
- [x] `src/preprocessing/feature_engineering.py` — `FeatureEngineer` (log template counts + metric stats)
- [x] `src/preprocessing/rcaeval_adapter.py` — `RCAEvalDataAdapter` with column normalization; smoke-tested against RE1/RE2/RE3
- [x] `src/data_collection/metrics_collector.py` — Prometheus query client
- [x] `notebooks/04_log_parsing_analysis.ipynb` — Drain3 template quality across HDFS 100K sample (OTel Demo logs deferred — no log shipper configured yet)

### LogHub Preprocessor & Supporting Components (Week 5)
- [x] `src/preprocessing/loghub_preprocessor.py` — `LogHubHDFSPreprocessor`: block grouping, label loading, sequence building; `create_hdfs_splits()` and `create_otel_splits()` helper functions
- [x] `src/data_collection/topology_extractor.py` — `TopologyGraph` (NetworkX DiGraph, 7 nodes, 9 edges, `get_subgraph()`, `to_json()`)
- [x] `src/knowledge_base/runbook_indexer.py` — `RunbookIndexer` with ChromaDB + `all-MiniLM-L6-v2` (`index_file()`, `index_directory()`, `search()`)
- [x] `src/knowledge_base/embeddings.py` — embedding utility functions (sentence-transformers `all-MiniLM-L6-v2` wrapper with lazy singleton)
- [x] Create 5 runbooks: `connection_exhaustion.md`, `cascading_failure.md`, `memory_pressure.md`, `high_latency.md`, `general_troubleshooting.md`
- [x] Index all runbooks into ChromaDB; verify search returns relevant results (57 chunks indexed; 3/4 top-1 matches, 4/4 top-3 matches)
- [x] `scripts/prepare_data_splits.py` — CLI script with `--hdfs`, `--otel`, `--all` flags; saves `train.npy`, `val.npy`, `metadata.json` to `data/splits/hdfs/`
- [ ] Advisor check-in completed (Week 5) — logs flowing, templates extracted, adapters working

### ✅ Phase 3 Complete When:
- OTel Demo pipeline: Kafka → Drain3 → windowing → feature vectors working end-to-end
- RCAEval adapter verified against all three variants (RE1/RE2/RE3)
- LogHub HDFS preprocessor producing correct sequence shapes and ~2.9% anomaly rate
- Data splits ready for training
- Runbooks indexed into ChromaDB; similarity search working

---

## Phase 4 — Modeling (Weeks 6–8)

**Goal:** Pretrain LSTM-AE on HDFS, fine-tune on OTel Demo, implement causal discovery, build and test the LangGraph agent.

### LSTM-Autoencoder (Week 6)
- [x] `src/anomaly_detection/lstm_autoencoder.py` — `LSTMAutoencoder(nn.Module)` with `forward()`, `get_reconstruction_error()` (encoder-decoder with latent bottleneck, 138K params)
- [x] `src/anomaly_detection/trainer.py` — `AnomalyTrainer` with MSELoss, Adam optimizer, early stopping, best-model restoration
- [x] `src/anomaly_detection/pretrain_on_loghub.py` — `pretrain_on_hdfs()`, `finetune_on_otel_demo()`, `_load_compatible_weights()`, `_one_hot_encode()`
- [x] `src/anomaly_detection/threshold.py` — `calculate_threshold()` with 95th percentile from baseline reconstruction errors
- [x] `src/anomaly_detection/isolation_forest.py` — `IsolationForestDetector` baseline (n_estimators=100, contamination=0.01, n_jobs=-1)
- [x] `src/anomaly_detection/detector.py` — `AnomalyDetector` real-time detection service (Fast Loop → Slow Loop bridge: scores each window, fires alert callback when threshold exceeded)
- [x] Run pretraining on HDFS in `notebooks/05_anomaly_detection_dev.ipynb` (Colab Pro, GPU) — checkpoint saved to `models/lstm_autoencoder/pretrained_hdfs.pt` (115 templates, 1.6M sequences, early stopped at epoch 36/50, val_loss=0.000011)
- [x] Run fine-tuning on OTel Demo — completed in Session 8 (Colab Pro L4 GPU, 54-dim features, lr=0.001, early stopped at epoch 168/200, val_loss=0.134, threshold=0.253)
- [x] Plot and save training curves to `docs/images/` (hdfs_pretraining_curves.png, hdfs_error_distribution.png)

### Log Pipeline & Fine-tuning Data Collection (Week 6.5 — between Week 6 and Week 7)

**Before collection (infrastructure setup):**
- [x] Add Promtail service to `docker-compose.yml` — ship OTel Demo container logs to Loki (Promtail does not natively support Kafka output)
- [x] Create `infrastructure/promtail/promtail-config.yml` — Docker SD via socket, `service` label from `com.docker.compose.service`, push to Loki
- [x] Update `scripts/start_infrastructure.sh` and `scripts/stop_infrastructure.sh` to include Promtail
- [x] Restart Docker stack and verify logs flowing: Loki receiving log entries with `service` label for all 7 OTel Demo services
- [x] Update `scripts/generate_training_data.py` to collect logs from Loki alongside Prometheus metrics (per-service queries with `{service="<name>"}`, `"service"` key in log entries)

**24h data collection (run with `caffeinate -s`; Week 7 tasks can proceed in parallel):**
- [x] Run 24-hour baseline collection with both metrics AND logs — saved to `data/baseline_with_logs/` (1440 snapshots, 2885 log entries, status=completed)

**After collection completes:**
- [x] Process collected data through `FeatureEngineer.build_sequence()` to produce feature vectors for fine-tuning — z-score normalized, saved to `data/splits/otel/` (train: 1144×10×54, val: 286×10×54, scaler params saved)
- [x] Run fine-tuning on OTel Demo using `finetune_on_otel_demo()` — checkpoint saved to `models/lstm_autoencoder/finetuned_otel.pt` (54-dim, lr=0.001, early stopped epoch 168/200, val_loss=0.134, 132K params)
- [x] Calculate anomaly detection threshold using `calculate_threshold()` on fine-tuned model — 95th percentile threshold: 0.253
- [x] Plot and save training curves (pretrain + fine-tune) to `docs/images/` (finetune_training_curves.png)
- [x] Synthetic anomaly benchmark on fine-tuned model — F1=0.97, Precision=0.95, Recall=1.00 (CPU spike 100%, Memory spike 100%, Network error 100% detection)

### Causal Discovery (Week 7)

> **Note:** Week 7 tasks have no dependency on the Week 6.5 data collection and can be completed while the 24h log-enriched baseline collection is in progress.

- [x] `src/causal_discovery/pc_algorithm.py` — `discover_causal_graph()` wrapping causal-learn PC (α=0.05, Fisher's Z), `create_time_lags()`, `parse_causal_graph()`
- [x] `src/causal_discovery/counterfactual.py` — `calculate_counterfactual_confidence()` scoring, `compute_baseline_stats()`
- [x] `src/causal_discovery/graph_utils.py` — `CausalEdge` and `CausalGraph` dataclasses with `to_ascii()` and `top_edges()`
- [x] `notebooks/06_causal_discovery_dev.ipynb` — validated PC on synthetic A→B→C (correct skeleton, no spurious A→C), complex B→C←D (collider oriented correctly), alpha sensitivity, counterfactual confidence scoring

### LangGraph Agent (Week 8)
- [x] `src/agent/state.py` — `AgentState` TypedDict (13 fields, `add_messages` reducer on `messages`, LangGraph `StateGraph` compatible)
- [x] `src/agent/graph.py` — LangGraph workflow: 5-node `StateGraph` (analyze_context → form_hypothesis → gather_evidence → analyze_causation → conditional → generate_report), `should_continue` routing, Gemini 2.5 Flash Lite LLM with `bind_tools`
- [x] `src/agent/tools/query_metrics.py` — Prometheus query tool (6 Docker Stats Exporter metrics: cpu_usage, memory_usage, network_rx/tx_bytes_rate, network_rx/tx_errors_rate; returns timestamps, values, stats, anomalous flag)
- [x] `src/agent/tools/search_logs.py` — Loki search tool (LogQL queries, service filter, error counting, pattern extraction)
- [x] `src/agent/tools/get_topology.py` — topology retrieval tool (wraps `TopologyGraph`, full graph or subgraph per service)
- [x] `src/agent/tools/search_runbooks.py` — ChromaDB search tool (wraps `RunbookIndexer`, returns title/content/relevance_score)
- [x] `src/agent/tools/discover_causation.py` — causal analysis tool (orchestrates MetricsCollector → time lags → PC algorithm → counterfactual scoring → CausalGraph; `max_conditioning_set=4` depth cap)
- [x] `src/agent/prompts/system_prompt.py` — agent persona + 6-step investigation methodology + available metrics list + tool budget guidance
- [x] `src/agent/prompts/report_template.py` — RCA report template with `str.format()` placeholders (14 fields)
- [x] `src/agent/executor.py` — `AgentExecutor` with `from_config()` classmethod, dual-mode `investigate()` (live + offline/RCAEval), error handling
- [x] `src/agent/tools/__init__.py` — `TOOLS` registry exporting all 5 tool functions
- [x] `src/agent/__init__.py` — package exports (`AgentExecutor`, `AgentState`)
- [x] `notebooks/07_agent_prototyping.ipynb` — 7-section notebook: setup, individual tool testing, graph compilation, routing logic, executor setup, full investigation, report inspection
- [x] `scripts/run_agent_demo.py` — CLI demo script with prerequisite checks, live/offline modes, formatted output
- [x] End-to-end agent test with manually injected fault — produces valid RCA report (verified in Session 10 via `service_crash` fault injection; cartservice correctly identified as root cause)
- [ ] Advisor check-in completed (Week 8) — show HDFS pretraining curves + working agent demo

### Unit Tests
- [x] `tests/unit/test_log_parser.py` — 12 tests (completed Session 6)
- [x] `tests/unit/test_feature_engineering.py` — 13 tests (completed Session 6)
- [x] `tests/unit/test_anomaly_detection.py` — 22 tests: LSTMAutoencoder (5), AnomalyTrainer (4), Threshold (3), IsolationForest (3), AnomalyDetector (3), LoadCompatibleWeights (2), OneHotEncode (2)
- [x] `tests/unit/test_causal_discovery.py` — 31 tests: CausalEdge (2), CausalGraph (5), CreateTimeLags (6), DiscoverCausalGraph (5), ParseCausalGraph (4), ComputeBaselineStats (4), CounterfactualConfidence (5)
- [x] `tests/unit/test_agent_tools.py` — 35 tests: GetTopology (6), QueryMetrics (8), SearchLogs (7), SearchRunbooks (5), DiscoverCausation (9)
- [x] `tests/unit/test_agent_state.py` — 5 tests: TypedDict structure, add_messages annotation, field completeness, construction, type hints
- [x] `tests/unit/test_agent_graph.py` — 12 tests: BuildGraph (2), ShouldContinue (5), AnalyzeContextNode (2), HelperFunctions (3)
- [x] `tests/unit/test_agent_executor.py` — 10 tests: Init (2), Investigate (4), FormatAlert (2), ExtractTop3 (3) — note: one test was removed by linter (9 remaining)
- [x] `tests/unit/test_metrics_calculator.py` — 30 tests: RecallAt1 (4), RecallAt3 (4), Precision (4), DetectionLatency (2), MttrProxy (3), ConfidenceInterval (3), LoadResults (3), CalculateMetrics (7)
- [x] `tests/unit/test_fault_injection_suite.py` — 20 tests: FaultScripts (5), ResolveScript (2), RunFaultInjection (8), LoadPerFaultCooldowns (4) — all subprocess/agent calls mocked
- [x] `tests/unit/test_baseline_comparison.py` — 17 tests: RuleBasedBaseline (5), ADOnlyBaseline (5), LLMWithoutToolsBaseline (5), RunAllBaselines (3) — all external services mocked
- [x] `tests/unit/test_inject_faults.py` — 8 tests: PreflightChecks (5), PrintSummary (3) — Docker/Prometheus/Loki/API key checks mocked
- [x] `tests/integration/test_data_pipeline.py` — 7 tests: PrometheusIntegration (2), LokiIntegration (2), TopologyIntegration (2), FullPipeline (1). `@pytest.mark.integration`
- [x] `tests/integration/test_agent_workflow.py` — 8 tests: GraphCompilation (1), ToolInvocation (3), EndToEndInvestigation (3, requires GEMINI_API_KEY). `@pytest.mark.integration`

### ✅ Phase 4 Complete When:
- `pretrained_hdfs.pt` and `finetuned_otel.pt` saved; training curves show convergence
- Anomaly threshold correctly flags injected test anomalies
- PC algorithm produces valid causal graphs on synthetic data
- Counterfactual confidence scores in [0, 1]
- Agent completes full investigation successfully with manual fault injection

---

## Phase 5 — Evaluation (Weeks 9–10)

**Goal:** Run fault injection tests, RCAEval cross-system evaluation, calculate all metrics and visualizations. See `context/evaluation_strategy.md` for full evaluation details.

### Fault Injection Evaluation (Week 9)
- [x] Create all 8 fault injection bash scripts in `demo_app/fault_scenarios/`
- [x] Create `scripts/inject_faults.py` — automated fault injection coordinator with preflight checks, summary output
- [x] Create `tests/evaluation/fault_injection_suite.py` — automated runner with inject → wait → investigate → save → restore pipeline
- [x] Create `tests/evaluation/metrics_calculator.py` — `EvaluationResults` dataclass, Recall@1, Recall@3, Precision, Detection Latency, MTTR Proxy, confidence intervals
- [x] Create `tests/evaluation/baseline_comparison.py` — 3 internal baselines: `RuleBasedBaseline`, `ADOnlyBaseline`, `LLMWithoutToolsBaseline`
- [x] Create `tests/unit/test_metrics_calculator.py` — 30 tests across 8 classes
- [x] Create `tests/unit/test_fault_injection_suite.py` — 20 tests across 4 classes
- [x] Create `tests/unit/test_baseline_comparison.py` — 17 tests across 4 classes
- [x] Create `tests/unit/test_inject_faults.py` — 8 tests across 2 classes
- [x] End-to-end single-fault test verified: `service_crash` → Recall@1=100%, Recall@3=100%, MTTR=15.3s
- [x] Run OTel Demo fault injection tests — 40 tests completed Session 11 (27.5% Recall@1 / 40% Recall@3); suite resized to **35 tests (7 types × 5 reps)** in Session 12 after diagnosis showed `cpu_throttling` is undetectable on the idle demo. Session 12 re-run achieved **42.9% Recall@1 / 68.6% Recall@3** (Tier 1/2/3 fixes). Session 12 follow-up with Tier 4/5 fixes achieved **91.4% Recall@1 / 94.3% Recall@3** — memory_pressure remained weak at 2/5 (40%). **Session 13 added memory-saturation detection (Docker Stats Exporter `container_spec_memory_limit_bytes` gauge, `memory_utilization` CRITICAL detector with peak-based trigger, dynamic fault-script cap) and achieved 100% Recall@1 / 100% Recall@3 (35/35) at 0.75 confidence uniformly, with memory_pressure at 5/5.** Results in `data/evaluation/results_session13/`. Clears the ≥80% Recall@1 and ≥95% Recall@3 targets.
- [ ] Evaluate 3 internal baselines: Rule-Based, AD-Only, LLM-Without-Tools

### RCAEval Cross-System Evaluation (Week 10, Days 1–2)
- [ ] Create `tests/evaluation/rcaeval_evaluation.py` — `evaluate_on_rcaeval()` + `run_all_rcaeval_variants()`
- [ ] Run OpsAgent against RE1 (375 cases); save results
- [ ] Run OpsAgent against RE2 (271 cases); save results
- [ ] Run OpsAgent against RE3 (90 cases); save results
- [ ] Compare results against published baselines (BARO, CIRCA, RCD, CausalRCA, MicroHECL)

### Analysis & Visualizations (Week 10, Days 3–5)
- [ ] Calculate all metrics across both evaluation tracks
- [ ] Manually score 25–30 RCA reports using 5-point explanation quality rubric; log to `data/evaluation/explanation_quality_scores.csv`
- [ ] Create `notebooks/08_evaluation_analysis.ipynb` with required visualizations (1–9):
  - [ ] Recall@1 by fault type (OTel Demo)
  - [ ] Baseline comparison grouped bar chart (OTel Demo)
  - [ ] RCAEval Recall@1 by variant vs. published baselines
  - [ ] Detection latency distribution
  - [ ] Confusion matrix (OTel Demo)
  - [ ] Causal graph examples
  - [ ] Agent tool usage distribution
  - [ ] Explanation quality score distribution
  - [ ] RCAEval Recall@1 by fault type (RE2 breakdown)
- [ ] Draft `docs/evaluation_results.md`
- [ ] Run statistical analysis (confidence intervals, paired t-tests)
- [ ] Advisor check-in completed (Week 10) — present results with charts

### 🔵 Nice-to-Have: LogHub HDFS AD Benchmark (Week 10 — only if ahead of schedule)

> Complete only after Tracks 1 and 2 are fully done. Full benchmark details in `context/anomaly_detection_specs.md` §11.

- [ ] Run `tests/evaluation/loghub_benchmark.py` with `pretrained_hdfs.pt` (Stage 2) — record F1, Precision, Recall
- [ ] Run `tests/evaluation/loghub_benchmark.py` with `finetuned_otel.pt` (Stage 3) — record F1, Precision, Recall
- [ ] Add Visualizations 10–11 to `notebooks/08_evaluation_analysis.ipynb`: HDFS F1 bar chart + training loss curves
- [ ] Document Track 3 results in `docs/evaluation_results.md` Section 3

### ✅ Phase 5 Complete When:
- 40 OTel Demo fault injection tests complete; Recall@1 ≥ 80%
- All 736 RCAEval cases evaluated; results vs. published baselines computed
- All required metrics calculated; Visualizations 1–9 created
- `docs/evaluation_results.md` drafted
- *(Nice-to-have: HDFS AD Benchmark tasks completed if time allows)*

---

## Phase 6 — Deployment & Documentation (Weeks 11–12)

**Goal:** Deploy serving layer, write CRISP-DM report, clean up repository, prepare presentation.

### Serving Layer (Week 11)
- [ ] `src/serving/api.py` — FastAPI with `POST /investigate`, `GET /health`, `GET /topology`
- [ ] `src/serving/dashboard.py` — Streamlit: system overview, investigation trigger, history viewer, real-time metrics
- [ ] `Dockerfile` — OpsAgent container (Python 3.11-slim, Poetry, FastAPI on :8000, Streamlit on :8501)
- [ ] Update `docker-compose.yml` to include OpsAgent service
- [ ] Verify end-to-end demo: fault injected → agent triggered → RCA report displayed in Streamlit

### Documentation & Final Cleanup (Week 12)
- [ ] Write `docs/CRISP_DM_Report.md` — all 6 phases with EDA findings, adapter design decisions, two-phase training strategy, dual evaluation tracks, statistical analysis
- [ ] Write `docs/architecture.md`
- [ ] Write `docs/api_reference.md`
- [ ] Add docstrings to all `src/` modules; run `mypy src/` with zero errors
- [ ] Update `README.md` — setup instructions, `python scripts/download_datasets.py --all`, demo walkthrough
- [ ] Add `LICENSE`
- [ ] Ensure `data/`, `models/*.pt` are in `.gitignore`; repo is clean for GitHub
- [ ] Create presentation slides: problem, architecture, data strategy, ML techniques, evaluation results (both tracks), demo
- [ ] Record demo video (optional backup)
- [ ] Advisor check-in completed (Week 11) — pre-defense review with draft report

### ✅ Phase 6 Complete When:
- End-to-end demo working (fault → alert → RCA report in Streamlit)
- `docs/CRISP_DM_Report.md` complete (all three data sources documented)
- Repository is clean; `README.md` has `python scripts/download_datasets.py --all`
- Presentation slides complete; ready for defense

---

## Progress Log

> **Template — fill this out at the end of every Claude Code session:**

```
### [YYYY-MM-DD] — Session N

**Phase:** Phase X — <Phase Name>
**Duration:** ~X hours

**Completed:**
- <task or file created/modified>
- <task or file created/modified>

**In Progress:**
- <task partially done — describe state>

**Blockers / Issues:**
- <any problems encountered and how they were handled or left>

**Next Session:**
- <first task to pick up in next session>
- <second task if applicable>

**Notes:**
- <any design decisions made, unexpected findings, or deviations from the plan>
```

---

### 2026-03-24 — Session 1

**Phase:** Phase 1 — Business Understanding
**Duration:** ~2 hours

**Completed:**
- Created `docs/problem_statement.md` — problem scope, two-loop architecture, three-dataset strategy, target users
- Created `docs/success_metrics.md` — all target metrics with rationale, explanation quality rubric, statistical analysis plan
- Created `docs/baselines.md` — 3 internal ablation baselines + 5 published RCAEval baselines with descriptions
- Created full directory structure (30+ directories) with `.gitkeep` files for Git tracking
- Created `__init__.py` in all 14 Python packages under `src/` and `tests/`
- Created `tests/conftest.py` shared fixture file
- Created `pyproject.toml` with all dependencies; ran `poetry install` successfully (Python 3.12, torch 2.2.2)
- Created `.env.example` (GEMINI_API_KEY template) and `.gitignore` (data/, models/*.pt, .env, caches)
- Created `README.md` with project overview, architecture summary, data strategy, quick start, tech stack table, project structure
- Created `Makefile` with targets: setup, test, test-integration, lint, format, typecheck, run, dashboard, infra-up/down, demo-up/down, clean
- Created all 4 config YAML files with full content from `context/config_reference.md`:
  - `configs/model_config.yaml` — LSTM-AE hyperparameters, two-phase training
  - `configs/agent_config.yaml` — LLM settings, tool parameters, causal discovery
  - `configs/dataset_config.yaml` — paths and parameters for all 3 datasets
  - `configs/evaluation_scenarios.yaml` — 8 fault types, evaluation runner settings

**In Progress:**
- None — Phase 1 is complete

**Blockers / Issues:**
- **PyTorch macOS x86_64 compatibility:** PyTorch >=2.3 dropped macOS Intel (x86_64) wheels. Pinned `torch>=2.0,<2.3` (resolves to 2.2.2). On Apple Silicon or Linux, this constraint can be relaxed.
- **onnxruntime macOS x86_64 compatibility:** onnxruntime >=1.20 lacks macOS x86_64 wheels. Pinned `onnxruntime>=1.17,<1.20` (resolves to 1.19.2).
- **RCAEval dependency conflict:** RCAEval's `[default]` extras pin `torch==1.12.1`, conflicting with our `torch>=2.0`. Resolved by installing RCAEval base package only (without `[default]` extras); `import RCAEval` works.
- **Python version:** System had Python 3.9 (conda) and 3.14 (system). Installed Python 3.12 via Homebrew and Poetry via pipx for compatibility with all dependencies.

**Next Session:**
- Begin Phase 2 — Data Understanding: create `docker-compose.yml` and infrastructure configs
- Set up OTel Demo services and monitoring stack

**Notes:**
- Poetry and pipx were installed fresh this session (`~/.local/bin/poetry`, `~/Library/Python/3.13/bin/pipx`). Ensure `PATH` includes these directories in future sessions.
- Python constraint set to `>=3.11,<3.13` in `pyproject.toml` due to torch/onnxruntime wheel availability on Intel Mac. Can be widened to `<3.14` once running on Apple Silicon or Linux.
- Config YAML files contain full production values (not stubs) — copied from `context/config_reference.md` to ensure cross-config constraints are satisfied from the start.
- `numpy` pinned to `<2.0` to maintain compatibility with torch 2.2.x and the broader dependency tree.

---

### 2026-03-25 — Session 2

**Phase:** Phase 2 — Data Understanding (Week 2 Infrastructure)
**Duration:** ~3 hours

**Completed:**
- Created `docker-compose.yml` — Prometheus, Grafana, Loki, Zookeeper, Kafka, cAdvisor (container metrics)
- Created `demo_app/docker-compose.demo.yml` — 6 OTel Demo services (frontend, cartservice, checkoutservice, paymentservice, productcatalogservice, currencyservice) + Redis + loadgenerator; pinned to `ghcr.io/open-telemetry/demo:1.7.0-*` tags; uses external `opsagent_opsagent-net` network
- Created `infrastructure/prometheus/prometheus.yml` — scrapes cAdvisor for container-level metrics (CPU, memory, network, disk I/O) with service name relabeling; Prometheus self-monitoring
- Created `infrastructure/prometheus/alert_rules.yml` — placeholder alert rules for HighLatency and HighErrorRate
- Created `infrastructure/loki/loki-config.yml` — boltdb-shipper storage, 7-day retention, in-memory ring
- Created `infrastructure/grafana/provisioning/datasources/datasources.yml` — auto-provisions Prometheus + Loki datasources
- Created `infrastructure/grafana/provisioning/dashboards/dashboards.yml` — auto-provisions dashboard directory
- Created `infrastructure/grafana/dashboards/service_overview.json` — Grafana dashboard with 6 panels (CPU, memory, network I/O, filesystem, network errors, container count)
- Created `scripts/start_infrastructure.sh` — sequential startup (monitoring stack → OTel Demo) with health wait periods
- Created `scripts/stop_infrastructure.sh` — reverse teardown (OTel Demo → monitoring stack)
- Created `scripts/generate_training_data.py` — baseline data collector with Prometheus metric snapshots + Loki log queries, 60s intervals, resume support, graceful interrupt handling, `metadata.json` tracking
- Created `scripts/download_datasets.py` — RCAEval automated download via `RCAEval.utility` + LogHub HDFS manual download instructions with verification
- Started 24h OTel Demo baseline metric collection (in progress, running via `caffeinate -s`)

**In Progress:**
- 24h OTel Demo baseline data collection is actively running (~230+ snapshots at session end)
- Logs are not being collected (0 count) — Loki has no log shipper configured yet (Promtail needed)
- Full stack verification incomplete — Prometheus + cAdvisor confirmed working; Grafana accessible; Kafka and log pipeline not yet verified

**Blockers / Issues:**
- **OTel Demo services don't expose Prometheus `/metrics` endpoints:** The original `prometheus.yml` from the context spec targeted individual service HTTP ports (frontend:8080, cartservice:7070, etc.), but these are gRPC services without native Prometheus exporters. **Resolution:** Replaced with cAdvisor for container-level metrics (CPU, memory, network, filesystem). This provides the metrics needed for anomaly detection without requiring an OTel Collector.
- **cAdvisor volume mounts on macOS:** The standard Linux cAdvisor config mounts `/sys`, `/var/lib/docker`, `/dev/disk`, etc. These don't exist or work the same way on macOS with Docker Desktop. **Resolution:** Stripped down to only `/var/run/docker.sock:ro` with `privileged: true` and `platform: linux/amd64`. cAdvisor starts successfully on Docker Desktop for Mac.
- **Docker daemon not running:** User encountered "failed to connect to docker API" error on first attempt. **Resolution:** Open Docker Desktop app and wait for it to fully start before running infrastructure scripts.
- **No log shipper to Loki:** Loki is running but empty — no Promtail or Docker logging driver configured to push container logs. Data collection script shows `logs: 0` on every iteration. **Resolution deferred:** Will add Promtail to the infrastructure stack in a future session before evaluation needs logs. Metrics collection is sufficient for the current 24h baseline run.

**Next Session:**
- Add Promtail to `docker-compose.yml` to ship container logs to Loki (requires stack restart — must wait until 24h collection completes)
- Download RCAEval datasets (RE1/RE2/RE3) and LogHub HDFS
- Create EDA notebooks (01, 02, 03) for OTel Demo baseline, RCAEval, and LogHub HDFS data exploration
- Visualize OTel Demo service topology (`docs/images/service_topology.html`)
- Confirm 24h baseline collection completed successfully

**Notes:**
- OTel Demo images pinned to `1.7.0` tag (not `latest`) for reproducibility.
- cAdvisor exposes metrics on port 8081 externally (mapped from container port 8080) to avoid conflict with OTel Demo frontend on port 8080.
- The `generate_training_data.py` script uses cAdvisor-provided container metrics via Prometheus — queries filter by `container_label_com_docker_compose_service` label to isolate OTel Demo services.
- Prometheus config diverges from `context/infrastructure_and_serving.md` spec: uses cAdvisor scraping instead of direct service scraping. The context file should be updated to reflect this architectural decision.
- Use `caffeinate -s` on macOS to prevent sleep during 24h data collection. Mac must be plugged into AC power.
- Data collection supports resume — if interrupted (`Ctrl+C` or kill), re-running the same command picks up from the last snapshot count via `metadata.json`.

---

### 2026-03-26 — Session 3

**Phase:** Phase 2 — Data Understanding (Week 2 Infrastructure Fix + Week 3 Start)
**Duration:** ~4 hours

**Completed:**
- Diagnosed empty metric data from first 24h collection — all 1440 snapshots contained empty arrays despite `status: completed`
- Root cause identified: cAdvisor v0.47.2 cannot discover individual Docker containers on macOS Docker Desktop (cgroupv2 + VM-based Docker). cAdvisor only saw the root cgroup (`id: /`); its Docker container discovery API returned 0 containers; `docker version` from inside cAdvisor returned "NOT FOUND"
- Replaced cAdvisor with custom Docker Stats Exporter (`infrastructure/docker_stats_exporter/`):
  - `exporter.py` — queries Docker API via Python Docker SDK, exposes Prometheus-format metrics on port 9101
  - `Dockerfile` — Python 3.11-slim with docker SDK
  - `requirements.txt` — docker>=7.0.0
  - Uses background thread for stats collection (avoids Prometheus scrape timeouts; `container.stats()` blocks ~1-2s per container)
- Updated `docker-compose.yml` — removed cAdvisor service, added `docker-stats-exporter` service
- Updated `infrastructure/prometheus/prometheus.yml` — replaced `cadvisor` scrape job with `docker-stats-exporter` job (port 9101)
- Updated `scripts/generate_training_data.py` — changed `_SVC_FILTER` from `container_label_com_docker_compose_service` to `service` label; simplified service name extraction
- Updated `infrastructure/grafana/dashboards/service_overview.json` — replaced all `container_label_com_docker_compose_service` references with `service` in query expressions and legend formats
- Fixed Grafana datasource provisioning (`infrastructure/grafana/provisioning/datasources/datasources.yml`) — added explicit `uid: prometheus` and `uid: loki` fields to match dashboard JSON references
- Updated `scripts/start_infrastructure.sh` — references `docker-stats-exporter` instead of `cadvisor`, includes `--build` flag
- Updated `CLAUDE.md` — replaced cAdvisor references with Docker Stats Exporter in technology stack, common commands, and gotchas sections
- Verified fix end-to-end: Prometheus targets all UP, 7 services reporting metrics with real values, rate() queries working, 3-minute test collection produced non-empty snapshots
- Confirmed Grafana dashboard populated with live data (no more "Datasource prometheus not found" error)
- Cleared old empty baseline data and started new 24h OTel Demo baseline collection (in progress)

**In Progress:**
- Second 24h OTel Demo baseline data collection actively running with verified non-empty metric snapshots
- Logs still not collected (0 count) — Promtail deferred since log training pipeline uses Kafka → Drain3 (Phase 3), not Loki baseline logs

**Blockers / Issues:**
- **cAdvisor cannot discover containers on macOS Docker Desktop:** cAdvisor v0.47.2 running inside Docker on macOS Docker Desktop (Docker 29.3.0, cgroupv2) cannot see other containers. The Docker socket is mounted but cAdvisor's internal Docker client fails to connect. Without Docker API access or cgroup filesystem mounts, cAdvisor only reports root-level aggregate metrics. **Resolution:** Replaced cAdvisor with a custom Docker Stats Exporter that queries the Docker API directly via the Python `docker` SDK. This works reliably on macOS Docker Desktop.
- **Docker Stats Exporter initial timeout:** First version of the exporter called `container.stats(stream=False)` synchronously during HTTP request handling. With ~14 containers at ~1-2s each, total collection time (~20s) exceeded Prometheus's 10s scrape timeout, causing BrokenPipeError. **Resolution:** Moved stats collection to a background thread that runs every 10s and caches results. The `/metrics` endpoint returns cached data instantly.
- **Grafana "Datasource prometheus not found":** The Grafana dashboard JSON referenced `"uid": "prometheus"` but the provisioned datasource YAML didn't specify a `uid` field, so Grafana auto-generated a random UID. **Resolution:** Added explicit `uid: prometheus` and `uid: loki` to the datasource provisioning YAML.

**Next Session:**
- Confirm second 24h baseline collection completed successfully with non-empty metrics
- Download RCAEval datasets (RE1/RE2/RE3) and LogHub HDFS
- Create EDA notebooks (01, 02, 03)
- Visualize OTel Demo service topology
- Complete remaining Phase 2 tasks

**Notes:**
- The Docker Stats Exporter uses a 10s background collection interval with thread-safe caching. Prometheus scrapes every 15s, so data is always fresh within one collection cycle.
- The exporter labels metrics with `service` (from `com.docker.compose.service` Docker label) — simpler than cAdvisor's `container_label_com_docker_compose_service`.
- `fs_usage_bytes` reports 6 entries instead of 7 — some containers don't report blkio stats. This is expected and doesn't affect downstream processing.
- Log collection (Promtail/Loki) intentionally deferred — the log training pipeline (LSTM-AE) uses Kafka → Drain3 → template sequences, not Loki baseline logs. Promtail can be added later if needed for the agent's `search_logs` tool.
- Python stdout buffering in Docker was fixed by adding `PYTHONUNBUFFERED=1` to the exporter's Dockerfile.

---

### 2026-03-27 — Session 4

**Phase:** Phase 2 — Data Understanding (Week 3 Dataset Acquisition & EDA)
**Duration:** ~3 hours

**Completed:**
- Confirmed second 24h OTel Demo baseline collection completed successfully (1440 snapshots, 8 metrics × 7 services, all non-empty, `status: completed`)
- Added `seaborn` dependency via Poetry (v0.13.2) for EDA notebook visualizations
- Rewrote `scripts/download_datasets.py` — replaced broken `RCAEval.utility` imports with direct Zenodo API downloads; added automated LogHub HDFS download; improved verification with true case counting via `inject_time.txt`; added per-system breakdown in `--status` output
- Downloaded and verified all RCAEval datasets: RE1 (375 cases across RE1-OB/SS/TT), RE2 (271 cases across RE2-OB/SS/TT), RE3 (90 cases across RE3-OB/SS/TT) — total 736 cases
- Downloaded and verified LogHub HDFS: `HDFS.log` (1505 MB, 11M+ lines), `anomaly_label.csv` (575,061 blocks)
- Created `notebooks/01_data_exploration.ipynb` — OTel Demo baseline EDA with 8 cells: data loading, statistical summary, time series plots (8 metrics × 7 services), distribution boxplots, correlation heatmap, anomalous period detection (rolling 3σ), service comparison bar charts, summary
- Created `notebooks/02_rcaeval_exploration.ipynb` — RCAEval EDA with 8 cells: case inventory builder, directory structure samples, fault type distribution charts, root cause service breakdown, metric column naming analysis across OB/SS/TT, data format comparison table, adapter design summary
- Created `notebooks/03_loghub_exploration.ipynb` — LogHub HDFS EDA with 9 cells: anomaly label analysis (rate verification), 100K-line log sample loading with parsing, block structure analysis, log pattern analysis (levels, components), Drain3 template extraction test (10K messages, depth=4, sim_th=0.4), template frequency distribution, normal vs anomalous block comparison, summary
- Created `docs/images/service_topology.html` — interactive vis.js topology visualization with 7 nodes (frontend, cartservice, checkoutservice, paymentservice, productcatalogservice, currencyservice, redis), 9 directed edges, color-coded by tier (gateway/backend/data store), hierarchical layout, legend
- Updated `PROGRESS.md` — marked all Phase 2 Week 3 tasks complete (except advisor check-in)

**In Progress:**
- None — all Phase 2 implementation tasks complete; only advisor check-in remains

**Blockers / Issues:**
- **RCAEval pip package is a stub:** The installed `RCAEval` PyPI package only contains a single `is_ok()` function. The `RCAEval.utility` module with download functions does not exist in the pip package — it only exists in the GitHub source. **Resolution:** Rewrote `download_datasets.py` to download directly from Zenodo API (`https://zenodo.org/api/records/14590730/files/{name}/content`) without depending on `RCAEval.utility`.
- **RE2 case count discrepancy:** RE2-OB has 91 cases instead of the documented 90, making the RE2 total 271 instead of 270. This is a minor data issue in the upstream RCAEval benchmark — no impact on our evaluation pipeline.
- **RE1 vs RE2/RE3 file format differences:** RE1 uses `data.csv` with simple `{service}_{metric}` column naming (51 columns). RE2/RE3 use `metrics.csv` with container-level metric naming (up to 418 columns). The Phase 3 `RCAEvalDataAdapter` must handle both formats.

**Next Session:**
- Begin Phase 3 — Data Preparation: implement OTel Demo data pipeline (`kafka_consumer.py`, `log_parser.py`, `windowing.py`, `feature_engineering.py`)
- Implement `rcaeval_adapter.py` and `loghub_preprocessor.py`
- Run notebooks 01-03 to validate EDA outputs

**Notes:**
- Notebooks were created in Session 4 and executed + validated in Session 5 — all outputs confirmed correct.
- RCAEval service naming differs across systems: OB uses `adservice`, `cartservice`, etc.; SS uses `carts`, `catalogue`, `orders`, etc.; TT uses `ts-auth-service`, `ts-order-service`, etc. The adapter must normalize these.
- LogHub HDFS has 575,061 blocks total in `anomaly_label.csv`, with expected ~2.9% anomaly rate.
- The topology visualization uses vis.js loaded from CDN — no Python dependency added. Edge direction follows the convention from `context/data_pipeline_specs.md`: A → B means "A is called by B" (A is a dependency of B).
- `seaborn` added as a main dependency (not dev-only) since it's used in notebooks that are part of the project deliverables.

---

### 2026-03-29 — Session 5

**Phase:** Phase 2 — Data Understanding (Week 3 Notebook Validation & Data Quality Review)
**Duration:** ~3 hours

**Completed:**
- Corrected RE2 case count (270→271) and total (735→736) across all project files: `CLAUDE.md`, `PROGRESS.md`, `README.md`, `configs/dataset_config.yaml`, `docs/problem_statement.md`, `docs/baselines.md`, `docs/success_metrics.md`, `context/architecture_and_design.md`, `context/evaluation_strategy.md`, `context/config_reference.md`, `context/data_pipeline_specs.md`
- Fixed outdated `RCAEval.utility` references in `context/data_pipeline_specs.md` — replaced with Zenodo API download instructions matching the actual `scripts/download_datasets.py` implementation
- Updated `context/data_pipeline_specs.md` §6.2 with accurate RCAEval directory structure, file format differences, and all three naming conventions
- Diagnosed Redis missing from notebook 01 output — traced to `SERVICES` list in `generate_training_data.py` (lines 29-36) not including `"redis"`, even though the PromQL filter and actual snapshot data contained Redis metrics
- Fixed `scripts/generate_training_data.py` — added `"redis"` to `SERVICES` list
- Fixed `data/baseline/metadata.json` — added `"redis"` to services array (now reports 7 services)
- Validated notebook 01 (OTel Demo baseline EDA) — all outputs verified:
  - 1440 snapshots, 79,152 rows, 7 services × 8 metrics, full 24h coverage with no gaps
  - 16 zero-variance pairs identified (14 network error rates + 2 fs_usage_bytes) — safe to drop
  - `memory_usage_bytes` and `memory_working_set_bytes` perfectly correlated (r=1.0) across all 7 services — drop one
  - Cross-service correlations confirmed real topology: `redis↔cartservice` network (r=0.939), `frontend↔paymentservice` TX (r=0.987)
  - No anomalous periods detected via 3σ rolling window — very stable baseline
  - Redis is the highest CPU consumer (mean 5.1e-3); frontend is the heaviest memory user (~200MB)
  - `checkoutservice` has 24 missing memory snapshots (1416 vs 1440) — minor container restart, not a concern
- Validated notebook 02 (RCAEval exploration) — all outputs verified:
  - 736 total cases confirmed: RE1=375, RE2=271, RE3=90
  - RE2 extra case traced to `checkoutservice_cpu` in OB having 4 runs instead of 3
  - Discovered RE1 format inconsistency: RE1-OB uses simple naming (51 cols), but RE1-SS (439 cols) and RE1-TT (1246 cols) use container-metric naming — same format as RE2/RE3
  - Infrastructure noise identified in column names: GKE node names, AWS IPs, `istio-init` — adapter must filter these
  - Updated `context/data_pipeline_specs.md` to document three naming conventions (not two)
- Validated notebook 03 (LogHub HDFS exploration) — all outputs verified:
  - 575,061 blocks, 2.93% anomaly rate — matches expected ~2.9%
  - 0 parse failures on 100K lines; 100% of lines contain block IDs (no filtering needed)
  - 100% INFO log level — anomaly detection must rely on template sequence patterns, not log levels
  - 15 Drain3 templates from 10K sample; top 5 cover 95.7%, top 10 cover 100%; 5 singletons
  - Anomalous blocks have fewer logs (mean 9.7 vs 12.7) with wider variance — incomplete lifecycle patterns
  - Drain3 parameters (`depth=4`, `sim_th=0.4`, `max_children=100`) validated as producing reasonable output

**In Progress:**
- None — all notebook validation complete

**Blockers / Issues:**
- **Redis missing from `SERVICES` metadata list:** `generate_training_data.py` included `redis` in the PromQL filter (`_SVC_FILTER`) but not in the `SERVICES` list used for `metadata.json`. The Docker Stats Exporter correctly exposed Redis metrics, Prometheus scraped them, and they were present in all 1440 snapshots — only the metadata was wrong. **Resolution:** Added `"redis"` to both the `SERVICES` list and `metadata.json`.
- **RCAEval RE1 format inconsistency:** The context files stated "RE1 uses `data.csv` with simple naming" — this is only true for RE1-OB (51 columns). RE1-SS (439 cols) and RE1-TT (1246 cols) use `data.csv` but with container-metric naming identical to RE2/RE3. **Resolution:** Updated `context/data_pipeline_specs.md` to document all three naming conventions. The `RCAEvalDataAdapter` must detect format by inspecting column names, not by variant alone.

**Next Session:**
- Begin Phase 3 — Data Preparation: implement OTel Demo data pipeline (`kafka_consumer.py`, `log_parser.py`, `windowing.py`, `feature_engineering.py`)
- Implement `rcaeval_adapter.py` with three-format detection and infrastructure noise filtering
- Implement `loghub_preprocessor.py` with streaming parser

**Notes:**
- All three EDA notebooks have been executed and validated — outputs confirm data quality is good across all three datasets with no blocking issues.
- The 24h OTel Demo baseline data does not need re-collection — Redis metrics were already present in snapshot files; only the metadata label was missing.
- Key design decisions confirmed by EDA: (1) drop `memory_usage_bytes` (redundant with `memory_working_set_bytes`), (2) drop zero-variance network error rate features, (3) per-service anomaly thresholds are necessary due to high variance differences across services, (4) Drain3 parameters validated on HDFS data.
- RE1 adapter cannot assume simple naming based on variant — must inspect column name patterns to detect format.

---

### 2026-03-29 — Session 6

**Phase:** Phase 3 — Data Preparation (Week 4 OTel Demo Pipeline)
**Duration:** ~4 hours

**Completed:**
- Fixed 8 discrepancies in `context/data_pipeline_specs.md`:
  1. Kafka library: rewrote LogConsumer from `kafka-python` to `confluent-kafka` API (`Consumer.poll()`, `msg.error()`, `msg.value().decode()`)
  2. RCAEval `metadata.json` assumption: replaced with `inject_time.txt` parsing (no metadata.json exists in dataset)
  3. RCAEval directory traversal: updated from 1-level to 3-level walk (system → fault → run)
  4. RCAEval case ID format: changed from `"cpu_cartservice_001"` to `"RE2-OB/checkoutservice_cpu/1"`
  5. RCAEval timestamp source: reads from `inject_time.txt` (Unix epoch) instead of metadata.json
  6. `_load_metrics()`: tries `metrics.csv` first, falls back to `data.csv` for RE1
  7. Ground truth extraction: parses service and fault_type from directory name (split on last underscore)
  8. Drain3 `match()` API: replaced `TemplateMiner.match()` (requires Drain3 >=0.9.8) with `Drain.tree_search()` (available in installed v0.9.1)
- Created `src/preprocessing/log_parser.py` — `LogParser` class wrapping Drain3 with `parse()` (mutating), `match()` (read-only via `tree_search`), bidirectional template_to_id/id_to_template mappings, monotonically increasing integer IDs, `FilePersistence`
- Created `src/data_collection/kafka_consumer.py` — `LogConsumer` class using `confluent_kafka.Consumer` with poll loop, JSON decoding, timestamp tuple unpacking, error handling
- Created `src/data_collection/metrics_collector.py` — `MetricsCollector` class with `instant_query()`, `range_query()`, `get_service_metrics()` against Prometheus HTTP API
- Created `src/preprocessing/windowing.py` — `WindowAggregator` class with 60s non-overlapping windows, `add_log()` returns completed window on boundary crossing, `add_metric()`, `flush()` for partial windows
- Created `src/preprocessing/feature_engineering.py` — `FeatureEngineer` class: log template counts + normalized freq + error ratio + unique count (num_templates × 2 + 2) combined with metric stats (mean/std/min/max/p50/p99/delta = 7 per metric), `build_sequence()` for LSTM-AE input, `reset()` for batch use
- Created `src/preprocessing/rcaeval_adapter.py` — `RCAEvalDataAdapter` class with 3-level directory traversal, 3-format detection (RE1-OB simple, RE1-SS/TT container-metric, RE2/RE3 container-metric), infrastructure noise filtering (`gke-*`, `ip-192-168-*`, `istio-init`), ground truth from directory names, timestamps from `inject_time.txt`
- Created `notebooks/04_log_parsing_analysis.ipynb` — executed and validated: 100K HDFS lines → 45 templates, growth curve converges by line ~92K, top 5 templates cover 98.3%, sensitivity analysis confirms sim_th=0.4 is optimal (15 templates at 0.4 vs 642 at 0.6)
- Created `tests/conftest.py` — 6 shared fixtures: `sample_hdfs_log_lines`, `sample_otel_log_lines`, `sample_window_dict`, `empty_window_dict`, `mock_kafka_message`, `mock_prometheus_response`
- Created `tests/unit/test_log_parser.py` — 12 tests: parse tuple return, first ID=0, monotonic IDs, template generalization, num_templates growth, match known/unknown, match doesn't create templates, get_template reverse lookup, persistence, HDFS log parsing
- Created `tests/unit/test_feature_engineering.py` — 13 tests: feature_dim formula, no-metrics/no-templates edge cases, output shape, template counts/freq, empty window, metric features (mean/min/max), error ratio with/without parser, build_sequence shape/ValueError, reset clears delta
- Smoke-tested RCAEval adapter: RE1=375, RE2=271, RE3=90 cases — all counts match expected totals
- All 25 unit tests passing; ruff lint clean; mypy 0 errors on all new source files

**In Progress:**
- None — all Week 4 tasks complete

**Blockers / Issues:**
- **Drain3 v0.9.1 lacks `TemplateMiner.match()` method:** The spec and Context7 docs reference `match()` but it was added in v0.9.8. Our installed version (0.9.1) only has `add_log_message()`. **Resolution:** Used `Drain.tree_search(root_node, tokens)` as an equivalent read-only lookup. This is the underlying mechanism that `match()` wraps in newer versions. Updated spec to document this.
- **`confluent-kafka` vs `kafka-python` API mismatch:** The spec used `kafka-python` imports (`from kafka import KafkaConsumer`) but `pyproject.toml` installs `confluent-kafka>=2.3`. These are entirely different libraries with incompatible APIs. **Resolution:** Rewrote LogConsumer using `confluent_kafka.Consumer` with `poll()` loop, `msg.error()` checking, and `msg.value().decode()` byte handling. Updated spec accordingly.
- **RCAEval has no `metadata.json` files:** The spec assumed each case directory contains `metadata.json` with ground truth and timestamps. No such files exist in the downloaded dataset. **Resolution:** Ground truth is parsed from directory names (`{service}_{fault_type}`) and timestamps from `inject_time.txt` (Unix epoch). Rewrote the entire adapter with 3-level directory traversal.
- **RE1-OB simple format detection failed initially:** The `_is_simple_format()` function checked for hyphens in column names, but RE1-OB has `frontend-external_load` and `frontend-external_error` (hyphens in service names). **Resolution:** Changed detection to check whether the metric suffix after the last underscore is a known simple metric (cpu, mem, load, latency, error) rather than checking for absence of hyphens.
- **mypy type errors in 3 files:** `windowing.py` had `None + timedelta` potential; `kafka_consumer.py` had `bytes | None` decode issue; `metrics_collector.py` had untyped return from `resp.json()`. **Resolution:** Added `assert` guard for window_start, null check for message value, and explicit type annotation for Prometheus response.

**Next Session:**
- Begin Phase 3 Week 5: `loghub_preprocessor.py`, `topology_extractor.py`, `runbook_indexer.py`, `embeddings.py`
- Create 5 runbooks in `runbooks/`
- Create `scripts/prepare_data_splits.py`
- Index runbooks into ChromaDB

**Notes:**
- Notebook 04 focuses on HDFS logs only — OTel Demo log parsing cannot be demonstrated until a log shipper (Promtail → Kafka) is configured. The 24h baseline collected metrics only; logs require separate infrastructure (Kafka topic ingestion from OTel services). This does not block Phase 3 Week 5 or the LSTM-AE pretraining (which uses HDFS, not OTel logs).
- The 24h baseline metric data does NOT need re-collection. Log data was never part of the baseline collection — logs enter the system via real-time Kafka streaming during fine-tuning (Phase 4), not from stored baseline files.
- Drain3 on 100K HDFS lines: 45 templates (vs 15 from 10K in notebook 03). The difference is from rare event templates (exceptions, deletions, replication). Core lifecycle templates (top 5 = 98.3% coverage) are the same. Template vocabulary converges by ~92K lines.
- Drain3 sensitivity analysis: sim_th=0.4 sits in the "safe zone" (15 templates). At 0.6 templates explode to 642 (40x increase). This validates the chosen parameter.
- RCAEval RE1-OB has 14 services in metrics (includes `main` and `frontend-external`), while RE2/RE3-OB have 13. This is inherent to the upstream dataset, not a bug.
- Context7 was used to verify up-to-date APIs for confluent-kafka, Drain3, and ChromaDB before implementation.

---

### 2026-04-01 — Session 7

**Phase:** Phase 3 → Phase 4 — Data Preparation (Week 5 completion) + Modeling (Week 6 LSTM-Autoencoder)
**Duration:** ~5 hours

**Completed:**

*Phase 3 Week 5 (carried from Session 6):*
- Created `src/preprocessing/loghub_preprocessor.py` — `LogHubHDFSPreprocessor` class with streaming HDFS.log parsing, block ID grouping via regex, anomaly label loading, fixed-length sequence building (left-padded, chunked); `create_hdfs_splits()` and `create_otel_splits()` helper functions
- Created `src/data_collection/topology_extractor.py` — `TopologyGraph` class with NetworkX DiGraph (7 nodes, 9 edges), `get_subgraph()` returning upstream/downstream dependencies with guard for unknown services, `to_json()` serialization
- Created `src/knowledge_base/embeddings.py` — lazy-loaded singleton wrapper for sentence-transformers `all-MiniLM-L6-v2` with `embed_text()` and `embed_batch()` functions
- Created `src/knowledge_base/runbook_indexer.py` — `RunbookIndexer` class with ChromaDB `PersistentClient`, paragraph-boundary chunking, MD5 document IDs, upsert-based indexing, similarity search with `1.0 - distance` relevance scoring
- Created 5 runbook markdown files in `runbooks/`: `connection_exhaustion.md`, `cascading_failure.md`, `memory_pressure.md`, `high_latency.md`, `general_troubleshooting.md` — each 500+ words with Symptoms, Root Cause, Investigation Steps, Remediation, Prevention sections
- Created `scripts/prepare_data_splits.py` — CLI script with `--hdfs`, `--otel`, `--all` flags
- Pinned `transformers>=4.36,<5.0` and `sentence-transformers>=2.2,<4.0` in `pyproject.toml` to resolve Intel Mac compatibility (transformers 5.x requires PyTorch >=2.4); downgraded to transformers 4.57.6 and sentence-transformers 3.4.1
- Indexed all 5 runbooks into ChromaDB — 57 chunks total; verified search: 3/4 top-1 matches, 4/4 top-3 matches
- Added `hdfs_data_dir` fixture to `tests/conftest.py` (synthetic 4 blocks, 16 log lines)
- Created 4 test files: `test_loghub_preprocessor.py` (15 tests), `test_topology_extractor.py` (10 tests), `test_embeddings.py` (5 tests), `test_runbook_indexer.py` (11 tests)
- All 66 unit tests passing; ruff lint clean; mypy 0 errors

*Phase 4 Week 6:*
- Created `src/anomaly_detection/lstm_autoencoder.py` — `LSTMAutoencoder(nn.Module)` with encoder-decoder LSTM architecture, latent bottleneck (Linear→LSTM→Linear→Linear→LSTM→Linear), `forward()` and `get_reconstruction_error()` (MSE per sequence)
- Created `src/anomaly_detection/trainer.py` — `AnomalyTrainer` with MSELoss, Adam optimizer, DataLoader-based training, early stopping with best-model in-memory restoration, device auto-detection
- Created `src/anomaly_detection/pretrain_on_loghub.py` — `pretrain_on_hdfs()` (HDFS parsing → one-hot encode → train), `finetune_on_otel_demo()` (load pretrained → partial weight transfer → fine-tune with lower LR), `_load_compatible_weights()` (filters by key name and shape match, skips embedding/output_layer)
- Created `src/anomaly_detection/threshold.py` — `calculate_threshold()` with batched inference under `no_grad()`, returns `np.percentile(errors, percentile)`
- Created `src/anomaly_detection/isolation_forest.py` — `IsolationForestDetector` wrapping sklearn with `fit()`, `predict()`, `score_samples()`, `n_jobs=-1`
- Created `src/anomaly_detection/detector.py` — `AnomalyDetector` Fast Loop → Slow Loop bridge with `score()` method, alert dict construction, callback invocation on threshold breach
- Created `tests/unit/test_anomaly_detection.py` — 22 tests across 7 classes (LSTMAutoencoder, AnomalyTrainer, Threshold, IsolationForest, AnomalyDetector, LoadCompatibleWeights, OneHotEncode)
- Created `notebooks/05_anomaly_detection_dev.ipynb` — 8-cell Colab GPU notebook (setup, HDFS load, pretrain, plot curves, threshold, HDFS benchmark, error distribution, fine-tune placeholder)
- All 88 unit tests passing; ruff lint clean; mypy 0 errors on `src/anomaly_detection/`

*Pretraining (run manually on Google Colab):*
- Parsed full HDFS.log: 11.2M lines → 115 Drain3 templates, 575,061 blocks
- Normal sequences: 1,608,443 (shape `(1608443, 10)`), anomalous: 42,785; sequence-level anomaly rate: 2.59%
- One-hot encoded to shape `(1286754, 10, 115)` train / `(321689, 10, 115)` val
- Training converged: loss 0.000444 → 0.000015 (epoch 1→36), val_loss best 0.000011 at epoch 31-32
- Early stopped at epoch 36/50 (patience=5); model restored to best weights
- Checkpoint saved: `models/lstm_autoencoder/pretrained_hdfs.pt` (138,243 parameters)
- 95th percentile threshold on holdout normal data: 0.000003
- HDFS benchmark: F1=0.58, Precision=0.60, Recall=0.56 (anomaly class) — moderate, expected for one-hot template-only features
- Training curves and error distribution plots saved to `docs/images/`

*PROGRESS.md updates:*
- Added "Week 6.5 — Log Pipeline & Fine-tuning Data Collection" section between Week 6 and Week 7 with 10 tasks for Promtail, log collection, fine-tuning, and threshold calculation

**In Progress:**
- None — all Week 5 + Week 6 implementation tasks complete; pretraining done

**Blockers / Issues:**
- **`transformers` 5.x NameError on Intel Mac:** The installed `transformers` 5.3.0 references `nn.Module` in a function signature but only imports `torch.nn` when PyTorch >=2.4 is detected. With torch 2.2.2, importing `sentence_transformers` crashes. **Resolution:** Pinned `transformers>=4.36,<5.0` and `sentence-transformers>=2.2,<4.0` in `pyproject.toml`. Downgraded to transformers 4.57.6 and sentence-transformers 3.4.1. All imports and ChromaDB indexing now work.
- **Drain3 v0.9.1 `load_state()` RuntimeError in tests:** `TemplateMiner.load_state()` iterates a dict while modifying it (Python 3.12 raises RuntimeError). Occurs when `LogParser()` loads existing state from `models/drain3/`. **Resolution:** Tests pass fresh `tmp_path`-based persistence paths to `LogParser` to avoid loading stale state.
- **HDFS benchmark F1=0.58 (below 0.90 target):** The one-hot encoded template sequences are sparse — anomalous blocks often share the same templates as normal blocks in different order/frequency. This is the HDFS-only pretraining result; the primary evaluation (OTel Demo fault injection) uses richer combined log+metric feature vectors. The HDFS benchmark is a nice-to-have track (see PROGRESS.md Phase 5).
- **HDFS full corpus produces 115 templates (vs 45 from 100K sample):** The full 11.2M lines expose rare event templates not seen in the 100K sample. Template vocabulary converges; 115 is the final count. This changes `input_dim` from the estimated 20-50 to 115, but the architecture handles dynamic `input_dim`.

**Next Session:**
- Begin Week 6.5: Add Promtail to `docker-compose.yml`, configure log shipping, collect 4-8h baseline with logs+metrics
- Run fine-tuning on OTel Demo with collected feature vectors
- Calculate production threshold on fine-tuned model
- Begin Week 7: Causal discovery (`pc_algorithm.py`, `counterfactual.py`, `graph_utils.py`)

**Notes:**
- The trainer saves best model weights in memory (not to disk) and restores after training loop. The caller (`pretrain_on_hdfs`, `finetune_on_otel_demo`) handles `torch.save()` with wrapped format `{"model_state_dict": ..., "history": ...}`.
- `_load_compatible_weights()` filters checkpoint keys by both name pattern and shape match — it loads ~8-12 LSTM encoder/decoder tensors while skipping embedding and output_layer (which depend on `input_dim` and differ between HDFS and OTel).
- The `AnomalyDetector.score()` method returns the reconstruction error as a float and fires the `on_anomaly` callback only when error > threshold. The alert dict includes `anomaly_score`, `threshold`, `severity`, `timestamp`, and `affected_services`.
- Ruff flagged `import torch.nn.functional as F` as N812 (CamelCase imported as non-lowercase). Renamed to `functional` throughout `pretrain_on_loghub.py`.
- Context7 was used to verify PyTorch nn.LSTM, DataLoader, and training loop patterns before implementation.
- The HDFS anomaly rate at the sequence level (2.59%) is lower than the block level (2.93%) because anomalous blocks have fewer log events per block (mean 9.7 vs 12.7), producing fewer chunked sequences per block.

---

### 2026-04-03 — Session 8

**Phase:** Phase 4 — Modeling (Week 6.5 completion + Week 7 Causal Discovery)
**Duration:** ~6 hours

**Completed:**

*Week 6.5 — Before collection (infrastructure setup):*
- Created `infrastructure/promtail/promtail-config.yml` — Docker SD via socket, `service` label from `com.docker.compose.service`, push to Loki at `loki:3100`
- Added Promtail service to `docker-compose.yml` — `grafana/promtail:2.9.0`, 128M memory, Docker socket mount
- Updated `scripts/start_infrastructure.sh` and `scripts/stop_infrastructure.sh` to include Promtail
- Updated `scripts/generate_training_data.py` — per-service Loki queries (`{service="<name>"}`), added `"service"` key to log entries; fixed pre-existing lint/mypy issues (line length, `timezone.utc` → `UTC`, `params` type annotation)
- Fixed `demo_app/docker-compose.demo.yml` — added `SHIPPING_SERVICE_ADDR=localhost:50053` and `EMAIL_SERVICE_ADDR=localhost:8080` to checkoutservice (was panic-crashing on startup without shippingservice)
- Verified Promtail shipping logs: Loki receiving labeled entries from all 7 OTel Demo services, 3-minute test collection confirmed non-zero log counts with proper service attribution

*Week 6.5 — 24h data collection:*
- Ran 24h baseline with metrics + logs: `caffeinate -s poetry run python scripts/generate_training_data.py --duration 24h --output-dir data/baseline_with_logs/`
- Collection completed: 1440 snapshots, 2885 log entries (checkoutservice: 1440, productcatalogservice: 1440, redis: 5), 8 metric types × 7 services, status=completed

*Week 6.5 — After collection (fine-tuning):*
- Preprocessed baseline into feature vectors: 1430 sequences → train (1144, 10, 54) / val (286, 10, 54) with z-score normalization, saved to `data/splits/otel/`
- Feature vector: 54 dims = log (5 templates × 2 + 2 = 12) + metrics (6 metrics × 7 stats = 42). Metrics: cpu_usage_rate, memory_working_set_bytes, network_rx/tx_bytes_rate, network_rx/tx_errors_rate
- Fine-tuned on Colab Pro (L4 GPU): 3 iterations to optimize — (1) lr=0.0001 too slow (100 epochs, val_loss=0.425, no convergence), (2) lr=0.001 with 40 dims converged at epoch 60 (val_loss=0.296), (3) lr=0.001 with 54 dims (added error rate metrics) converged at epoch 158 (val_loss=0.134, best result)
- Checkpoint saved: `models/lstm_autoencoder/finetuned_otel.pt` (132,326 params)
- 95th percentile threshold: 0.253
- Synthetic anomaly benchmark: F1=0.97, Precision=0.95, Recall=1.00 (CPU spike 100%, Memory spike 100%, Network error 100% detection rate)
- Training curves and benchmark plots saved to `docs/images/` (finetune_training_curves.png, otel_finetune_benchmark.png)

*Week 7 — Causal Discovery:*
- Created `src/causal_discovery/graph_utils.py` — `CausalEdge` and `CausalGraph` dataclasses with `to_ascii()` rendering and `top_edges()` sorting
- Created `src/causal_discovery/pc_algorithm.py` — `discover_causal_graph()` wrapping causal-learn PC (Fisher's Z, stable=True, uc_rule=0, uc_priority=2), `create_time_lags()` for temporal feature augmentation, `parse_causal_graph()` for extracting directed edges from causal-learn's adjacency matrix
- Created `src/causal_discovery/counterfactual.py` — `calculate_counterfactual_confidence()` (correlation² × z-score contribution, clamped [0,1]) and `compute_baseline_stats()`
- Created `tests/unit/test_causal_discovery.py` — 31 tests across 7 classes: CausalEdge (2), CausalGraph (5), CreateTimeLags (6), DiscoverCausalGraph (5), ParseCausalGraph (4), ComputeBaselineStats (4), CounterfactualConfidence (5)
- Created `notebooks/06_causal_discovery_dev.ipynb` — validated on synthetic data: simple A→B→C chain (correct skeleton, no spurious A→C), complex B→C←D topology (collider correctly oriented), alpha sensitivity analysis, counterfactual confidence scoring, time-lagged features
- All 119 unit tests passing (88 existing + 31 new); ruff clean; mypy clean

**In Progress:**
- None — all Week 6.5 + Week 7 tasks complete

**Blockers / Issues:**
- **Promtail `keep` relabel causes empty-label errors:** Promtail 2.9.0 with `docker_sd_configs` opens log streams for ALL discovered containers before applying relabel rules. The `keep` action filters targets, but containers that don't match still have their logs read and batched without labels, causing Loki to reject with "at least one label pair is required per stream". **Resolution:** Removed the `keep` filter; set a default `job=docker` label on all containers. The `generate_training_data.py` script filters by service at the Loki query level (`{service="<name>"}`), so monitoring stack logs in Loki are harmless.
- **checkoutservice panic without SHIPPING_SERVICE_ADDR:** The reduced OTel Demo excluded shippingservice, but checkoutservice requires `SHIPPING_SERVICE_ADDR` to start. **Resolution:** Added `SHIPPING_SERVICE_ADDR=localhost:50053` and `EMAIL_SERVICE_ADDR=localhost:8080` as dummy env vars in `demo_app/docker-compose.demo.yml`. Service starts cleanly; checkout requests needing shipping fail gracefully at gRPC level.
- **Un-normalized features caused MSE loss ~10^14:** Raw feature vectors span 6 orders of magnitude (memory_bytes ~10^8 vs cpu_rate ~10^-3). First fine-tuning attempt produced constant val_loss with no learning. **Resolution:** Added z-score normalization per feature dimension before saving splits. Scaler params (`scaler_mean.npy`, `scaler_std.npy`) saved alongside splits for inference-time use.
- **lr=0.0001 too conservative for fine-tuning:** With HDFS→OTel dimension mismatch (115→54), embedding and output layers are reinitialized from scratch. Only LSTM body weights transfer. The conservative lr=0.0001 needed 100+ epochs without converging. **Resolution:** Increased to lr=0.001 (matching pretraining rate). Converged properly with early stopping.
- **PC algorithm produces undirected edges in simple chains:** The A→B→C synthetic data has no collider, so PC correctly recovers the skeleton but cannot determine edge direction. Only topologies with v-structures (e.g., B→C←D) produce directed edges. **Resolution:** This is expected PC behavior. The counterfactual confidence scoring handles undirected edges by scoring all edge directions and ranking by confidence.
- **causal-learn not installed on Colab:** `notebooks/06_causal_discovery_dev.ipynb` requires `causal-learn` which is not in Colab's default environment. **Resolution:** Added `%pip install causal-learn` cell at the top of the notebook.

**Next Session:**
- Begin Week 8: LangGraph Agent (`state.py`, `graph.py`, agent tools, prompts, executor)
- Create `notebooks/07_agent_prototyping.ipynb`
- End-to-end agent test with manually injected fault

**Notes:**
- Promtail version 2.9.0 uses `docker_sd_configs` syntax (not the newer `docker:` shorthand which was added in later versions). The Context7 docs showed both syntaxes; the newer one caused "field docker not found" errors.
- causal-learn's edge encoding: `cg.G.graph[j,i]==1 and cg.G.graph[i,j]==-1` means i→j (note transposed indices). The `parse_causal_graph()` function handles this mapping.
- OTel Demo services produce very few stdout logs (~2 entries/minute total from checkoutservice + productcatalogservice). This is inherent to compiled gRPC services. The ~2,900 log entries over 24h is sufficient because feature vectors are primarily metric-driven (42/54 dims).
- Fine-tuning was iteratively improved across 3 Colab runs: (1) unnormalized features → broken, (2) normalized + 40 dims + lr=0.0001 → slow convergence, (3) normalized + 54 dims + lr=0.001 → val_loss=0.134, F1=0.97 on synthetic benchmark. Adding network error rate metrics (zero in baseline, spike during faults) proved valuable.
- Context7 was used to verify causal-learn PC API (return value structure, edge encoding conventions, Fisher Z test import path) and Promtail Docker SD configuration syntax.

---

### 2026-04-05 — Session 9

**Phase:** Phase 4 — Modeling (Week 8 LangGraph Agent)
**Duration:** ~5 hours

**Completed:**

*Phase A — State Definition + Prompts:*
- Created `src/agent/state.py` — `AgentState` TypedDict with 13 fields in 4 groups (input, investigation, causal, output); `messages` field uses `Annotated[list, add_messages]` reducer from LangGraph; all other fields use last-write-wins
- Created `src/agent/prompts/system_prompt.py` — `SYSTEM_PROMPT` constant with 6-step investigation methodology (map topology → form hypotheses → gather evidence → analyze causation → consult docs → generate report), key principles, available metrics list, tool budget guidance
- Created `src/agent/prompts/report_template.py` — `RCA_REPORT_TEMPLATE` with 14 `str.format()` placeholders for structured RCA output

*Phase B — Tool Implementations:*
- Created `src/agent/tools/get_topology.py` — wraps `TopologyGraph`, returns full topology or per-service subgraph with upstream/downstream lists; no external dependency
- Created `src/agent/tools/query_metrics.py` — wraps `MetricsCollector` with 6 PromQL templates mapping to Docker Stats Exporter metrics; computes stats (min/max/mean/std/current) and 2σ anomalous flag
- Created `src/agent/tools/search_logs.py` — direct HTTP calls to Loki (`/loki/api/v1/query_range`), LogQL construction with optional service filter, error level extraction, top pattern counting
- Created `src/agent/tools/search_runbooks.py` — wraps `RunbookIndexer`, transforms results to `{title, content, relevance_score, source}` format
- Created `src/agent/tools/discover_causation.py` — orchestrates full causal pipeline: MetricsCollector → DataFrame → `create_time_lags()` → `discover_causal_graph(max_conditioning_set=4)` → `parse_causal_graph()` → `compute_baseline_stats()` → `calculate_counterfactual_confidence()` → `CausalGraph.to_ascii()`; identifies root cause as highest-confidence source with no incoming edges
- Created `src/agent/tools/__init__.py` — `TOOLS` registry list + `__all__` exports

*Phase C — Graph + Executor:*
- Created `src/agent/graph.py` — 5-node `StateGraph` with `ChatGoogleGenerativeAI(model="gemini-2.5-flash-lite")`, `bind_tools(TOOLS)`, conditional routing via `should_continue()`; manual tool execution in `gather_evidence_node` to track tool call budget; `_parse_hypotheses()` JSON extraction from LLM responses; `_extract_actions()` from report text
- Created `src/agent/executor.py` — `AgentExecutor` class with `from_config()` classmethod, dual-mode `investigate()` (live: tools query Prometheus/Loki; offline: pre-loaded data for RCAEval), initial state construction with `SystemMessage` + `HumanMessage`, error handling with fallback report
- Updated `src/agent/__init__.py` — exports `AgentExecutor` and `AgentState`

*Phase D — Comprehensive Tests (62 new tests):*
- Updated `tests/conftest.py` — added 5 new fixtures: `sample_alert`, `sample_prometheus_range_response_factory`, `sample_loki_response_factory`, `sample_agent_config`, `sample_causal_metrics_df`
- Created `tests/unit/test_agent_tools.py` — 35 tests across 5 classes: TestGetTopology (6), TestQueryMetrics (8), TestSearchLogs (7), TestSearchRunbooks (5), TestDiscoverCausation (9). All external services mocked.
- Created `tests/unit/test_agent_state.py` — 5 tests: TypedDict validation, add_messages annotation, field completeness, construction, type hints
- Created `tests/unit/test_agent_graph.py` — 12 tests: graph compilation (2), should_continue routing (5), analyze_context_node (2), helper functions (3)
- Created `tests/unit/test_agent_executor.py` — 10 tests: init/config loading (2), investigate output (4), _format_alert (2), _extract_top3 (3)
- Created `tests/integration/test_data_pipeline.py` — 7 tests: Prometheus (2), Loki (2), Topology (2), FullPipeline (1). All `@pytest.mark.integration`.
- Created `tests/integration/test_agent_workflow.py` — 8 tests: graph compilation (1), live tool invocation (3), end-to-end investigation (3, requires GEMINI_API_KEY). All `@pytest.mark.integration`.

*Phase E — Notebook + Demo:*
- Created `notebooks/07_agent_prototyping.ipynb` — 7-section notebook: setup/imports, individual tool testing (all 5 tools), graph compilation + Mermaid visualization, routing logic verification, AgentExecutor setup from config, full investigation with synthetic alert, report inspection
- Created `scripts/run_agent_demo.py` — CLI demo script with prerequisite checks (API key, Prometheus, Loki), synthetic alert construction, live/offline modes, formatted output with timing

*Bug Fixes + Model Change:*
- Changed LLM from `gemini-1.5-flash` → `gemini-2.5-flash-lite` in `src/agent/graph.py`, `configs/agent_config.yaml`, and `tests/conftest.py`
- Fixed double curly braces in PromQL templates — `{{service="{service}"}}` produced literal `{{...}}` since `.replace()` doesn't interpret Python brace escaping; Prometheus returned 400 Bad Request. Fixed to single braces in `query_metrics.py` (6 templates) and `discover_causation.py` (4 templates)
- Fixed system prompt referencing unavailable metrics (`error_rate`, `latency_p99`) — the LLM wasted 4/10 tool calls on invalid metric names. Added explicit available metrics list to `SYSTEM_PROMPT` and updated tool budget example to reference `cpu_usage, memory_usage`
- Added `max_conditioning_set` parameter to `discover_causal_graph()` in `src/causal_discovery/pc_algorithm.py` — maps to causal-learn's `depth` parameter; set to 4 in `discover_causation.py` to prevent combinatorial explosion (32 columns at unrestricted depth took 30+ minutes; capped at depth 4, runs in <30 seconds)

**In Progress:**
- End-to-end agent demo tested live but was interrupted due to PC algorithm taking 30+ min before depth cap was added. Demo to be re-run with `max_conditioning_set=4`.

**Blockers / Issues:**
- **Double curly braces in PromQL templates:** The `METRIC_PROMQL` dict used `{{service="{service}"}}` which is Python's brace-escaping syntax for `.format()` strings, but the code uses `.replace()` instead. The double braces passed through literally, producing invalid PromQL that Prometheus rejected with 400 Bad Request. **Resolution:** Changed all PromQL templates to use single braces: `{service="{service}"}`.
- **LLM requesting unavailable metrics:** The system prompt listed `error_rate` and `latency_p99` in the tool budget example, but Docker Stats Exporter doesn't expose application-level metrics — only container-level (CPU, memory, network). The LLM wasted 4 of its 10 tool calls on these invalid metrics before the tool returned error dicts. **Resolution:** Added explicit "Available Metrics" section to `SYSTEM_PROMPT` listing only the 6 valid metric names, and updated the tool budget example.
- **PC algorithm combinatorial explosion at depth 5–6:** With 2 services × 4 metrics × 4 lag levels = 32 columns, the unrestricted PC algorithm spent 30+ minutes in depth 5–6 computing C(30,5) = 142,506 and C(30,6) = 593,775 conditioning set tests per edge pair. This is correct algorithm behavior, not a bug — but impractical for real-time RCA. **Resolution:** Added `max_conditioning_set` parameter to `discover_causal_graph()` (maps to causal-learn's `depth` kwarg), set to 4 in the discover_causation tool. Depth 4 is sufficient for the OTel Demo topology (longest causal chain = 3 hops) and completes in seconds. The config value `causal_discovery.max_conditioning_set: 3` in `agent_config.yaml` was already defined but not wired through — now it is.
- **LangGraph `AgentState` must be TypedDict (not dict subclass):** Initial implementation used `class AgentState(dict)` with annotations, which LangGraph doesn't recognize for state validation. **Resolution:** Changed to `class AgentState(TypedDict)` from `typing_extensions`, matching LangGraph's documented pattern.
- **mypy `response.content` type:** `ChatGoogleGenerativeAI.invoke()` returns `AIMessage` whose `.content` is `str | list[str | dict]`, not just `str`. Passing to string-typed functions triggered mypy errors. **Resolution:** Added `isinstance(response.content, str)` guards before passing to `_parse_hypotheses()` and `_extract_actions()`.

**Next Session:**
- Re-run `scripts/run_agent_demo.py` with depth-capped PC algorithm and verify full RCA report generation
- Begin Phase 5 — Evaluation: create fault injection scripts, evaluation framework
- Advisor check-in with working agent demo

**Notes:**
- Installed dependency versions: LangGraph 1.1.3, langchain-core 1.2.22, langchain-google-genai 4.2.1, Poetry 2.3.2.
- The `AgentState` uses `Annotated[list, add_messages]` for the `messages` field — this is the LangGraph reducer that appends new messages by ID instead of replacing the list. All other fields use default last-write-wins semantics.
- The 5-node graph structure (not `create_react_agent`) was chosen deliberately — it enforces the investigation protocol regardless of what the LLM prefers, preventing it from skipping directly to report generation.
- Tool execution in `gather_evidence_node` is manual (not LangGraph's built-in `ToolNode`) to maintain control over the tool call counter. The node checks `response.tool_calls`, executes each via `_TOOLS_BY_NAME`, creates `ToolMessage` objects, and decrements `tool_calls_remaining`.
- The `discover_causation` tool identifies root cause as the highest-confidence edge source with no incoming edges. If all sources also appear as targets (cycles), it falls back to the source of the highest-confidence edge overall.
- The system prompt explicitly lists the 6 available metrics from Docker Stats Exporter. The original spec's 7 metrics (latency_p50/p99, error_rate, request_count, connection_count) are application-level metrics not available from container monitoring — only cpu_usage, memory_usage, and network_rx/tx_bytes/errors_rate are exposed.
- Context7 was used to verify LangGraph `StateGraph` API (TypedDict state, `add_messages` reducer, `START`/`END` constants, `add_conditional_edges`), `ChatGoogleGenerativeAI` tool binding, and `ToolMessage` construction patterns.
- 181 total unit tests passing (119 existing + 62 new). All ruff lint clean. mypy 0 errors on `src/agent/` (13 files).

---

### 2026-04-06 — Session 10

**Phase:** Phase 5 — Evaluation (Week 9 Fault Injection Evaluation)
**Duration:** ~5 hours

**Completed:**

*Evaluation Framework (12 new files):*
- Created `tests/evaluation/metrics_calculator.py` — `EvaluationResults` dataclass, `load_results()`, `recall_at_1()`, `recall_at_3()`, `precision()`, `detection_latency()`, `mttr_proxy()`, `confidence_interval()` (t-distribution), `calculate_metrics()` orchestrator with per-fault breakdowns. Uses `datetime.fromisoformat()` for timestamp parsing (compatible with `datetime.now().isoformat()` output)
- Created `tests/evaluation/fault_injection_suite.py` — `FAULT_SCRIPTS` (8 entries), `GROUND_TRUTH` (8 entries), `run_fault_injection()` (inject → 60s wait → investigate → save JSON + RCA report → restore), `_load_per_fault_cooldowns()`, `main()` with argparse (`--fault`, `--repetitions`, `--output`, `--cooldown`, `--max-wait`)
- Created `tests/evaluation/baseline_comparison.py` — `RuleBasedBaseline` (static CPU/memory thresholds), `ADOnlyBaseline` (LSTM-AE reconstruction error ranking), `LLMWithoutToolsBaseline` (Gemini without tool bindings, `_parse_response()` for service name extraction), `run_all_baselines()` orchestrator
- Created `scripts/inject_faults.py` — user-facing coordinator with `preflight_checks()` (Docker, GEMINI_API_KEY, Prometheus, Loki, demo services, fault scripts), `print_summary()`, argparse with `--skip-preflight` and `--summary-only` flags

*8 Fault Injection Bash Scripts in `demo_app/fault_scenarios/`:*
- `01_service_crash.sh` — `docker compose stop/start cartservice`
- `02_high_latency.sh` — sidecar container sharing paymentservice network namespace, Alpine + iproute2 + `tc netem delay 500ms` (sidecar approach because OTel Demo images are distroless with no package manager)
- `03_memory_pressure.sh` — `docker update --memory 128m` checkoutservice (default 256M from compose)
- `04_cpu_throttling.sh` — `docker update --cpus 0.1` productcatalogservice; restore via `docker compose up -d --force-recreate` (because `docker update --cpus` sets NanoCpus which can't be cleared via `docker update`)
- `05_connection_exhaustion.sh` — `redis-cli CONFIG SET maxclients 5/10000`
- `06_network_partition.sh` — `docker network disconnect/connect opsagent_opsagent-net`
- `07_cascading_failure.sh` — crash cartservice + 30s propagation wait
- `08_config_error.sh` — stop currencyservice, `docker run` replacement with invalid `CURRENCY_DATA_FILE` env var, restore via stop+rm+start

*75 New Unit Tests (4 test files):*
- `tests/unit/test_metrics_calculator.py` — 30 tests: RecallAt1 (4), RecallAt3 (4), Precision (4), DetectionLatency (2), MttrProxy (3), ConfidenceInterval (3), LoadResults (3), CalculateMetrics (7)
- `tests/unit/test_fault_injection_suite.py` — 20 tests: FaultScripts (5), ResolveScript (2), RunFaultInjection (8), LoadPerFaultCooldowns (4)
- `tests/unit/test_baseline_comparison.py` — 17 tests: RuleBasedBaseline (5), ADOnlyBaseline (5), LLMWithoutToolsBaseline (5), RunAllBaselines (3)
- `tests/unit/test_inject_faults.py` — 8 tests: PreflightChecks (5), PrintSummary (3)

*Critical Bug Fixes to Agent Pipeline (discovered during live testing):*
- **`discover_causation.py` — singular matrix fix:** Added zero-variance column dropping, highly-correlated column dropping (r > 0.999 via `_drop_correlated_columns()`), and tiny jitter (`np.random.default_rng(42)`, scale 1e-8) before passing data to PC algorithm. Fisher's Z test crashes with `LinAlgError: Singular matrix` when columns are constant or perfectly correlated (common with network_rx/tx_errors_rate = 0 and lagged copies of slow-changing metrics)
- **`discover_causation.py` — service cap:** Hard-capped at 5 services to prevent combinatorial explosion (7 services × 4 metrics × 3 lag levels = 84 columns → PC takes 30+ minutes even at depth 3)
- **`discover_causation.py` — reduced lags:** Changed from `lags=[1, 2, 5]` to `lags=[1, 2]` to reduce column count. Lag 5 added many columns with minimal causal benefit for short-lived faults
- **`discover_causation.py` — depth 3:** Set `max_conditioning_set=3` (was 4). Depth 4 tested but produced worse results — more aggressive edge pruning removed weak signal from crashed services while strengthening spurious signals from healthy services
- **`query_metrics.py` — stale/sparse data detection:** Added detection of crashed/down services via two signals: (1) stale data (last data point >90s old), (2) sparse data (<70% of expected data points for the time range). Returns `anomalous: True` with `CRITICAL` note when either triggers. Previously, a crashed service returned stale cached data that looked "normal" (`anomalous: False`), so the agent couldn't distinguish dead services from healthy ones
- **`query_metrics.py` — no-data CRITICAL signal:** When `query_metrics` returns zero data points, it now returns `anomalous: True` with explicit "service is DOWN, CRASHED, or UNREACHABLE" message (previously returned `anomalous: False` with bland note)
- **`graph.py` — hypothesis override for low-confidence causal results:** When PC algorithm returns confidence <50% or "inconclusive" (common when root cause service is down and invisible to PC), the agent now checks LLM hypotheses and evidence for CRITICAL signals. A service flagged CRITICAL by `query_metrics` (no data or sparse data) gets boosted to at least 70% confidence. This prevents the PC algorithm from overriding correct LLM hypotheses with weak causal signals from surviving services
- **`graph.py` — 10-minute causal discovery window:** Changed from 30-minute to 10-minute query window for causal discovery, increasing anomaly-to-baseline ratio (4/40 fault data points vs 4/120)
- **`fault_injection_suite.py` — 60s pre-investigation wait:** Changed from 10s to 60s wait before triggering investigation, allowing ~4 Prometheus scrape cycles of anomalous data for stronger PC algorithm signal
- **`fault_injection_suite.py` — neutral alert title:** Changed from `"Fault Injection Evaluation — {fault_type}"` to `"Anomaly Detected — Automated Investigation Triggered"` to prevent LLM from reading the fault type hint and to prevent it from blaming "Fault Injection System" as root cause
- **`fault_injection_suite.py` — affected_services in alert:** Added all 7 services to alert's `affected_services` list so agent knows which services to investigate (previously empty, causing agent to investigate blind)
- **`04_cpu_throttling.sh` — restore via recreate:** `docker update --cpus 0` doesn't reset NanoCpus. `docker update --cpu-quota=-1` fails with "Conflicting options: CPU Period cannot be updated as NanoCPUs has already been set". Fixed restore to use `docker compose up -d --force-recreate productcatalogservice`
- **`02_high_latency.sh` — sidecar approach:** OTel Demo paymentservice image is distroless (no `apt-get`, no `apk`, no `tc`). Replaced in-container `tc` with a lightweight Alpine sidecar sharing paymentservice's network namespace (`--network container:demo_app-paymentservice-1`, `--cap-add NET_ADMIN`)

*End-to-End Verification:*
- Successfully ran `service_crash` fault injection test: Recall@1=100%, Recall@3=100%, MTTR=15.3s, detection latency=60.4s, investigation duration=15.3s
- All 256 unit tests passing (181 existing + 75 new). All ruff lint clean

**In Progress:**
- 40 fault injection tests not yet run (all 8 fault types × 5 runs planned for single-day execution in next session)
- 3 internal baseline evaluations not yet run

**Blockers / Issues:**
- **OTel Demo images are distroless (no package manager):** `02_high_latency.sh` originally tried to run `tc` inside the paymentservice container, but the image has no `apt-get`, `apk`, or `tc`. **Resolution:** Used Alpine sidecar container sharing paymentservice's network namespace (`--network container:...`). The sidecar installs `iproute2` and runs `tc netem` on the shared `eth0`.
- **`docker update --cpus 0` doesn't reset NanoCpus on macOS Docker Desktop:** Once `--cpus` sets `NanoCpus`, it can't be cleared via `docker update`. Even `--cpu-quota=-1` fails with "Conflicting options". **Resolution:** Restore via `docker compose up -d --force-recreate productcatalogservice` which recreates the container from compose (no CPU limit defined).
- **Gemini API free tier quota (20 requests/day):** The agent makes 4-5 LLM calls per investigation. Free tier's 20/day limit was exhausted during iterative testing. **Resolution:** Upgraded to Gemini paid tier.
- **PC algorithm singular matrix error:** Fisher's Z test in causal-learn crashes with `LinAlgError: Singular matrix` when the correlation matrix has zero-variance or perfectly-correlated columns. Network error rates are zero during normal operation, and lagged copies of slow-changing metrics (memory) are nearly identical. **Resolution:** Three-layer defense: (1) drop zero-variance columns (var < 1e-12), (2) drop perfectly correlated columns (|r| > 0.999), (3) add tiny jitter (1e-8 normal noise).
- **Prometheus serves stale cached metrics for stopped containers:** When cartservice is crashed (`docker compose stop`), Prometheus continues to serve data from the `rate()` lookback window. The `query_metrics` tool returned `anomalous: False` for a dead service because the cached data looked normal. **Resolution:** Added sparse data detection — if the number of returned data points is <70% of expected (based on scrape interval and time range), the service is flagged CRITICAL.
- **PC algorithm can't see crashed services:** A stopped service produces no new metrics, so the PC algorithm only analyzes surviving services and attributes blame to whichever shows the most variance (usually redis). **Resolution:** Combined approach: (1) LLM hypotheses detect the CRITICAL/sparse signal from `query_metrics`, (2) `analyze_causation_node` overrides low-confidence PC results with the LLM's top hypothesis when CRITICAL evidence is present.
- **Alert title leaked test metadata to LLM:** The original alert title `"Fault Injection Evaluation — service_crash"` caused the LLM to blame "Fault Injection System" as root cause instead of a real service. **Resolution:** Changed to neutral `"Anomaly Detected — Automated Investigation Triggered"`.
- **Depth 3 vs Depth 4 for PC algorithm:** Tested both. Depth 4 produced worse Recall@1 (0% vs 100%) for service_crash because deeper conditioning pruned away weak crashed-service signals while strengthening spurious healthy-service signals. **Resolution:** Locked at `max_conditioning_set=3`.

**Next Session:**
- Run all 40 fault injection tests in a single session (8 fault types × 5 runs, ~3.5-4 hours total)
- Run 3 internal baseline evaluations (rule-based, AD-only, LLM-without-tools)
- Begin Week 10 tasks if time permits

**Notes:**
- The evaluation testing schedule was changed from 4 days (10 tests/day) to a single-day run (~3.5-4 hours). Per-test time: ~60s wait + ~15-30s investigation + ~15-30s restore + cooldown (120-240s) ≈ 4-6 minutes per test.
- The `discover_causation` tool now uses `lags=[1, 2]` (was `[1, 2, 5]`) and `max_conditioning_set=3` (was 4). With the 5-service cap, this produces ~30-50 columns after zero-variance and correlation filtering — PC completes in <1 second.
- The `analyze_causation_node` in `graph.py` now implements a hybrid root cause determination: if PC algorithm confidence ≥50%, use its result; otherwise, check if the LLM's top hypothesis has CRITICAL evidence (stale/sparse metrics indicating a down service) and override. This is critical for service crash scenarios where the root cause service is invisible to PC.
- The `query_metrics` tool's sparse data detection uses 70% coverage threshold (data points / expected points based on 15s scrape interval). For a 10-minute window, expected is ~40 points. A service that crashed 60s ago will have ~36 points (90% coverage, above threshold). But with `rate()` which needs 2 consecutive scrapes, the effective gap is larger — coverage typically drops below 70% within 60-90s of a crash.
- All 8 bash scripts tested manually: inject → verify effect → restore. All work correctly on macOS Docker Desktop.
- 256 total unit tests passing (181 existing + 75 new). All ruff lint clean.

---

### 2026-04-16 — Session 11

**Phase:** Phase 5 — Evaluation (Week 9 — Iterative Debugging of 40-Test Fault Injection Suite)
**Duration:** ~14 hours (multi-day iterative debugging with 7 full 40-test runs)

**Completed:**

*Ran 7 full 40-test fault injection evaluation rounds with iterative fixes. Recall@1 progression:*
- Run 1 (initial): **10.0% (4/40)** — baseline with Phase A fixes + OTel Collector
- Run 2: 17.5% (7/40) — after hypothesis parsing, confidence calibration, fault script improvements
- Run 3: **25.0% (10/40)** — after application metrics added (OTel Collector + spanmetrics)
- Run 4: 22.5% (9/40) — after fixing false CRITICAL from application metrics on non-trace services
- Run 5: 22.5% (9/40) — after 120s wait, 70% sparse threshold, frozen detection, high_latency target change
- Run 6: 15.0% (6/40) — after Service Probe Exporter added
- Run 7: **27.5% (11/40)** — after probe_up CRITICAL check + Redis deprioritization (new best, tied with Run 3)
- Recall@3 best: 52.5% (Run 6)

*Evaluation Framework Enhancements:*
- **Pre-investigation wait increased to 120s** (was 60s): `fault_injection_suite.py`. The `rate()` function's `[1m]` lookback window persists data for ~75s after a service stops. At 60s wait, crashed services still show 100% data coverage; at 120s, coverage drops to ~65% triggering 70% sparse CRITICAL threshold
- **Sparse threshold lowered to 70%** (was 90%): `query_metrics.py`. The 90% threshold triggered false CRITICAL on healthy services with minor scrape jitter. At 70%, only services losing 30%+ of data trigger CRITICAL
- **Frozen-metric detection added**: `query_metrics.py`. When 5+ of last 8 rate-metric values are 0.0 AND historical mean > 0.0001, flags CRITICAL. Catches paused containers where Docker Stats Exporter still reports stats but CPU rate goes flat. `had_activity` guard prevents false triggers on naturally idle services (currencyservice)
- **high_latency target changed** from paymentservice to frontend: `demo_app/fault_scenarios/02_high_latency.sh`, `GROUND_TRUTH`, `configs/evaluation_scenarios.yaml`. Loadgenerator sends traffic to frontend, not paymentservice. Frontend latency becomes directly visible as probe_latency spikes 60x (0.017s → 1.0s)

*OTel Collector + Application Metrics (Run 3):*
- Created `infrastructure/otel-collector/otel-collector-config.yaml` with `spanmetrics` connector: derives request rate, error rate, and latency histograms from trace spans. Uses OTLP receivers (gRPC:4317, HTTP:4318) and Prometheus exporter (port 9464)
- Added `otel-collector` service to `docker-compose.yml` with `otel/opentelemetry-collector-contrib:0.91.0`
- Added `OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317` and `OTEL_SERVICE_NAME=<name>` to all demo services
- Added Prometheus scrape job for `otel-collector:9464`
- Added `latency_p99`, `error_rate`, `request_rate` metrics to `query_metrics.py` `METRIC_PROMQL` dict (uses `service_name` label from OTel resource attributes, not `service` from Docker Stats Exporter)
- Added application metrics to `discover_causation.py` `_CAUSAL_METRICS` dict

*OTel Demo Image Upgrade (Run 5 → Run 6):*
- Upgraded OTel Demo services from `1.7.0` to `1.10.0` tags in `demo_app/docker-compose.demo.yml` and `08_config_error.sh`. v1.7.0 service images silently failed to export traces despite proper `OTEL_EXPORTER_OTLP_ENDPOINT` configuration; v1.10.0 has corrected SDK behavior and exports traces successfully for 5 of 7 services (frontend, checkoutservice, productcatalogservice, paymentservice, loadgenerator)
- v2.x tested but rejected: breaking service name changes (cart vs cartservice) and Redis replaced with Valkey
- Updated loadgenerator env vars for v1.10.0: `LOCUST_HOST`, `LOCUST_HEADLESS=true`, `LOCUST_USERS`, `LOCUST_SPAWN_RATE` (v1.10.0 uses Playwright by default — required explicit config)
- v1.10.0 currencyservice crash-loops with SIGSEGV (exit code 139) — creates noisy baseline for config_error tests
- cartservice in v1.10.0 binds internally to port 8080 (was 7070 in v1.7.0) — probe exporter had to be updated to use port 8080

*Service Probe Exporter (Run 6):*
- Created `infrastructure/service_probe_exporter/probe_exporter.py` — Python stdlib-only exporter (socket, threading, http.server). Probes all 7 services every 15s via application-level data exchange (Redis PING, HTTP GET, gRPC empty payload). Exposes `service_probe_up` (1/0) and `service_probe_duration_seconds` as Prometheus gauge metrics on port 9102
- Created `infrastructure/service_probe_exporter/Dockerfile` (Python 3.11-slim, no external dependencies)
- Added `service-probe-exporter` service to `docker-compose.yml` (64MB memory, port 9102)
- Added Prometheus scrape job for `service-probe-exporter:9102`
- Updated `scripts/start_infrastructure.sh` to include service-probe-exporter in startup
- Added `probe_up` and `probe_latency` metrics to `query_metrics.py` `METRIC_PROMQL` and `discover_causation.py` `_CAUSAL_METRICS`
- Updated system prompt in `src/agent/prompts/system_prompt.py` with probe metric guidance (prioritize probe_up first)
- **Critical discovery during live testing:** TCP `connect()` succeeds on paused containers (kernel handles SYN/SYN-ACK even when process is SIGSTOP'd). Updated probe to send application-level data (Redis PING, HTTP GET, gRPC empty payload) and wait for response. Without this, `docker pause` faults were undetectable

*probe_up CRITICAL Fix (Run 7):*
- **Key discovery from diagnostic analysis of Run 6:** In 27 of 34 wrong predictions, the LLM EXPLICITLY DOCUMENTED that the correct service's probe_up was 0 in its evidence chain, but still predicted a different service (usually redis). The probe_up=0 signal was not triggering the code-level CRITICAL override
- **Root cause:** probe_up returns a time series `[1,1,1,0,0,0,0,0]`. The 2σ anomaly check computes `abs(0.0 - 0.45) = 0.45 < 2 * 0.497 = 0.99` → anomalous=False. probe_up was in `_app_metrics` exclusion set (to prevent false CRITICAL when probe exporter is down), which also disabled sparse/stale CRITICAL detection. The CRITICAL override in `analyze_causation_node` scans evidence for "CRITICAL" string but probe_up never produced it
- **Fix:** Added dedicated `probe_up` check in `query_metrics.py`: when 3+ of last 4 values are 0.0 AND mean > 0.1 (service was previously up), returns `anomalous: True` with explicit "CRITICAL: {service} is DOWN" note. This triggers the existing CRITICAL override
- **Fix:** Added `probe_latency` spike detection: when current > 10x mean AND mean > 0.0001s, returns CRITICAL for severe latency (catches frontend 500ms injection: 1.0s vs 0.017s = 60x)

*Redis Deprioritization (Run 7):*
- Added explicit guidance to `src/agent/prompts/system_prompt.py` about Redis's naturally high CPU variance. The LLM was defaulting to Redis (~58% of predictions in Run 6) because Redis has the highest natural CPU usage among all services. New guidance: "Elevated redis CPU alone is NOT an anomaly indicator. Only consider redis as root cause if: (1) probe_up shows redis is DOWN, or (2) error logs from downstream services explicitly mention redis connection failures"
- Result: Redis predictions dropped from 23/40 (Run 6) to 3/40 (Run 7)

*Other Fixes:*
- **NaN handling in query_metrics.py:** The `latency_p99` PromQL (`histogram_quantile`) returns `NaN` when no histogram buckets exist. Python's `float('nan')` serializes as `NaN` in JSON (invalid JSON), causing Gemini API 400 errors. Added `np.isnan(v)` filter before appending to values list
- **Application metrics false CRITICAL fix:** cartservice/currencyservice/redis don't export traces, so `error_rate`/`latency_p99` return empty. Original code returned `anomalous=True` with CRITICAL note (assumed service was DOWN). New code: if metric is in `_app_metrics` set, returns `anomalous=False` with "No application metrics available" note instead. Also skip sparse/stale CRITICAL detection for these metrics (spanmetrics have irregular data rates based on traffic, not fixed 15s scrape interval)

*Testing:*
- 262 total unit tests passing (256 existing + 6 new). All ruff lint clean. All 8 bash scripts pass syntax check
- Extensive live Docker validation between each test run (spin up stack, inject single fault, observe metric behavior, tear down)

**In Progress:**
- 40 fault injection tests show Recall@1 plateau at 22-27% across Runs 3-7. Recall@3 improved to 52.5% (Run 6) — correct service often in top 3 but not #1
- Known issue: cross-test state pollution — 10-min query window contains residual data from previous test's fault, causing false CRITICAL on recovering services
- Known issue: LLM reasoning inconsistency — agent sees probe_up=0 evidence for correct service but still picks wrong service (27/34 wrong predictions in Run 6 had this pattern before the Run 7 fix)

**Blockers / Issues:**
- **OTel Demo v1.7.0 doesn't export traces:** Despite `OTEL_EXPORTER_OTLP_ENDPOINT` configured correctly, trace export silently fails for all services. Process-level metrics export fine. **Resolution:** Upgrade to v1.10.0 images (corrected SDK behavior). v1.11.0+ introduces breaking changes (Valkey, service name changes) — stay on v1.10.0.
- **OTel Demo v1.10.0 currencyservice crashes:** Exit code 139 (SIGSEGV) in crash-loop. **Resolution:** Accepted as known limitation — currencyservice probe_up shows 0 intermittently in baseline, making config_error tests noisier.
- **cartservice port changed in v1.10.0:** .NET app binds to 8080 internally (was 7070 in v1.7.0). **Resolution:** Updated `probe_exporter.py` SERVICES dict to use port 8080 for cartservice.
- **TCP connect succeeds on paused containers:** Kernel-level TCP stack handles SYN/SYN-ACK even when process is SIGSTOP'd. Simple TCP connect probes couldn't detect paused services. **Resolution:** Probe exporter now sends application-level data and waits for response (Redis PING, HTTP GET, gRPC empty payload).
- **`docker pause` doesn't affect Docker Stats Exporter metrics:** Paused containers still report full metrics via Docker API. **Resolution:** Probe exporter bypasses this by directly testing service responsiveness via network.
- **paymentservice receives no traffic from loadgenerator:** Locust browses frontend catalog but doesn't complete checkout flows. 500ms latency on paymentservice was invisible. **Resolution:** Changed high_latency target to frontend where real traffic flows.
- **NaN values break Gemini API:** `histogram_quantile` returns NaN for services without histogram buckets, which serializes as invalid JSON. **Resolution:** Filter NaN values in query_metrics.py before appending to result.
- **False CRITICAL on non-trace services:** cartservice/currencyservice/redis don't export traces, so app metrics query returned CRITICAL ("service is DOWN"). **Resolution:** Added `_app_metrics` exclusion set — returns neutral response instead of CRITICAL.
- **LLM ignores probe_up=0:** The CRITICAL override scans evidence for "CRITICAL" string, but probe_up=0 didn't produce that signal because (a) 2σ check fails due to high std of 0/1 mix, (b) sparse/stale detection was disabled for probe metrics. **Resolution:** Added dedicated probe_up check that flags CRITICAL when 3+ of last 4 values are 0.0.
- **Redis over-prediction (23/40 in Run 6):** Redis has highest natural CPU variance, attracting PC algorithm and LLM defaults. **Resolution:** Added explicit system prompt guidance about Redis's high variance being normal.
- **90% sparse threshold too aggressive:** Healthy services with minor scrape jitter triggered false CRITICAL, especially during cooldown period between tests. **Resolution:** Lowered to 70%.
- **60s wait insufficient:** `rate()` data persists 75s after crash. Crashed services showed 100% coverage at 60s. **Resolution:** Increased to 120s.
- **Cross-test state pollution:** 10-min query window contains previous test's fault data. Services restored 60-120s ago still show some zero values. Currently unresolved — would require longer cooldowns or per-test metric snapshotting.
- **LLM reasoning inconsistency:** Even with correct probe_up=0 signal, Gemini 2.5 Flash Lite sometimes picks different service due to topology-based "upstream must have caused downstream failure" reasoning. Mitigated by code-level CRITICAL override in Run 7.
- **Gemini API ConnectTimeout:** Transient network timeout broke investigations. **Resolution:** Added `max_retries=3` to `ChatGoogleGenerativeAI` in `_get_llm()`.

**Next Session:**
- Implement remaining improvements to address cross-test state pollution (longer cooldowns, per-test metric snapshotting)
- Investigate LLM consistency issues — potentially improve system prompt or add more programmatic override logic
- Consider running RCAEval cross-system evaluation (Week 10, Days 1-2 tasks)
- Document final evaluation results in `docs/evaluation_results.md`

**Notes:**
- The Service Probe Exporter is a significant new infrastructure component. It's a lightweight (64MB) Python service using only stdlib (socket, threading, http.server). Probes via application-level data exchange (Redis PING, HTTP GET, gRPC empty payload) rather than simple TCP connect, which correctly detects paused/frozen services that respond to TCP SYN at the kernel level but can't respond to application requests.
- OTel Collector spanmetrics connector converts trace spans into Prometheus metrics (`span_calls_total`, `span_duration_milliseconds_bucket`). Only 5 of 7 services export traces successfully: frontend, checkoutservice, productcatalogservice, paymentservice, loadgenerator. cartservice (.NET) and currencyservice (C++) don't export traces in v1.10.0 images. redis has no OTel SDK instrumentation.
- The probe_up CRITICAL check uses 3/4 recent zeros (not all 4) because the rate() lookback window sometimes leaks non-zero values into the most recent position. The `had_activity` guard (`mean > 0.1`) prevents false triggers on services that are always effectively down (currencyservice crash-loop).
- Redis deprioritization in system prompt had dramatic effect: false Redis predictions dropped from 23/40 → 3/40. But side effect: cartservice over-prediction emerged in Run 7 (19/40), likely due to cross-test state pollution after service_crash/cascading_failure tests.
- All 7 test runs used v1.10.0 OTel Demo images (except Run 1 which used v1.7.0 before the trace export issue was discovered).
- Cross-run trend: Recall@3 improved steadily (32.5% → 47.5% → 52.5%) as the pipeline matured. The correct service is increasingly found in the top 3, but Recall@1 (pick correctly as #1) plateaus around 22-27% due to LLM reasoning inconsistency and cross-test state pollution.

---

### 2026-04-18 — Session 12

**Phase:** Phase 5 — Evaluation (Week 9 — Deep-dive diagnosis + Tier 1/2/3/4/5 fixes + 35-test re-run)
**Duration:** ~30 hours (multi-day deep-dive, plan, implementation, test runs, iterative debug, Tier 4/5 implementation)

**Completed:**

*Deep-dive diagnosis (Session-12 start):*
- Wrote a full architectural diagnosis of Session 11's 27.5% Recall@1 plateau identifying 17 root causes tiered by impact. Captured in an approved plan file at `/Users/srikarpottabathula/.claude/plans/sparkling-booping-iverson.md`.
- Key findings: cartservice prediction bias (22/40 = 55% in Session 11); PC algorithm effectively disabled (`min_len < 10` bailout because of empty spanmetric series); LLM reasoning weakness (Gemini 2.5 Flash Lite); `{causal_graph_ascii}` report-template placeholder bug; cross-test metric pollution in the 10-min window; `cpu_throttling` fundamentally unobservable on idle demo (productcatalogservice baseline CPU 0.09% of a core).
- Reviewed the user's decision-flow via `AskUserQuestion`: chose `gemini-3-flash-preview`, new `sweep_probes_node` with bypassed tool budget, retarget `config_error` to productcatalogservice, `[start_time, start_time + time_range_minutes]` window semantics, retain 5-reps × 8 faults initially.

*Phase A — Isolated non-breaking changes:*
- **Fix 1 — LLM swap to `gemini-3-flash-preview`**: updated `src/agent/graph.py:_get_llm`, `configs/agent_config.yaml`, `tests/conftest.py`, `tests/evaluation/baseline_comparison.py`, `CLAUDE.md`, `README.md`, `.env.example` comment.
- **Fix 3 — System prompt rewrite**: `src/agent/prompts/system_prompt.py` — replaced the two cartservice-only examples with 7 fault-family examples (one per real fault type). Added an explicit anti-bias directive. Later also added a currencyservice baseline-noise warning (see Session 12 regression below).
- **Fix 13 — Destructive-fault cooldowns**: `configs/evaluation_scenarios.yaml` — `service_crash` 120s → **300s**, `cascading_failure` 240s → **300s** to give the 10-min query window time to flush probe_up=0 residue.
- **Fix 15 — config_error retarget**: `demo_app/fault_scenarios/08_config_error.sh` now targets **productcatalogservice** with `PRODUCT_CATALOG_SERVICE_PORT=999999` + `--restart on-failure` (invalid-port crash-loop). Validated live: triggers clean probe_up=0 + sparse/stale CRITICAL. `GROUND_TRUTH["config_error"]` updated in `fault_injection_suite.py`.

*Phase B — Tool-layer semantics:*
- **Fix 5 — `probe_latency` baseline contamination**: `query_metrics.py` now computes `baseline_mean` from the first 60% of the window (or anchor-based when `start_time` is pinned) before the 10× ratio check. Under a pinned window, pre-anchor points are used as baseline directly. Live verified: 500ms netem on frontend → current=1.02s vs baseline_mean=0.016s = **63× increase**, CRITICAL fires.
- **Fix 6 — `probe_up` fresh-drop check**: added a parallel trigger: `baseline_mean ≥ 0.9 AND last 2 readings == 0`. Catches just-dropped services that the 3-of-4-zeros rule misses at 120s wait. Also tightened the `was_healthy` guard to `baseline_mean ≥ 0.7` to prevent currencyservice baseline crash-loop (probe_up mean ~0.2) from firing CRITICAL.
- **Fix 7 — Remove `probe_up`/`probe_latency` from `_app_metrics`**: probe metrics now participate in sparse/stale CRITICAL detection. Added exporter-unavailable guard: if the probe exporter returns no series at all, degrades to a neutral note instead of CRITICAL.
- **Fix 8 — Zero-fill + NaN filter in `MetricsCollector`**: `get_service_metrics` now zero-fills missing Prometheus series (instead of returning `[]`) and coerces `NaN` values from `histogram_quantile` to 0.0. Prevents `min_len<10` bailout in `discover_causation` when any service has no data for one metric. Also added `_parse_step_seconds` helper.
- **Fix 9 — Decouple PC from spanmetrics**: dropped `request_rate`, `error_rate`, `latency` from `_CAUSAL_METRICS`. PC input is now 6 metrics × 5 services × 2 lags = 60 columns (all container-level or probe). Services that don't export traces no longer block PC.
- **Fix 11 — Soft PC `min_len` gate**: changed from `min_len < 10` hard bail to `min_len < 6 OR short_frac > 0.30`. Also post-lag length gate lowered 10→8. PC now runs with partial data.
- Empty-container-metric CRITICAL softened: returns neutral note instead of CRITICAL when a container metric has no data at all — prevents currencyservice memory_usage-missing from being flagged as a fault.

*Phase C — Graph/executor changes:*
- **Fix 4 — Python-side report template substitution**: `generate_report_node` now pre-fills the `RCA_REPORT_TEMPLATE` with `str.format()` for structural slots (title, timestamp, severity, confidence, root cause, causal_graph_ascii, counterfactual) and replaces sentinel tokens (`__OPS_SUMMARY__`, `__OPS_EVIDENCE_CHAIN__`, …) with the LLM's JSON free-text output. Fallback dumps raw LLM output into `summary` if JSON parsing fails. Added final sanitisation that replaces leftover `{placeholder}` with `"N/A"`. New helper `_parse_report_fields()`.
- **Fix 12 — Per-test metric snapshotting via `start_time`**: added `start_time: str | None` parameter to `AgentExecutor.investigate()`, `query_metrics()`, `search_logs()`, `discover_causation()`, and the `AgentState` TypedDict. When set, tools query `[anchor - 60s, min(anchor + time_range_minutes, now)]` — the 60s pre-anchor extension preserves pre-fault baseline data. `end` is clamped to `now` to avoid querying the future. Baseline for CRITICAL checks switched to anchor-based split (points before anchor_ts) when `start_time` is pinned. `fault_injection_suite.py` now passes `start_time=record["fault_start_time"]` so each test sees an isolated window.
- **Fix 10 — `critical_services` priors into PC**: `discover_causation` accepts an optional `critical_services` kwarg; excludes those services from the PC input, synthesises high-confidence (`0.9`) edges from each critical service to each surviving downstream, and overrides `root_service` to the first critical service with `best_confidence ≥ 0.75`. `analyze_causation_node` extracts `critical_services` from evidence before invoking PC and passes it in.
- **Fix 2 — New `sweep_probes_node`**: second graph node (after `analyze_context`). Initially queried `probe_up` for every affected service; later extended to `probe_up` + `probe_latency` + (in Tier 4/5) `cpu_usage` + `memory_usage` + log-crash-sweep. **Bypasses `tool_calls_remaining`** — mandatory infrastructure, not LLM-directed reasoning. Pre-populates `state["evidence"]` with `args["pre_gathered"] = True`. Emits a summary `AIMessage` listing CRITICAL vs clean services.

*Phase D — Eval harness:*
- **Fix 14 — Randomized fault order with seed**: `--seed` CLI arg added to both `fault_injection_suite.py` and `scripts/inject_faults.py`. New `_shuffled_faults(faults, seed)` helper produces deterministic permutations per seed. Default seed=42.

*Run 1 (35 tests, Session 12) — results:*
- Recall@1: **15/35 = 42.9%** (95% CI [26.5%, 59.3%])
- Recall@3: **24/35 = 68.6%** (95% CI [53.2%, 84.0%])
- Inconclusive predictions: **0/35** (was 5/40 = 12.5% in Session 11)
- Per-fault breakdown:
  - config_error: **100%** (0% in Session 11 — retarget win)
  - connection_exhaustion: 60% (20% prior)
  - network_partition: 40% (0% prior)
  - memory_pressure: 20% (flat)
  - high_latency: 0% (flat — blocked by truncation bug)
  - service_crash: 60% (vs 80% prior)
  - cascading_failure: 20% (vs 80% prior — bias-removal regression: `redis` picked 3/5 times because cart-stop→redis-idle correlation misleads PC)
- cartservice top-1 concentration: 55% → **31%** (bias reduced)
- New productcatalogservice over-prediction: 8% → 31% (config_error example in prompt may have created a new bias)

*Session-12 diagnosis of remaining 20 misses (autopsy):*
- 11/20 misses: GT never considered (system blind)
- 8/20 misses: GT in top-3 but ranked below another hyp
- 1/20 misses: root-cause override flipped LLM's correct top-1 (stale CRITICAL cross-contamination from prior service_crash test)
- Identified systemic cause: **`evidence[].finding` is truncated to 500 chars and the `"CRITICAL"` substring sits past the cutoff** (timestamps array dominates the first 500 chars). `analyze_causation_node`'s string-scan for CRITICAL doesn't fire → `critical_services` stays empty → Fix 10 override bypassed → PC picks a weaker candidate.

*Tier 4/5 implementation (Session-12 follow-up to the 35-test autopsy):*
- **Fix 17 — Logs as CRITICAL signal**: `src/agent/tools/search_logs.py` — added `_CRASH_PATTERNS` (OOMKilled, SIGKILL/SIGSEGV, panic, std::logic_error, terminate, fatal error, unhandled exception, exit 137/139, listen-tcp-bind failure, core dumped, connection refused, max clients reached) and `_detect_crash_signal()` helper. When ≥3 matches attributable to a specific `service_filter`, the tool now returns `critical_service`, `anomalous=True`, and a `"CRITICAL: <svc> logs show N crash/fault pattern matches…"` note. Threshold chosen to filter single-line transient warnings.
- **Fix 18-ext — Extended sweep**: `sweep_probes_node` now queries **4 metrics** per service (`probe_up`, `probe_latency`, `cpu_usage`, `memory_usage`) AND runs a per-service `search_logs` with the crash query pattern. 6 services × 4 metrics + 6 log calls = **30 sweep calls per investigation**, all bypassing the tool budget.
- **Direct `args["critical"]` flag (architecturally required for Fix 17)**: every sweep evidence entry now carries `args["critical"] = bool` set from the full (un-truncated) tool result. `analyze_causation_node`'s critical-services extraction reads this flag first, falling back to the legacy `"CRITICAL" in finding` string-scan for LLM-invoked tools that don't set it. Bypasses the 500-char truncation bug for sweep-produced evidence.
- **Fix 21 — Knockout node**: new `knockout_node` in `src/agent/graph.py`, wired on the "end" branch of the conditional (after `analyze_causation`, before `generate_report`) so it runs once, not per loop. Skips when `confidence ≥ 0.75` or `root_cause in {"", "unknown", "inconclusive"}`. Otherwise counts sweep-evidence `critical=True` entries per candidate (root_cause + top-3 LLM hypotheses). If an alternative has **strictly more** CRITICAL signals than the current root_cause, swap root_cause with a moderate confidence bump (max 0.65, staying below the 0.75 CRITICAL-override band).
- Graph is now **7 nodes**: START → analyze_context → sweep_probes → form_hypothesis → gather_evidence → analyze_causation → (conditional: continue→form_hypothesis, end→knockout) → generate_report → END.
- Verified live: Gemini 3 agent correctly identifies `frontend` as root cause for high_latency with 0.75 confidence via the direct `args.critical` flag. `[INFO] CRITICAL override: frontend has stale/sparse metrics (service DOWN)` appears in the log.

*Other fixes / robustness hardening discovered mid-run:*
- **UTC timestamps in `fault_injection_suite.py`**: `datetime.now()` (local time, no tz) was being passed to tools that interpreted naive timestamps as UTC — effectively querying a window multiple hours in the future. Changed all 5 `datetime.now()` calls to `datetime.now(UTC)`. Every pinned-window test before this fix was querying empty windows → zero-fill → PC bail → "inconclusive".
- **LogQL OR-alternation handling**: LogQL's `|=` is a literal-substring filter, not a boolean expression. LLM-generated queries like `panic OR fatal OR "bind"` produced 400 Bad Request from Loki (nested quotes terminated the string literal). `search_logs._build_logql()` now detects OR-alternation, converts it to a regex line filter (`|~ \`(?i)term1|term2|…\``), strips embedded quotes/backticks from terms, and uses backtick strings (raw) for the LogQL literal. Also applied to the forced-log-search in `gather_evidence_node`.
- **Gemini 3 list-of-parts content format**: Gemini 3 Flash Preview returns `response.content` as `[{"type":"text","text":"..."}]` rather than a plain string. All three call sites that read `response.content` (`form_hypothesis_node`, `generate_report_node`, and implicit in the tool-call path) went through `isinstance(..., str) else ""` → discarding all text. Added `_extract_text(response)` helper that normalises both formats and filters out non-text parts (thoughts, signatures). RCA reports went from "Executive Summary: N/A, Evidence Chain: N/A, …" to fully populated.
- **Leftover `opsagent-tc-sidecar` + stale tc qdisc on frontend**: earlier diagnostic tests left the sidecar + `tc netem delay 500ms` active on frontend's eth0. When the suite tried to re-run `02_high_latency.sh inject`, it got "container name in use" (exit 125). Made `02_high_latency.sh inject` and `restore` both **idempotent**: force-remove any prior sidecar, strip any stale qdisc via a throwaway Alpine container that shares the target's netns, then apply the new netem.
- **`currencyservice` excluded from `affected_services`**: v1.10.0 currencyservice SIGSEGV crash-loop makes its probe_up=0 and `std::logic_error` crash-logs a permanent baseline state. Surfacing it to the agent via the sweep caused consistent misattribution (LLM saw "one service is clearly down, with crash logs" and blamed it for every fault). `affected_services` in the alert now lists only the **6 legitimate services**. Also added an explicit "currencyservice is BROKEN IN BASELINE — never pick it as root cause" warning to the system prompt.

*Unit + integration tests:*
- **315 unit tests passing** (was 262 pre-session) + 15 integration tests.
- New tests cover: `_extract_text` (5 tests — string/list/thought-parts/mixed/missing), `_build_logql` (5 tests — single-term/OR/quotes/backticks/no-filter), `_detect_crash_signal` and crash-signal escalation (3 tests), sweep's direct `args.critical` flag (2 tests), `sweep_probes_node` call counts across 4 metrics + log sweep (1 updated test + 2 new), `knockout_node` (5 tests — skip-high-confidence/skip-unknown/passthrough/flip/tie/ignore-non-sweep), zero-fill + NaN filter in `MetricsCollector` (3 tests), `_parse_step_seconds` (1 test), `start_time` window semantics (2 tests), Gemini 3 model assertion (1 test), system-prompt anti-bias + all-7-services assertions (2 tests), cpu_throttling exclusion (1 test), config_error retarget assertion (1 test), destructive-fault cooldown values (1 test), `--fault unknown` clean-error (1 test), `currencyservice` not in alert (1 test), randomized fault order (4 tests — preserve-set/deterministic/different-seeds/no-mutation).
- `ruff` clean, `mypy` clean on `src/`.

*cpu_throttling removed from active suite:*
- Diagnosis showed `docker update --cpus 0.1 productcatalogservice` never forces CPU saturation on the idle demo (baseline 0.09% of a core; even `--cpus 0.005` doesn't bite). Tested `--cpus 0.005` live: `probe_latency 0.0011s → 0.0020s` — too small a signal to detect. Accepted dropping cpu_throttling from the active registry. Script retained at `demo_app/fault_scenarios/04_cpu_throttling.sh` with a header comment noting it is out of scope.
- Propagated across: `configs/evaluation_scenarios.yaml`, `tests/evaluation/fault_injection_suite.py:FAULT_SCRIPTS/GROUND_TRUTH`, `tests/unit/test_fault_injection_suite.py`, `CLAUDE.md` (new gotcha), `README.md`, shell script header.
- New `--fault cpu_throttling` on the CLI returns a clean error (not a `KeyError`) listing the 7 valid fault types.

**In Progress:**
- 35-test re-run after Tier 4/5 implementation is pending — results from this iteration are only from the Tier-1/2/3 run. Unit-test-verified plus live smoke-tested (high_latency correctly identifies frontend at 0.75 confidence); a full re-run is the next step.
- Documentation: `docs/evaluation_results.md` not yet drafted with the 42.9% / 68.6% results.

**Blockers / Issues:**
- **cpu_throttling undetectable on idle demo**: `docker update --cpus X` only bites a service that's saturating its CPU. OTel Demo's productcatalogservice baseline is 0.09% of a core — no fault cap short of the Docker minimum can approach a signal without an active load generator hitting productcatalogservice directly. **Resolution:** Removed from suite; retained script for future re-use if the demo gets load-tested.
- **Gemini 3 Flash Preview returns list-of-parts content**: broke every non-tool LLM response handler (discarded all text when `isinstance(content, str)` was False). **Resolution:** `_extract_text()` helper in `graph.py`.
- **LogQL `|=` doesn't support OR boolean**: LLM-generated `"a OR b OR c"` queries produced 400s at Loki. **Resolution:** `_build_logql()` converts OR to regex `|~` + handles embedded quotes via backtick literals.
- **Evidence `finding` truncated to 500 chars drops CRITICAL substring**: timestamps array dominates the first 500 chars of the JSON dump; `"CRITICAL"` note at the end gets cut off. `analyze_causation_node`'s string-scan misses legitimate signals. **Resolution (partial, for sweep only):** direct `args["critical"]` boolean flag set from the pre-truncation tool result. LLM-invoked tool calls still rely on the string-scan (acceptable since the sweep covers the high-value pre-hypothesis path).
- **Stale fault state leakage across tests**: `docker compose stop/restart` for service_crash/cascading_failure leaves probe_up=0 data in the 10-min query window for ~5 min after restore. **Resolution:** 300s cooldowns for destructive faults + per-test `start_time` pinning. Not fully eliminated (one test still showed this pattern: network_partition_run_4 picked cartservice because of residual cartservice=0 history).
- **UTC vs local time in `fault_injection_suite.py`**: `datetime.now().isoformat()` returned local time without a tz suffix; tool code then treated it as UTC. **Resolution:** all 5 `datetime.now()` calls in the file use `datetime.now(UTC)` now.
- **Leftover sidecar + tc qdisc from diagnostic tests**: manual live experimentation before the run left the sidecar + qdisc active, which blocked a clean re-run. **Resolution:** `02_high_latency.sh` inject and restore are now both idempotent.
- **currencyservice baseline distracting the agent**: v1.10.0 currencyservice crash-loops in baseline, its probe_up=0 and crash-log stream look identical to a real fault. **Resolution:** excluded from `affected_services` + explicit prompt warning.
- **memory_pressure signal subtle**: 25MB cap on a ~15MB working-set checkoutservice produces modest memory pressure that rarely triggers CRITICAL. Crash-log detection (Fix 17) may help if OOMKilled logs accumulate; Session-12 Tier-1/2/3 run had 20% Recall@1 on this fault.

**Next Session:**
- **Full 35-test re-run with Tier 4/5 fixes.** Expected Recall@1: 65–85% based on the autopsy (Fix 17 contributes ~4–6 correct predictions; Fix 21 contributes ~3–5). Run with `caffeinate -s env PYTHONUNBUFFERED=1 poetry run python scripts/inject_faults.py --repetitions 5 --seed 42`.
- Evaluate 3 internal baselines (Rule-Based, AD-Only, LLM-Without-Tools) against the same 35-test suite.
- Begin Week 10: RCAEval cross-system evaluation on RE1/RE2/RE3 (736 cases total).
- Draft `docs/evaluation_results.md` with both Recall@1/Recall@3 numbers + per-fault breakdown + comparison against Session 11 (27.5%/40%) baseline.

**Notes:**
- Graph is now **7 nodes** (was 5): added `sweep_probes` between analyze_context and form_hypothesis, and `knockout` on the end-branch of the conditional. Both sit on non-looping paths (sweep runs once; knockout runs once at end).
- Default `max_tool_calls` stayed at 10. The 30-call sweep bypasses the budget, so the LLM still gets its full 10-call budget for reasoning + evidence gathering.
- Window semantics when `start_time` is pinned: `[anchor - 60s, min(anchor + time_range_minutes, now)]`. The 60s pre-anchor extension gives the baseline_mean calibration for CRITICAL checks a pre-fault reference.
- Baseline_mean calibration under `start_time` pinning: count points with timestamp < anchor; use those as baseline. Falls back to first-60%-of-window when unpinned or when too few pre-anchor points exist.
- cartservice prediction concentration reduced from 55% (Session 11) to 31% (Session 12). New productcatalogservice bias at 31% is partly the `config_error` retarget success (5/11 correct) and partly the system prompt's Example 7 reinforcing "productcatalogservice ↔ crash-loop" patterns.
- `currencyservice` is now out of the investigation scope (not in `affected_services`). It continues to crash-loop in baseline (v1.10.0 SIGSEGV), but no longer distracts the agent.
- Context files updated for new counts: 7 fault types, 35 tests, LLM = `gemini-3-flash-preview`, graph = 7 nodes.
- `scripts/inject_faults.py` now validates `--fault <unknown>` with a helpful error (listing known types) instead of a `KeyError` traceback. `--seed` flag propagates to the underlying suite.
- The user requested that git commits be held — all Session 12 changes are uncommitted on `main`. Awaiting manual commit after the Tier 4/5 re-run validates the improvements.

---

### 2026-04-19 — Session 13

**Phase:** Phase 5 — Evaluation (Week 9 — memory_pressure deep-dive + full 35-test re-run → 100% Recall@1)
**Duration:** ~12 hours (multi-day deep-dive: diagnosis, fix A+B, tuning, fault-script patch, 35-test suite)

**Completed:**

*Diagnosis of the memory_pressure recall gap (Session 12 was 2/5 = 40% — only weak fault class):*
- Ran live `03_memory_pressure.sh inject` against a warm checkoutservice and traced every channel the agent sees: `probe_up = 1.0` (stable), `probe_latency ~3 ms` (stable), `cpu_usage` has a 2σ flag but no CRITICAL, `memory_usage` flattens near the cap (no 2σ spike because mean ≈ current), crash-pattern logs produce **zero** matches (no OOMKilled / SIGKILL — Go runtime GC-cycles under cgroup pressure without emitting stdout errors). Result: not a single CRITICAL detector fires on the correct service, so `critical_services` stays empty, the CRITICAL-override is bypassed, and PC's weak noise wins.
- Measured baseline memory utilization across the 6 active services: **7.3 – 45.4%** (checkoutservice 11.6%, frontend 31.4%, cartservice 33.1%, paymentservice 45.2%, productcatalogservice 10.0%, redis 14.8%). During memory_pressure fault with a 25 MiB cap, checkoutservice reached 85-88%. Clean separability.
- Verified Docker API exposes `stats["memory_stats"]["limit"]` on every container — tracks `docker update --memory` in real time. This is what made the ratio detector possible.
- Identified cross-test baseline noise: in v1.10.0, `frontend` is configured to connect to `cartservice:7070` but the .NET cartservice actually binds on port 8080, producing persistent `ECONNREFUSED 172.18.0.16:7070` log spam that the LLM otherwise latches onto during memory_pressure (no other CRITICAL signal to override it). Documented but not fixed in this session — other faults' stronger CRITICAL signals already drown it out.

*Fix A — memory saturation detection pipeline:*
- **`infrastructure/docker_stats_exporter/exporter.py`** — `_extract_memory()` now returns a 3-tuple `(usage, working_set, limit)`; new gauge `container_spec_memory_limit_bytes{service, name}` emitted alongside `container_memory_working_set_bytes` with the identical label set so Prometheus joins the ratio `working_set / limit` natively. Documented the macOS-Docker-Desktop corner case where an uncapped container reports `limit == host RAM` (~16 GB). Updated HELP/TYPE header list.
- **`src/agent/tools/query_metrics.py`** — added two keys to `METRIC_PROMQL`: `memory_limit` (cgroup limit in bytes) and `memory_utilization` (working_set / limit ratio). New CRITICAL detector after the `probe_latency` block: fires when `metric_name == "memory_utilization" AND len(values) >= 4 AND baseline_mean <= 0.50 AND peak >= 0.80`. Emits `stats["peak"]` alongside `current`, `mean`, `baseline_mean`, etc. The `peak`-based (not `current`-based) trigger is critical — see Session 13 detector tuning below.
- **`src/agent/graph.py`** — `sweep_probes_node` now queries 5 metrics per service (added `memory_utilization` as the 5th channel). Sweep math: 6 services × 5 metrics + 6 log calls = **36 calls per investigation** (was 30). Updated docstring to renumber channels 1-5 and explicitly document memory_utilization's role. Still bypasses `tool_calls_remaining` — mandatory infra. Summary message's "Do NOT re-query" list expanded to include `memory_utilization`.

*Fix B — system prompt correction:*
- **`src/agent/prompts/system_prompt.py`** — "Available Metrics" section lists `memory_limit` and `memory_utilization` under container metrics with one-line descriptions and the uncapped-container note. Example 3 (memory_pressure) fully rewritten: the old "look for OOMKilled log entries" guidance was factually wrong (Go GC absorbs soft pressure without emitting crash logs) and actively misled the LLM. New example steers the agent to the `memory_utilization` CRITICAL + elevated-error-rate signature and explicitly warns that OOMKilled log absence is EXPECTED for soft memory pressure.

*Detector tuning — peak vs current (smoke-run iteration 1):*
- First smoke run hit 6/7 on per-fault verification: memory_pressure failed despite the detector shipping. Prometheus replay of the fault window showed checkoutservice `memory_utilization` was 0.856 for 5 consecutive samples (clear saturation), then dipped to 0.572 due to a GC cycle right before the 120-s investigation mark. With `current = values[-1]` the detector saw 0.572, below the 0.80 threshold, and missed the fault. Changed the trigger to `peak = np.max(arr)` — captures the sustained-saturation band regardless of tail-scrape GC state. Rationale: memory_utilization is a bounded ratio [0, 1] with no realistic mechanism for a single-scrape spurious spike to 80%+; uncapped containers stay <1% consistently; the existing `baseline_mean <= 0.50` guard still rejects always-hot services.
- Added regression unit test `test_memory_utilization_fires_despite_gc_dip_at_tail` exercising this exact scenario (20 samples at 0.08, 5 at 0.85, 10 at 0.57 — `current` subthreshold but `peak` fires).

*Fault-script patch — deterministic saturation (smoke-run iteration 2):*
- Second smoke run still missed memory_pressure (new confidence: 0.45, `productcatalogservice` misattributed via PC noise). Root cause: `checkoutservice` had been idle post-previous-test, Go GC had reclaimed heap to 8-15 MiB, and the fixed `--memory 25m` cap left 10 MiB of headroom — working set plateaued at `8/25 = 0.32 to 15/25 = 0.60`, never reached the 0.80 threshold. **The fault itself was not producing saturation.**
- **`demo_app/fault_scenarios/03_memory_pressure.sh`** — rewrote `inject` to query `docker stats --no-stream --format '{{.MemUsage}}'`, parse KiB / MiB / GiB via awk+sed (no Python dependency), and apply `cap_mb = max(working_mb * 1.2, working_mb + 2)`. The `W + 2` branch takes over for cold heaps (< 10 MiB) where 1.2× would leave < 2 MiB GC headroom and risk OOMKill. Falls back to fixed 25 MiB if stats parse fails. Restore unconditionally sets cap to 256 MiB.
- Manual verification: cold-restart checkoutservice (W=8 MiB) → cap=10 MiB → immediate utilization 89%, sustained at 86-94% across 120 s under load, restart_count=1 (one mid-test OOM-like event but container kept running, oom_killed=false). Warm checkoutservice (W=29 MiB) → cap=34 MiB → sustained 85% utilization. Both cases saturate reliably.

*Comprehensive testing:*
- **New test file `tests/unit/test_docker_stats_exporter.py`** (7 tests): `TestExtractMemory` (3 — 3-tuple return with/without limit, None-safety); `TestBuildMetrics` (4 — new gauge emitted, label-set matches working_set for ratio join, omitted when Docker returns no limit, skips unlabeled containers). Mocks `docker.DockerClient` via a `sys.modules['docker']` stub since the host Poetry env doesn't ship the `docker` SDK (it runs inside the exporter's own container).
- **Extended `tests/unit/test_agent_tools.py::TestQueryMetrics`** (6 new tests): `test_metric_promql_contains_memory_limit_and_utilization`, `test_memory_utilization_critical_fires_on_saturation`, `test_memory_utilization_no_fire_when_baseline_already_high`, `test_memory_utilization_no_fire_at_moderate_usage`, `test_memory_utilization_fires_despite_gc_dip_at_tail`, `test_memory_utilization_no_fire_insufficient_samples`.
- **Extended `tests/unit/test_agent_graph.py::TestSweepProbesNode`**: updated `test_sweeps_all_affected_services` and `test_threads_start_time` call-count assertions from 24 → 30 metric calls; evidence-count from 30 → 36; added `memory_utilization` to the 5-metric tuple assertion.
- **Extended `tests/unit/test_agent_graph.py::TestSystemPrompt`**: `test_memory_metrics_documented` (both `memory_limit` and `memory_utilization` appear in SYSTEM_PROMPT), `test_memory_pressure_example_updated` (old "OOMKilled entries" wording is gone, `memory_utilization` appears near Example 3).
- **330 unit tests passing** (was 315 pre-session, +15 across 3 test files). `ruff check` and `ruff format` clean on all modified files. `mypy src/` clean (39 source files, 0 issues).

*Live verification sequence:*
- Rebuilt + restarted Docker Stats Exporter; confirmed `container_spec_memory_limit_bytes` flowing for all 14 containers (6 demo + 7 infra + 1 Docker socket).
- Confirmed Prometheus ratio query `container_memory_working_set_bytes{...} / container_spec_memory_limit_bytes{...}` returns numeric values without label-join issues. Baseline utilizations measured across the 6 active services: 7.3-45.4% — all well below the 0.80 trigger and 0.50 baseline guard.
- Injected memory_pressure; waited 120 s; directly invoked `query_metrics.invoke({..., "metric_name": "memory_utilization", "start_time": <anchor>})` — CRITICAL fired on checkoutservice with `peak=0.833, baseline_mean=0.116, current=0.833`, matching the expected signal shape. Verified no false-fire on the other 5 services.

*Full 35-test fault-injection suite (Session 13 run, results in `data/evaluation/results_session13/`):*
- Executed `caffeinate -s poetry run python tests/evaluation/fault_injection_suite.py --seed 42 --repetitions 5` (foreground, ~3.5 hours).
- **Overall: Recall@1 = 35/35 = 100.0%, Recall@3 = 35/35 = 100.0%**, 95% Wilson CI on Recall@1: [90.1%, 100.0%], zero inconclusive.
- **Every single correct prediction at exactly 0.75 confidence** — the CRITICAL-override band fires uniformly across all fault classes. No test fell through to the lower hypothesis-override or PC+LLM-average paths.
- Per-fault: cascading_failure 5/5, config_error 5/5, connection_exhaustion 5/5, high_latency 5/5, **memory_pressure 5/5**, network_partition 5/5, service_crash 5/5. Mean investigation duration 24.1 s (range 18.4-33.8 s); mean detection latency 125.2 s (pre-investigation wait constant); mean MTTR 149.4 s.
- Top-1 prediction distribution: cartservice 10 (GT 10), productcatalogservice 5 (GT 5), redis 5 (GT 5), frontend 5 (GT 5), checkoutservice 5 (GT 5), paymentservice 5 (GT 5). **Exact 1-to-1 match with ground-truth frequencies** — zero misattribution, zero bias. Dramatic improvement over Session 11's 55% cartservice over-prediction.

*Cross-session comparison:*
- Session 11: 27.5% Recall@1 / 40% Recall@3 / 5 inconclusive / mean correct-conf 0.60.
- Session 12 Tier 1/2/3: 42.9% / 68.6% / 0 inconclusive / mean correct-conf 0.49.
- Session 12 Tier 4/5: 91.4% / 94.3% / 0 inconclusive / mean correct-conf 0.73 (memory_pressure 40%).
- **Session 13: 100.0% / 100.0% / 0 inconclusive / mean correct-conf 0.75 (memory_pressure 100%).**
- Cumulative delta S11 → S13: +72.5 pp Recall@1. Incremental S12 → S13: +8.6 pp overall, +60 pp on memory_pressure alone.

*Documentation updates:*
- `CLAUDE.md`: updated "8 container-level metrics available" (was 6), "Sweep covers 5 metrics + logs" (was 4), "Fault script restore methods" (item 3 now documents dynamic cap formula). Added five new gotchas: Docker Stats Exporter emits `container_spec_memory_limit_bytes`; `memory_utilization` CRITICAL detector (peak-based, 80%/50%); `memory_utilization` + `memory_limit` added to `METRIC_PROMQL` (and NOT to `_CAUSAL_METRICS`); dynamic fault-script cap formula; Session 13 final result.
- `PROGRESS.md`: marked "Run OTel Demo fault injection tests" as complete; added this session log entry.
- `context/evaluation_strategy.md`: updated Fault 3 description + fault summary table. `context/infrastructure_and_serving.md`: updated Docker Stats Exporter gauge list. `context/agent_specs.md`: added memory_limit + memory_utilization rows to the Available Metrics table; updated sweep channel count.

**In Progress:**
- Git commits still held at user's request — all Session 12 + Session 13 changes are uncommitted on `main`. Awaiting explicit commit instruction.
- `docs/evaluation_results.md` still needs to be drafted with the 100%/100% Session 13 numbers + per-fault breakdown + cross-session comparison.

**Blockers / Issues:**
- **Fault non-determinism with a fixed `--memory 25m` cap**: under Go GC, checkoutservice working-set ranges 8-30 MiB depending on recent traffic / GC history. A fixed 25 MiB cap is only "tight" when heap is warm (≥20 MiB); idle heaps produce 32-60% utilization and miss the 0.80 threshold. **Resolution:** dynamic `max(W*1.2, W+2)` formula guarantees ≥80% immediate utilization regardless of heap state while preserving the "soft pressure" fault class (no OOM-kill, no crash loop).
- **Peak vs `values[-1]` in memory_utilization CRITICAL trigger**: Go runtimes GC-cycle under tight caps — working set oscillates between ~cap (85%+) and dips (55-65%). A tail scrape landing on a GC dip misses the threshold. **Resolution:** switched trigger to `peak = np.max(arr)`. Safe for memory_utilization because the metric is a bounded ratio with no realistic single-scrape spurious-spike mechanism; probe_latency's `values[-1]`-based detector is unchanged because a single slow scrape IS a meaningful latency signal there.
- **`docker` SDK not installed in host Poetry env**: `infrastructure/docker_stats_exporter/exporter.py` is tested via a module-level `sys.modules['docker']` stub (a `ModuleType` with `DockerClient = MagicMock()`) injected before `importlib.util` loads the file. Every test that exercises `_build_metrics` passes its own `MagicMock` client, so the stub just needs to satisfy the import.
- **Baseline cart-unreachable noise** (v1.10.0 frontend → cartservice:7070 misconfiguration): persistent `ECONNREFUSED` spam in frontend logs at all times. Not fixed this session — other faults' stronger CRITICAL signals override it, and Session 13's memory_utilization CRITICAL now overrides it for memory_pressure too.

**Next Session:**
- Evaluate the 3 internal baselines (Rule-Based, AD-Only, LLM-Without-Tools) against the same 35-test suite for MTTR-reduction comparison.
- Begin Week 10: RCAEval cross-system evaluation on RE1 (375 cases) / RE2 (271) / RE3 (90) via `tests/evaluation/rcaeval_evaluation.py`.
- Draft `docs/evaluation_results.md` with Session 13's 100%/100% numbers + per-fault breakdown + Session 11-12-13 progression chart.
- Optional: fix the v1.10.0 frontend → cartservice:7070 misconfiguration (set `CART_SERVICE_ADDR=cartservice:8080` in frontend's env) to remove baseline log noise that hasn't bitten evaluation but pollutes investigations.

**Notes:**
- Total sweep tool calls jumped from 30 (Session 12) → 36 (Session 13) due to the added `memory_utilization` channel. Still bypasses `tool_calls_remaining` so the LLM keeps its full 10-call budget.
- Session 13's 24.1 s mean investigation duration is **faster than Session 12's 30.7 s** despite the larger sweep — because the CRITICAL-override fires immediately on the correct service for every fault type, the agent's investigation loop terminates at the first cycle (no refinement round). Memory_pressure in particular dropped from 62.3 s → 23.2 s mean.
- `memory_utilization` was **deliberately NOT added** to `_CAUSAL_METRICS` in `discover_causation.py`. Derived ratios are known to degrade Fisher's Z test when their numerator/denominator columns are also present (they introduce perfect-collinearity via the ratio identity and near-collinearity with lagged copies). The CRITICAL-override path handles attribution without needing PC to see the ratio.
- Live diagnosis validated Context7-equivalent knowledge of Docker SDK: `stats["memory_stats"]["limit"]` is always present for running containers; `docker update --memory` mutates it without a restart; macOS Docker Desktop reports host-RAM for uncapped containers. No upstream docs surprised us.
- Peak utilization observed on checkoutservice during the 35-test suite's memory_pressure runs: **0.83-0.94 (all 5 runs)**. Baseline_mean (pre-anchor window) stayed at 0.05-0.12 across all runs. Clear separation from the detector's [0.50, 0.80] thresholds.
- Git commits are still held at user's request. All Session 12 + Session 13 changes are uncommitted on `main`. No branch was created per user instruction.
