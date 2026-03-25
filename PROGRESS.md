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
- [ ] Create `docker-compose.yml` with Prometheus, Grafana, Loki, Zookeeper, Kafka
- [ ] Create `infrastructure/prometheus/prometheus.yml` (scrape OTel Demo services)
- [ ] Create `infrastructure/loki/loki-config.yml`
- [ ] Create `infrastructure/grafana/provisioning/datasources/datasources.yml` (Prometheus + Loki)
- [ ] Create `demo_app/docker-compose.demo.yml` (6 OTel services + Redis + loadgenerator)
- [ ] Create `scripts/start_infrastructure.sh` and `scripts/stop_infrastructure.sh`
- [ ] Verify full stack: Grafana shows metrics at `localhost:3000`; logs visible; Kafka topic receiving messages
- [ ] Create `scripts/generate_training_data.py` and start 24h OTel Demo baseline collection

### Dataset Acquisition & EDA (Week 3)
- [ ] Create `scripts/download_datasets.py` (with `--rcaeval`, `--loghub`, `--all` flags)
- [ ] Download RCAEval RE1 (375 cases), RE2 (270 cases), RE3 (90 cases) to `data/RCAEval/`
- [ ] Download LogHub HDFS to `data/LogHub/HDFS/` (verify `HDFS.log` + `anomaly_label.csv` present)
- [ ] Create `notebooks/01_data_exploration.ipynb` — OTel Demo log volume, metric availability, error rates
- [ ] Create `notebooks/02_rcaeval_exploration.ipynb` — fault distributions, root cause service breakdown, metric naming across RE1/RE2/RE3
- [ ] Create `notebooks/03_loghub_exploration.ipynb` — block structure, anomaly rate (~2.9%), Drain3 sample quality
- [ ] Visualize OTel Demo service topology (`docs/images/service_topology.html`)
- [ ] Confirm 24h OTel Demo baseline collection complete (`data/baseline/metadata.json` status = `completed`)
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
- [ ] `src/data_collection/kafka_consumer.py` — `LogConsumer` class
- [ ] `src/preprocessing/log_parser.py` — `LogParser` wrapping Drain3 `TemplateMiner`
- [ ] `src/preprocessing/windowing.py` — `WindowAggregator` (60s windows)
- [ ] `src/preprocessing/feature_engineering.py` — `FeatureEngineer` (log template counts + metric stats)
- [ ] `src/preprocessing/rcaeval_adapter.py` — `RCAEvalDataAdapter` with column normalization; smoke-tested against RE1/RE2/RE3
- [ ] `src/data_collection/metrics_collector.py` — Prometheus query client
- [ ] `notebooks/04_log_parsing_analysis.ipynb` — Drain3 template quality across OTel Demo + HDFS samples

### LogHub Preprocessor & Supporting Components (Week 5)
- [ ] `src/preprocessing/loghub_preprocessor.py` — `LogHubHDFSPreprocessor`: block grouping, label loading, sequence building; validated (~2.9% anomaly rate)
- [ ] `src/data_collection/topology_extractor.py` — `TopologyGraph` (NetworkX DiGraph, 6 services + Redis = 7 total nodes, all edges)
- [ ] `src/knowledge_base/runbook_indexer.py` — `RunbookIndexer` with ChromaDB + `all-MiniLM-L6-v2`
- [ ] `src/knowledge_base/embeddings.py` — embedding utility functions (sentence-transformers `all-MiniLM-L6-v2` wrapper)
- [ ] Create 5 runbooks: `connection_exhaustion.md`, `cascading_failure.md`, `memory_pressure.md`, `high_latency.md`, `general_troubleshooting.md`
- [ ] Index all runbooks into ChromaDB; verify search returns relevant results
- [ ] `scripts/prepare_data_splits.py` — CLI script that calls `create_otel_splits()` and `create_hdfs_splits()` (defined in `src/preprocessing/`; see `context/data_pipeline_specs.md` §5)
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
- [ ] `src/anomaly_detection/lstm_autoencoder.py` — `LSTMAutoencoder` class with `get_reconstruction_error()`
- [ ] `src/anomaly_detection/trainer.py` — `AnomalyTrainer` with training loop and early stopping
- [ ] `src/anomaly_detection/pretrain_on_loghub.py` — `pretrain_on_hdfs()` and `finetune_on_otel_demo()` with `_load_compatible_weights()`
- [ ] `src/anomaly_detection/threshold.py` — 95th percentile threshold from baseline reconstruction errors
- [ ] `src/anomaly_detection/isolation_forest.py` — Isolation Forest baseline (n_estimators=100, contamination=0.01)
- [ ] `src/anomaly_detection/detector.py` — `AnomalyDetector` real-time detection service (Fast Loop → Slow Loop bridge: scores each window, fires alert to `AgentExecutor` when threshold exceeded)
- [ ] Run pretraining on HDFS in `notebooks/05_anomaly_detection_dev.ipynb` (Colab Pro, GPU) — checkpoint saved to `models/lstm_autoencoder/pretrained_hdfs.pt`
- [ ] Run fine-tuning on OTel Demo — checkpoint saved to `models/lstm_autoencoder/finetuned_otel.pt`
- [ ] Plot and save training curves to `docs/images/`

### Causal Discovery (Week 7)
- [ ] `src/causal_discovery/pc_algorithm.py` — `discover_causal_graph()` wrapping causal-learn PC (α=0.05, Fisher's Z)
- [ ] `src/causal_discovery/counterfactual.py` — `calculate_counterfactual_confidence()` scoring
- [ ] `src/causal_discovery/graph_utils.py` — `CausalEdge` and `CausalGraph` dataclasses
- [ ] `notebooks/06_causal_discovery_dev.ipynb` — validate PC algorithm on synthetic data with known ground truth

### LangGraph Agent (Week 8)
- [ ] `src/agent/state.py` — `AgentState` TypedDict
- [ ] `src/agent/graph.py` — LangGraph workflow: analyze_context → form_hypothesis → gather_evidence → analyze_causation → should_continue → generate_report
- [ ] `src/agent/tools/query_metrics.py` — Prometheus query tool
- [ ] `src/agent/tools/search_logs.py` — Loki search tool
- [ ] `src/agent/tools/get_topology.py` — topology retrieval tool
- [ ] `src/agent/tools/search_runbooks.py` — ChromaDB search tool
- [ ] `src/agent/tools/discover_causation.py` — causal analysis tool
- [ ] `src/agent/prompts/system_prompt.py` — agent persona + instructions
- [ ] `src/agent/prompts/report_template.py` — RCA report template
- [ ] `src/agent/executor.py` — `AgentExecutor` (10 tool call limit, error handling)
- [ ] `notebooks/07_agent_prototyping.ipynb` — agent workflow testing
- [ ] End-to-end agent test with manually injected fault — produces valid RCA report
- [ ] Advisor check-in completed (Week 8) — show HDFS pretraining curves + working agent demo

### Unit Tests
- [ ] `tests/unit/test_log_parser.py`
- [ ] `tests/unit/test_feature_engineering.py`
- [ ] `tests/unit/test_anomaly_detection.py`
- [ ] `tests/unit/test_causal_discovery.py`
- [ ] `tests/unit/test_agent_tools.py`
- [ ] `tests/integration/test_data_pipeline.py`
- [ ] `tests/integration/test_agent_workflow.py`

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
- [ ] Create all 8 fault injection bash scripts in `demo_app/fault_scenarios/`
- [ ] Create `scripts/inject_faults.py` — automated fault injection coordinator
- [ ] Create `tests/evaluation/fault_injection_suite.py`
- [ ] Create `tests/evaluation/metrics_calculator.py` — Recall@1, Recall@3, Precision, Detection Latency, MTTR Proxy, F1
- [ ] Run 40 OTel Demo fault injection tests (8 fault types × 5 runs); save results to `data/evaluation/results/`
- [ ] Create `tests/evaluation/baseline_comparison.py`
- [ ] Evaluate 3 internal baselines: Rule-Based, AD-Only, LLM-Without-Tools

### RCAEval Cross-System Evaluation (Week 10, Days 1–2)
- [ ] Create `tests/evaluation/rcaeval_evaluation.py` — `evaluate_on_rcaeval()` + `run_all_rcaeval_variants()`
- [ ] Run OpsAgent against RE1 (375 cases); save results
- [ ] Run OpsAgent against RE2 (270 cases); save results
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
- All 735 RCAEval cases evaluated; results vs. published baselines computed
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
