# OpsAgent

**Autonomous Root Cause Analysis Agent for Microservices.**

OpsAgent monitors a microservice architecture, detects anomalies across logs and metrics, and autonomously investigates incidents to produce structured RCA reports. It acts as a virtual Site Reliability Engineer.

## Architecture

OpsAgent uses a **two-loop design** that separates real-time detection from deep investigation:

**Fast Loop (Watchdog).** Continuous, lightweight anomaly detection. Kafka ingests logs from microservices; Drain3 extracts structured log templates; an LSTM-Autoencoder scores log sequences against a learned baseline. Prometheus scrapes metrics in parallel from three custom exporters (Docker Stats Exporter, Service Probe Exporter, OpenTelemetry Collector spanmetrics connector). When the combined anomaly score crosses the threshold, the Fast Loop triggers the Slow Loop.

**Slow Loop (Investigator).** LangGraph-powered autonomous investigation. On trigger, a 7-node agent queries metrics (Prometheus) and logs (Loki), retrieves service topology, runs causal discovery (PC algorithm), scores counterfactual confidence, searches relevant runbooks (ChromaDB with sentence-transformers), and generates a structured RCA report with an evidence chain, root-cause identification, causal graph, and confidence score. Typical investigation wall-clock is 20 to 30 seconds.

See [docs/architecture.md](docs/architecture.md) for the full system architecture.

## Data Strategy

Three complementary data sources serve non-overlapping purposes:

| Dataset | Size | Role |
|---------|------|------|
| **OpenTelemetry Demo** (self-generated) | 24h baseline + 35 fault tests (7 types x 5 reps) | Primary training data and controlled evaluation with known ground truth |
| **LogHub HDFS** (Zenodo DOI: 10.5281/zenodo.8196385) | 11M+ logs, block-level labels | LSTM-AE pretraining and Drain3 template validation |
| **RCAEval** RE1/RE2/RE3 (Zenodo DOI: 10.5281/zenodo.14590730) | 736 labeled failure cases | Cross-system RCA validation; comparison against 5 published baselines |

## Evaluation Highlights

OpsAgent's evaluation produced three result tracks. Full report in [docs/evaluation_results.md](docs/evaluation_results.md).

- **Primary (OTel Demo fault injection, n=35).** 100 percent Recall@1 and 100 percent Recall@3 (Wilson 95 percent CI [0.901, 1.000]). All 7 fault classes at 5/5. Mean investigation duration 24.1 seconds.
- **Internal baseline ablations (n=35 each).** Rule-Based 11.4 percent Recall@1, AD-Only 14.3 percent, LLM-Without-Tools 31.4 percent. OpsAgent dominates every baseline on every test (McNemar p less than 1e-7; zero discordant cases where a baseline wins).
- **Cross-system validation (RCAEval-OB, n=216).** Recall@1 7.9 percent (at random chance), Recall@3 33.3 percent (6 percentage points above random). Honest finding: OpsAgent's native cross-system performance depends on its custom telemetry (Service Probe Exporter, memory_utilization CRITICAL detector), which the RCAEval metrics-only CSVs do not provide.
- **Explanation quality.** Manual 5-point rubric scoring across all 35 RCA reports from the primary evaluation produced a mean overall score of 4.25 of 5.0 (95 percent CI [4.12, 4.38]), clearing the 4.0 target with margin.

## Quick Start

### Prerequisites

- Python 3.11 or newer (less than 3.13 per `pyproject.toml`)
- [Poetry](https://python-poetry.org/docs/#installation)
- Docker and Docker Compose (for the infrastructure stack and the OTel Demo)
- A Gemini API key (free tier works for the demo; paid tier required to re-run the evaluation suite)

### Setup

```bash
git clone <repository-url>
cd opsagent
poetry install

# Configure environment
cp .env.example .env
# Edit .env and add your GEMINI_API_KEY

# Verify installation
poetry run python -c "import torch; print(torch.__version__)"
```

### Common Commands

```bash
# Environment
make setup                    # Install all dependencies
poetry run python <script>    # Run within Poetry env

# Infrastructure
make infra-up                 # Start Docker stack (Prometheus, Grafana, Loki, Kafka, exporters)
make infra-down               # Tear down Docker stack
make demo-up                  # Start the reduced OTel Demo microservices
make demo-down                # Stop the OTel Demo

# Data
poetry run python scripts/download_datasets.py --all     # Download RCAEval + LogHub HDFS
poetry run python scripts/generate_training_data.py      # Collect OTel Demo baseline (24h)
poetry run python scripts/inject_faults.py               # Run the fault-injection evaluation

# Quality
make lint                     # Lint and format (ruff)
make typecheck                # Type check (mypy)
make test                     # Run unit tests (approximately 570 tests)
make test-integration         # Run integration tests

# Serving (host processes)
make run                      # FastAPI at http://localhost:8000 (Swagger UI at /docs)
make dashboard                # Streamlit at http://localhost:8501

# Serving (Docker image)
make docker-build             # Build the opsagent image
make docker-up                # Run opsagent-api + opsagent-dashboard on opsagent-net
make docker-down              # Stop them
make api-health               # curl http://localhost:8000/health (pretty-printed)

# Evaluation
poetry run python scripts/run_evaluation.py              # Aggregate all result directories into evaluation_summary.json
```

### End-to-End Demo (Guided Service-Picker)

The dashboard ships with a guided demo flow. Pick one of six services, and OpsAgent injects the mapped fault, waits for the anomaly to propagate, investigates, generates an RCA report, and restores the service. Total wall-clock is about 3 minutes per demo.

1. `make infra-up` starts Prometheus, Grafana, Loki, Kafka, and the three custom exporters.
2. `make demo-up` starts the reduced OTel Demo microservices.
3. `make run` in one terminal starts the FastAPI API at <http://localhost:8000>.
4. `make dashboard` in a second terminal starts the Streamlit dashboard at <http://localhost:8501>.
5. In the dashboard, open the **Investigate** page and pick one of the six supported services:
   - `cartservice` triggers a service crash.
   - `checkoutservice` triggers memory pressure.
   - `frontend` triggers high latency (500 ms tc netem injection).
   - `paymentservice` triggers a network partition (Docker pause).
   - `productcatalogservice` triggers a config error (invalid port crash-loop).
   - `redis` triggers connection exhaustion (Docker pause).
6. Watch the phase stepper: **Injecting** (about 1 second) to **Waiting** (120 seconds for the anomaly to propagate into the Prometheus `rate()` lookback window) to **Investigating** (about 25 seconds agent wall-clock) to **Restoring** (about 15 seconds) to **Completed**.
7. The page renders the root-cause card with a 0.75 confidence ring, the top-3 candidates, and the full RCA report with evidence chain, causal graph, and recommendations.

No manual fault-script invocation is needed. The restore script always runs, including on failure, so the Docker stack is never left in a broken state. Concurrent demos are rejected with HTTP 409 (single-user lock).

## Technology Stack

| Layer | Tool | Purpose |
|-------|------|---------|
| Orchestration | Docker Compose | Local multi-service stack |
| Target System | OpenTelemetry Demo (reduced, 6 services plus Redis) | Source of microservice logs and metrics |
| Metrics | Prometheus + Grafana + Docker Stats Exporter | Metrics collection and visualization |
| App Telemetry | OpenTelemetry Collector (spanmetrics connector) | Application-level request, error, and latency metrics from traces |
| Service Probes | Service Probe Exporter (custom) | Direct TCP and HTTP probes per service (probe_up, probe_latency) |
| Logs | Loki + Kafka + Promtail | Log aggregation and stream ingestion |
| Log Parsing | Drain3 (v0.9.1 pinned) | Template extraction from raw logs |
| Feature Engineering | Pandas + NumPy | Windowed aggregations and 54-dim feature vectors |
| Topology | NetworkX | Service dependency graph (11 nodes, 14 edges) |
| Vector DB | ChromaDB + sentence-transformers | Runbook similarity search |
| Anomaly Detection | PyTorch (LSTM-Autoencoder) | Primary: log sequence reconstruction error |
| Anomaly Baseline | scikit-learn (Isolation Forest) | Comparison baseline |
| Causal Discovery | causal-learn | PC Algorithm plus counterfactual confidence scoring |
| Agent Orchestration | LangGraph | 7-node stateful investigation graph |
| LLM | Gemini 3 Flash (preview) / Gemini 2.5 Flash | Agent reasoning and report generation |
| API | FastAPI | REST endpoints (`/health`, `/topology`, `/investigate`, `/demo/investigate`, and three more) |
| Dashboard | Streamlit (5 pages) | Overview, Investigate, History, Metrics, Settings |

## HTTP API

Seven endpoints; full reference in [docs/api_reference.md](docs/api_reference.md). Highlights:

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Report OpsAgent and dependency status |
| `GET` | `/topology` | Return the full service dependency graph or a subgraph |
| `POST` | `/investigate` | Run a synchronous RCA investigation for a caller-supplied alert |
| `POST` | `/demo/investigate` | Start a guided fault-injection demo for one of six services |
| `GET` | `/demo/investigations/{id}/status` | Poll the current phase for the dashboard's stepper |

The Swagger UI is served at <http://localhost:8000/docs> when the API is running.

## Project Structure

```
opsagent/
├── src/
│   ├── data_collection/      # Kafka consumer, metrics collector, topology extractor
│   ├── preprocessing/        # Log parser, windowing, feature engineering, RCAEval and LogHub adapters
│   ├── anomaly_detection/    # LSTM-AE, trainer, detector, threshold, Isolation Forest
│   ├── causal_discovery/     # PC algorithm, counterfactual scoring, graph utilities
│   ├── agent/                # LangGraph 7-node agent, tools, prompts, state, executor
│   ├── knowledge_base/       # Runbook indexer, embeddings
│   └── serving/              # FastAPI API, Streamlit dashboard, schemas, theme, helpers
├── tests/                    # Unit, integration, and evaluation tests
├── configs/                  # YAML configuration files
├── data/                     # Datasets (not tracked in git)
├── models/                   # Trained model checkpoints (LSTM-AE not tracked in git)
├── notebooks/                # Jupyter notebooks for experimentation and analysis
├── scripts/                  # Setup, data collection, fault injection, evaluation scripts
├── infrastructure/           # Prometheus, Grafana, Loki, Kafka, exporter configs
├── demo_app/                 # OTel Demo compose file and fault scenario scripts
├── runbooks/                 # Operational runbooks for ChromaDB indexing
└── docs/                     # User-facing documentation
```

## Documentation

| Document | Contents |
|---|---|
| [docs/architecture.md](docs/architecture.md) | System architecture, two-loop design, topology, component catalog, LangGraph flow, confidence banding |
| [docs/api_reference.md](docs/api_reference.md) | HTTP API reference with schemas, examples, and error model |
| [docs/CRISP_DM_Report.md](docs/CRISP_DM_Report.md) | Full CRISP-DM final report (business, data, preparation, modeling, evaluation, deployment) |
| [docs/evaluation_results.md](docs/evaluation_results.md) | Detailed evaluation results with statistical analysis |
| [docs/problem_statement.md](docs/problem_statement.md) | Problem framing and target users |
| [docs/success_metrics.md](docs/success_metrics.md) | Success criteria, rubric definitions, statistical plan |
| [docs/baselines.md](docs/baselines.md) | Internal ablation baselines plus published baseline references |

## License

See [LICENSE](LICENSE) for details.
