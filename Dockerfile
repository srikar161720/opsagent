# OpsAgent serving image.
#
# Single image serves both the FastAPI backend (default CMD) and the
# Streamlit dashboard (started with a ``command`` override in
# docker-compose.yml). Single-worker Uvicorn — the LangGraph agent holds
# in-memory state that's unsafe to duplicate across workers.
#
# Models (``models/``) and the ChromaDB runbook index (``data/chromadb/``)
# are mounted as read-only volumes, not baked in — they're gitignored and
# too tied to the running environment to copy at build time.

FROM python:3.11-slim

WORKDIR /app

# Small system deps:
#   - build-essential: only needed transitively for a handful of wheels
#     that don't have pre-built manylinux artefacts.
#   - curl: used by the HEALTHCHECK below.
#   - graphviz: required by the ``graphviz`` Python bindings at runtime
#     for the Streamlit dashboard's topology visualisation.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        graphviz \
    && rm -rf /var/lib/apt/lists/*

# Pin Poetry to the same version CLAUDE.md documents on the host so
# lockfile resolution is deterministic between host and container.
RUN pip install --no-cache-dir poetry==2.3.2

# Copy dependency files first so the expensive install layer caches
# independently of source changes.
COPY pyproject.toml poetry.lock README.md ./

# Install deps globally (no venv) so ``uvicorn`` / ``streamlit`` are on
# $PATH directly. ``--without dev`` skips ruff/mypy/pytest. ``--no-root``
# skips installing the project itself — we ship source via COPY below.
RUN poetry config virtualenvs.create false \
    && poetry install --without dev --no-interaction --no-ansi --no-root

# Copy application source + config. Order matters for layer caching:
# source changes more often than deps, so it goes last.
COPY src/ ./src/
COPY configs/ ./configs/
COPY runbooks/ ./runbooks/
COPY .streamlit/ ./.streamlit/

# Expose both services' ports. The dashboard uses :8501.
EXPOSE 8000 8501

# Active healthcheck so docker-compose can gate downstream consumers on a
# truly-ready API (not just a listening socket).
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

# Default: FastAPI. docker-compose.yml override runs Streamlit instead for
# the dashboard service.
CMD ["uvicorn", "src.serving.api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
