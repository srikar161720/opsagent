# OpsAgent

Autonomous Root Cause Analysis Agent for Microservices.

OpsAgent monitors a microservice architecture, detects anomalies across logs and metrics, and autonomously investigates incidents to produce structured RCA reports — acting as a virtual Site Reliability Engineer.

## Architecture

OpsAgent uses a **two-loop design** that separates real-time detection from deep investigation:

**Fast Loop (Watchdog)** — Continuous, lightweight anomaly detection. Kafka ingests logs from microservices; Drain3 extracts structured log templates; an LSTM-Autoencoder scores log sequences against a learned baseline. Prometheus scrapes metrics in parallel. When the combined anomaly score exceeds the threshold, the Fast Loop triggers the Slow Loop.

**Slow Loop (Investigator)** — LangGraph-powered autonomous investigation. On trigger, the agent queries metrics (Prometheus) and logs (Loki), retrieves service topology, runs causal discovery (PC algorithm) to distinguish cause from effect, scores counterfactual confidence, searches relevant runbooks, and generates a structured RCA report with an evidence chain, root cause identification, causal graph, and confidence score.

## Data Strategy

Three complementary data sources serve non-overlapping purposes:

| Dataset | Size | Role |
|---------|------|------|
| **OpenTelemetry Demo** (self-generated) | 24h baseline + 40 fault tests | Primary training data and controlled evaluation with known ground truth |
| **LogHub HDFS** (Zenodo DOI: 10.5281/zenodo.8196385) | 11M+ logs, block-level labels | LSTM-AE pretraining; Drain3 template validation; benchmark vs. DeepLog/LogRobust |
| **RCAEval** RE1/RE2/RE3 (Zenodo DOI: 10.5281/zenodo.14590730) | 736 labeled failure cases | Cross-system RCA validation; comparison against 5 published baselines |

## Quick Start

### Prerequisites

- Python 3.11+
- [Poetry](https://python-poetry.org/docs/#installation)
- Docker and Docker Compose (for infrastructure and OTel Demo)

### Setup

```bash
# Clone and install dependencies
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
make infra-up                 # Start Docker stack (Prometheus, Grafana, Loki, Kafka)
make infra-down               # Tear down Docker stack
make demo-up                  # Start OTel Demo app

# Data
poetry run python scripts/download_datasets.py --all  # Download all datasets

# Quality
make lint                     # Lint + format (ruff)
make typecheck                # Type check (mypy)
make test                     # Run unit tests
make test-integration         # Run integration tests
```

## Technology Stack

| Layer | Tool | Purpose |
|-------|------|---------|
| Orchestration | Docker Compose | Local multi-service stack |
| Target System | OpenTelemetry Demo (6 services) | Source of microservice logs and metrics |
| Metrics | Prometheus + Grafana | Metrics collection and visualization |
| Logs | Loki + Kafka | Log aggregation and stream ingestion |
| Log Parsing | Drain3 | Template extraction from raw logs |
| Feature Engineering | Pandas + NumPy | Windowed aggregations and feature vectors |
| Topology | NetworkX | Service dependency graph |
| Vector DB | ChromaDB + sentence-transformers | Runbook similarity search |
| Anomaly Detection | PyTorch (LSTM-Autoencoder) | Log sequence anomaly scoring |
| Causal Discovery | causal-learn | PC Algorithm for root cause graph |
| Agent Orchestration | LangGraph | Stateful multi-step investigation graph |
| LLM | Gemini 1.5 Flash | Agent reasoning and report generation |
| API | FastAPI | REST endpoint (`POST /investigate`) |
| Dashboard | Streamlit | Interactive demo UI |

## Project Structure

```
opsagent/
├── src/
│   ├── data_collection/      # Kafka consumer, metrics collector, topology extractor
│   ├── preprocessing/        # Log parser, windowing, feature engineering, adapters
│   ├── anomaly_detection/    # LSTM-AE, trainer, detector, threshold, Isolation Forest
│   ├── causal_discovery/     # PC algorithm, counterfactual scoring, graph utilities
│   ├── agent/                # LangGraph agent, tools, prompts, state, executor
│   ├── knowledge_base/       # Runbook indexer, embeddings
│   └── serving/              # FastAPI API, Streamlit dashboard
├── tests/                    # Unit, integration, and evaluation tests
├── configs/                  # YAML configuration files
├── data/                     # Datasets (not tracked in git)
├── models/                   # Trained model checkpoints
├── notebooks/                # Jupyter notebooks for experimentation
├── scripts/                  # Setup, data collection, evaluation scripts
├── infrastructure/           # Prometheus, Grafana, Loki, Kafka configs
├── demo_app/                 # OTel Demo compose file and fault scenarios
├── runbooks/                 # Operational runbooks for ChromaDB indexing
└── docs/                     # Documentation and evaluation results
```

## License

See [LICENSE](LICENSE) for details.
