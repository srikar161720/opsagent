"""FastAPI entry-point for OpsAgent.

Exposes the following user-facing endpoints:

* ``GET  /health``                           — dependency readiness tile.
* ``GET  /topology``                         — service dependency graph.
* ``POST /investigate``                      — run an RCA investigation synchronously.
* ``GET  /investigations``                   — list recent investigations.
* ``GET  /investigations/{id}``              — fetch a single saved investigation.
* ``POST /demo/investigate``                 — guided end-to-end demo that
  injects a fault, waits for it to manifest, investigates, and restores —
  returning an id immediately and running the lifecycle asynchronously.
* ``GET  /demo/investigations/{id}/status``  — poll the demo lifecycle
  phase (dashboard polls every ~2 s to drive its phase stepper).

The heavy resources (``AgentExecutor``, ``TopologyGraph``) are initialized
in the ``lifespan`` context manager and stored on ``app.state`` so they're
created once per process, not per request. Investigations are executed
off the event loop via ``run_in_executor`` / ``asyncio.to_thread`` because
``agent.investigate()`` is a blocking call (30–90 s typical).

Run locally with::

    poetry run uvicorn src.serving.api:app --reload --host 0.0.0.0 --port 8000

Swagger UI auto-generated at ``/docs``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import uuid
from collections import OrderedDict
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from src.agent.executor import AgentExecutor
from src.data_collection.topology_extractor import TopologyGraph
from src.serving.schemas import (
    AlertPayload,
    DemoInvestigationRequest,
    DemoInvestigationStatus,
    DemoPhase,
    DemoService,
    HealthStatus,
    InvestigationRequest,
    InvestigationResponse,
    RootCauseResult,
    TopologyResponse,
)
from tests.evaluation.fault_injection_suite import (
    FAULT_SCRIPTS,
    GROUND_TRUTH,
    _resolve_script,
)

logger = logging.getLogger(__name__)

# Cap the in-memory history so long-running demos don't OOM.
MAX_HISTORY = 100

# Default dependency URLs — overridable via env vars so the same image works
# live (localhost defaults) and inside Docker Compose (service DNS names).
_PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL", "http://localhost:9090")
_LOKI_URL = os.environ.get("LOKI_URL", "http://localhost:3100")
_KAFKA_BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
_CHROMADB_PATH = os.environ.get("CHROMADB_PATH", "data/chromadb")
_AGENT_CONFIG_PATH = os.environ.get("AGENT_CONFIG_PATH", "configs/agent_config.yaml")

_DASHBOARD_ORIGINS = [
    "http://localhost:8501",
    "http://127.0.0.1:8501",
    "http://opsagent-dashboard:8501",
]


# ---------------------------------------------------------------------------
# Demo-lifecycle constants
# ---------------------------------------------------------------------------

# The six services with canonical Session-13 fault scenarios. Each one produced
# 5/5 Recall@1 at 0.75 confidence in the Session-13 evaluation run.
_SERVICE_TO_FAULT: dict[str, str] = {
    "cartservice": "service_crash",
    "checkoutservice": "memory_pressure",
    "frontend": "high_latency",
    "paymentservice": "network_partition",
    "productcatalogservice": "config_error",
    "redis": "connection_exhaustion",
}

# Services passed as ``affected_services`` to the agent. Mirrors
# ``fault_injection_suite.run_fault_injection`` exactly — currencyservice is
# excluded because its permanent v1.10.0 SIGSEGV crash-loop makes probe_up=0
# a permanent baseline signal that the agent otherwise fixates on.
_DEMO_AFFECTED_SERVICES: tuple[str, ...] = (
    "cartservice",
    "checkoutservice",
    "frontend",
    "paymentservice",
    "productcatalogservice",
    "redis",
)

# Seconds the API sleeps after injection before triggering the investigation.
# The Session-13 harness uses the same 120 s — at 60 s the ``rate()[1m]``
# lookback window still contains stale pre-fault data and the CRITICAL
# detectors don't fire.
_DEMO_WAIT_SECONDS: int = 120

# Subprocess timeout for the inject / restore fault-script calls.
_DEMO_SUBPROCESS_TIMEOUT: int = 120

# Metric query window (minutes) passed to the agent. Session 13 uses 10.
_DEMO_TIME_RANGE_MINUTES: int = 10

# FIFO cap on ``demo_status`` entries.
_DEMO_STATUS_CAP: int = 20

_DEMO_PHASE_LABELS: dict[str, str] = {
    "queued": "Queued",
    "injecting": "Injecting fault",
    "waiting": "Waiting for anomaly",
    "investigating": "Investigating",
    "restoring": "Restoring",
    "completed": "Completed",
    "failed": "Failed",
}

_DEMO_PHASE_PROGRESS: dict[str, int] = {
    "queued": 0,
    "injecting": 10,
    "waiting": 30,
    "investigating": 70,
    "restoring": 90,
    "completed": 100,
    "failed": 100,
}

# Phases considered "in-flight" for the shutdown-hook restore sweep.
_DEMO_IN_FLIGHT_PHASES: frozenset[str] = frozenset(
    {"queued", "injecting", "waiting", "investigating"}
)


# ---------------------------------------------------------------------------
# Alert translation
# ---------------------------------------------------------------------------


def _alert_payload_to_executor_alert(alert: AlertPayload) -> dict[str, Any]:
    """Translate the API's minimal ``AlertPayload`` into the richer alert
    dict that ``AgentExecutor._format_alert`` expects.

    Mirrors the convention used by ``scripts/run_agent_demo.py`` so the
    investigation experience is identical whether triggered via the CLI or
    via the API.
    """
    return {
        "title": f"LSTM-AE Anomaly Detected — Elevated {alert.metric} in {alert.service}",
        "severity": "high",
        "timestamp": alert.timestamp,
        "affected_services": [alert.service],
        "anomaly_score": alert.value,
        "threshold": alert.threshold,
        "alert_metric": alert.metric,  # echoed for downstream consumers
    }


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Initialize heavy resources once at startup.

    Uses ``app.state`` so endpoint handlers can share them. We intentionally
    do NOT initialize ``AgentExecutor`` at module import time — that would
    load the LLM client and LangGraph before Uvicorn has forked workers,
    which is wasteful (and broken for multi-worker setups).

    On shutdown, sweeps ``demo_status`` for any in-flight demo and attempts
    its ``restore`` script synchronously so a crashed uvicorn doesn't leave
    a fault active on the Docker stack.
    """
    logger.info("OpsAgent API starting up...")
    app.state.agent = AgentExecutor.from_config(_AGENT_CONFIG_PATH)
    app.state.topology = TopologyGraph()
    app.state.investigations = OrderedDict()
    app.state.demo_lock = asyncio.Lock()
    app.state.demo_status = OrderedDict()
    app.state.started_at = datetime.now(UTC).isoformat()
    logger.info("OpsAgent API ready. config=%s", _AGENT_CONFIG_PATH)

    yield

    logger.info("OpsAgent API shutting down.")
    _shutdown_demo_restore(app)


def _shutdown_demo_restore(app: FastAPI) -> None:
    """Attempt to restore any demo fault left in-flight at shutdown.

    Runs synchronously (we're already out of the event loop). Safe to
    call multiple times — the restore scripts are idempotent.
    """
    statuses: OrderedDict[str, DemoInvestigationStatus] = getattr(
        app.state, "demo_status", None
    ) or OrderedDict()
    for inv_id, status in list(statuses.items()):
        if status.phase not in _DEMO_IN_FLIGHT_PHASES:
            continue
        fault_type = status.fault_type
        if fault_type not in FAULT_SCRIPTS:
            continue
        script_path = _resolve_script(fault_type)
        logger.warning(
            "Demo %s in-flight at shutdown (phase=%s); attempting restore",
            inv_id,
            status.phase,
        )
        try:
            subprocess.run(
                ["bash", script_path, "restore"],
                check=False,
                timeout=_DEMO_SUBPROCESS_TIMEOUT,
            )
        except Exception:
            logger.exception("Shutdown restore for demo %s failed", inv_id)


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------


app = FastAPI(
    title="OpsAgent API",
    description=(
        "Autonomous root-cause analysis for microservices. Wraps the "
        "OpsAgent LangGraph agent behind a small synchronous HTTP surface."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_DASHBOARD_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------


def _probe_prometheus() -> str:
    try:
        r = requests.get(f"{_PROMETHEUS_URL}/-/healthy", timeout=2)
        return "connected" if r.status_code == 200 else "error"
    except Exception:
        return "unreachable"


def _probe_loki() -> str:
    try:
        r = requests.get(f"{_LOKI_URL}/ready", timeout=2)
        # Loki's /ready returns 200 "ready" when healthy.
        return "connected" if r.status_code == 200 else "error"
    except Exception:
        return "unreachable"


def _probe_kafka() -> str:
    try:
        from confluent_kafka.admin import AdminClient

        admin = AdminClient({"bootstrap.servers": _KAFKA_BOOTSTRAP})
        admin.list_topics(timeout=2)
        return "connected"
    except Exception:
        return "unreachable"


def _probe_chromadb() -> str:
    try:
        import chromadb

        client = chromadb.PersistentClient(path=_CHROMADB_PATH)
        client.heartbeat()
        return "connected"
    except Exception:
        return "unreachable"


def _probe_llm() -> str:
    """Gemini is stateless; a true probe would burn tokens, so we just
    report ``available`` when the API key is set and ``unconfigured``
    otherwise.
    """
    return "available" if os.environ.get("GEMINI_API_KEY") else "unconfigured"


@app.get(
    "/health",
    response_model=HealthStatus,
    tags=["ops"],
    summary="Check OpsAgent and dependency health.",
)
def health_check() -> HealthStatus:
    components = {
        "prometheus": _probe_prometheus(),
        "loki": _probe_loki(),
        "kafka": _probe_kafka(),
        "chromadb": _probe_chromadb(),
        "llm": _probe_llm(),
    }
    overall = (
        "healthy"
        if all(v in ("connected", "available") for v in components.values())
        else "degraded"
    )
    return HealthStatus(status=overall, components=components)


# ---------------------------------------------------------------------------
# /topology
# ---------------------------------------------------------------------------


@app.get(
    "/topology",
    response_model=TopologyResponse,
    tags=["ops"],
    summary="Retrieve the service dependency graph.",
)
def get_topology(request: Request, service: str | None = None) -> TopologyResponse:
    """Return the full topology or a subgraph centred on ``service``.

    The full graph has 11 nodes / 15 edges (reduced OTel Demo + Online
    Boutique extension services). A subgraph returns ``service`` plus its
    immediate upstream and downstream neighbours.
    """
    topology: TopologyGraph = request.app.state.topology
    if service:
        data = topology.get_subgraph(service)
        return TopologyResponse(
            nodes=data.get("nodes", []),
            edges=data.get("edges", []),
            subgraph_of=service,
        )
    # ``TopologyGraph.to_json()`` returns a JSON *string* (used by the
    # LLM agent tool where JSON text is what lands in the prompt). Parse
    # it back into a dict for the Pydantic response model.
    data = json.loads(topology.to_json())
    return TopologyResponse(nodes=data["nodes"], edges=data["edges"], subgraph_of=None)


# ---------------------------------------------------------------------------
# /investigate
# ---------------------------------------------------------------------------


def _build_response_from_agent_result(
    investigation_id: str,
    started_at: datetime,
    result: dict[str, Any],
) -> InvestigationResponse:
    duration = (datetime.now(UTC) - started_at).total_seconds()
    service = result.get("root_cause") or "unknown"
    root_cause: RootCauseResult | None = None
    if service and service not in ("unknown", "inconclusive"):
        root_cause = RootCauseResult(
            service=service,
            confidence=float(result.get("root_cause_confidence", 0.0) or 0.0),
        )
    return InvestigationResponse(
        investigation_id=investigation_id,
        status="completed",
        root_cause=root_cause,
        top_3_predictions=result.get("top_3_predictions", []) or [],
        report=result.get("rca_report"),
        evidence=[],  # agent doesn't yet surface structured evidence; placeholder
        recommendations=result.get("recommended_actions", []) or [],
        duration_seconds=round(duration, 3),
        started_at=started_at.isoformat(),
    )


def _remember(history: OrderedDict, inv: InvestigationResponse) -> None:
    """Insert an investigation into the history store and evict oldest if full.

    Uses ``OrderedDict.popitem(last=False)`` for FIFO eviction.
    """
    history[inv.investigation_id] = inv
    while len(history) > MAX_HISTORY:
        history.popitem(last=False)


@app.post(
    "/investigate",
    response_model=InvestigationResponse,
    tags=["agent"],
    summary="Run a root-cause investigation synchronously.",
)
async def investigate(request: Request, body: InvestigationRequest) -> InvestigationResponse:
    """Trigger a new RCA investigation.

    Runs synchronously in a worker thread; typical duration 30–90 seconds.
    Errors are caught and returned as ``status="failed"`` rather than HTTP
    5xx — the UX goal is "see the failure in the dashboard", not a cryptic
    error code.
    """
    investigation_id = f"inv_{uuid.uuid4().hex[:8]}"
    started_at = datetime.now(UTC)
    agent: AgentExecutor = request.app.state.agent
    history: OrderedDict = request.app.state.investigations

    executor_alert = _alert_payload_to_executor_alert(body.alert)

    try:
        loop = asyncio.get_running_loop()
        result: dict[str, Any] = await loop.run_in_executor(
            None,
            lambda: agent.investigate(alert=executor_alert),
        )
        response = _build_response_from_agent_result(investigation_id, started_at, result)
    except Exception as exc:
        logger.exception("Investigation %s failed", investigation_id)
        duration = (datetime.now(UTC) - started_at).total_seconds()
        response = InvestigationResponse(
            investigation_id=investigation_id,
            status="failed",
            root_cause=None,
            report=f"Investigation failed: {exc}",
            duration_seconds=round(duration, 3),
            started_at=started_at.isoformat(),
        )

    _remember(history, response)
    return response


# ---------------------------------------------------------------------------
# /investigations
# ---------------------------------------------------------------------------


@app.get(
    "/investigations",
    response_model=list[InvestigationResponse],
    tags=["agent"],
    summary="List recent investigations.",
)
def list_investigations(request: Request, limit: int = 20) -> list[InvestigationResponse]:
    """Newest first. Capped at ``MAX_HISTORY`` (100) entries total."""
    history: OrderedDict = request.app.state.investigations
    limit = max(1, min(limit, MAX_HISTORY))
    # OrderedDict is insertion-ordered (oldest first after eviction), so
    # reverse to hand back newest first.
    values = list(history.values())[-limit:]
    return list(reversed(values))


@app.get(
    "/investigations/{investigation_id}",
    response_model=InvestigationResponse,
    tags=["agent"],
    summary="Get a single investigation by id.",
)
def get_investigation(request: Request, investigation_id: str) -> InvestigationResponse:
    history: OrderedDict[str, InvestigationResponse] = request.app.state.investigations
    if investigation_id not in history:
        raise HTTPException(status_code=404, detail="Investigation not found")
    return history[investigation_id]


# ---------------------------------------------------------------------------
# /demo/investigate — guided service-picker demo
# ---------------------------------------------------------------------------


def _record_demo_status(app: FastAPI, inv_id: str, status: DemoInvestigationStatus) -> None:
    """Insert or replace a demo status entry, capped at ``_DEMO_STATUS_CAP``."""
    store: OrderedDict[str, DemoInvestigationStatus] = app.state.demo_status
    store[inv_id] = status
    while len(store) > _DEMO_STATUS_CAP:
        store.popitem(last=False)


def _update_demo_phase(
    app: FastAPI,
    inv_id: str,
    phase: DemoPhase,
    *,
    error: str | None = None,
    result: InvestigationResponse | None = None,
    completed_at: str | None = None,
) -> None:
    """Replace the ``phase`` field (and related progress/label fields) of an
    existing demo status entry. No-ops if the entry has been evicted.
    """
    store: OrderedDict[str, DemoInvestigationStatus] = app.state.demo_status
    existing = store.get(inv_id)
    if existing is None:
        return
    updated = existing.model_copy(
        update={
            "phase": phase,
            "phase_label": _DEMO_PHASE_LABELS[phase],
            "progress_pct": _DEMO_PHASE_PROGRESS[phase],
            "error": error if error is not None else existing.error,
            "result": result if result is not None else existing.result,
            "completed_at": completed_at if completed_at is not None else existing.completed_at,
        }
    )
    store[inv_id] = updated


def _build_demo_alert(service: str, fault_start_time: str) -> dict[str, Any]:
    """Build the 6-service alert dict the agent's executor expects.

    Mirrors ``tests.evaluation.fault_injection_suite.run_fault_injection`` so
    the investigation signal shape is identical to Session 13's. The title
    is intentionally neutral — including the fault type or the word "fault"
    in the title causes the LLM to blame the fault-injection system itself
    instead of a real service.
    """
    return {
        "title": "Anomaly Detected — Automated Investigation Triggered",
        "severity": "critical",
        "timestamp": fault_start_time,
        "affected_services": list(_DEMO_AFFECTED_SERVICES),
        "anomaly_score": 1.0,
        "alert_service": service,
    }


async def _run_demo_lifecycle(
    inv_id: str,
    service: DemoService,
    app: FastAPI,
) -> None:
    """Background task: inject → wait → investigate → restore.

    Reuses the Session-13 fault-injection constants (``FAULT_SCRIPTS``,
    ``GROUND_TRUTH``, ``_resolve_script``) but reorchestrates the phases
    in async so ``demo_status`` can be updated between each step. Blocking
    calls (``subprocess.run``, ``agent.investigate``) are off-loaded to a
    worker thread via ``asyncio.to_thread``. The 120-s wait uses
    ``asyncio.sleep`` so the event loop stays responsive.

    The ``restore`` script is ALWAYS called in ``finally``, even on
    exception, so a failed agent call or mid-flight crash doesn't leave
    the Docker stack in a broken state.
    """
    fault_type = _SERVICE_TO_FAULT[service]
    script_path = _resolve_script(fault_type)
    agent: AgentExecutor = app.state.agent
    history: OrderedDict[str, InvestigationResponse] = app.state.investigations

    async with app.state.demo_lock:
        fault_start_time = datetime.now(UTC).isoformat()
        investigation_response: InvestigationResponse | None = None
        investigation_failed = False
        failure_error: str | None = None
        try:
            # 1) Inject fault
            _update_demo_phase(app, inv_id, "injecting")
            await asyncio.to_thread(
                subprocess.run,
                ["bash", script_path, "inject"],
                check=True,
                timeout=_DEMO_SUBPROCESS_TIMEOUT,
            )

            # 2) Wait for anomaly to manifest
            _update_demo_phase(app, inv_id, "waiting")
            await asyncio.sleep(_DEMO_WAIT_SECONDS)

            # 3) Investigate
            _update_demo_phase(app, inv_id, "investigating")
            alert_time = datetime.now(UTC)
            alert = _build_demo_alert(service, fault_start_time)
            result: dict[str, Any] = await asyncio.to_thread(
                agent.investigate,
                alert=alert,
                start_time=fault_start_time,
            )
            investigation_response = _build_response_from_agent_result(inv_id, alert_time, result)
            _remember(history, investigation_response)

        except Exception as exc:
            logger.exception("Demo %s lifecycle failed", inv_id)
            investigation_failed = True
            failure_error = str(exc)
            fault_start_dt = datetime.fromisoformat(fault_start_time)
            duration = (datetime.now(UTC) - fault_start_dt).total_seconds()
            investigation_response = InvestigationResponse(
                investigation_id=inv_id,
                status="failed",
                root_cause=None,
                report=f"Demo investigation failed: {exc}",
                duration_seconds=round(duration, 3),
                started_at=fault_start_time,
            )
            _remember(history, investigation_response)
        finally:
            # 4) Restore — always runs, idempotent on success and failure
            restore_error: str | None = None
            _update_demo_phase(app, inv_id, "restoring", error=failure_error)
            try:
                await asyncio.to_thread(
                    subprocess.run,
                    ["bash", script_path, "restore"],
                    check=False,
                    timeout=_DEMO_SUBPROCESS_TIMEOUT,
                )
            except Exception as restore_exc:
                logger.exception("Demo %s restore failed", inv_id)
                restore_error = str(restore_exc)

            completed_at = datetime.now(UTC).isoformat()
            final_phase: DemoPhase = "failed" if investigation_failed else "completed"
            final_error = failure_error or restore_error
            _update_demo_phase(
                app,
                inv_id,
                final_phase,
                error=final_error,
                result=investigation_response,
                completed_at=completed_at,
            )


@app.post(
    "/demo/investigate",
    tags=["demo"],
    summary="Start a guided fault-injection demo.",
)
async def demo_investigate(request: Request, body: DemoInvestigationRequest) -> dict[str, str]:
    """Trigger a guided end-to-end demo for one of the six supported services.

    Returns an ``investigation_id`` immediately. The dashboard polls
    ``GET /demo/investigations/{id}/status`` every ~2 s to drive its phase
    stepper. Typical wall-clock per demo: ~3 minutes (120 s wait +
    ~25 s investigation + ~15 s restore + overhead).

    Rejects concurrent demos with HTTP 409 to avoid two faults colliding
    on the shared Docker stack.
    """
    if request.app.state.demo_lock.locked():
        raise HTTPException(status_code=409, detail="A demo is already running; please wait.")

    service: DemoService = body.service
    fault_type = _SERVICE_TO_FAULT[service]
    inv_id = f"demo_{uuid.uuid4().hex[:8]}"
    status = DemoInvestigationStatus(
        investigation_id=inv_id,
        service=service,
        fault_type=fault_type,
        phase="queued",
        phase_label=_DEMO_PHASE_LABELS["queued"],
        progress_pct=_DEMO_PHASE_PROGRESS["queued"],
        started_at=datetime.now(UTC).isoformat(),
    )
    _record_demo_status(request.app, inv_id, status)

    asyncio.create_task(_run_demo_lifecycle(inv_id, service, request.app))

    return {
        "investigation_id": inv_id,
        "service": service,
        "fault_type": fault_type,
        "ground_truth": GROUND_TRUTH[fault_type],
    }


@app.get(
    "/demo/investigations/{investigation_id}/status",
    response_model=DemoInvestigationStatus,
    tags=["demo"],
    summary="Get the current phase of a demo investigation.",
)
def get_demo_status(request: Request, investigation_id: str) -> DemoInvestigationStatus:
    """Return the live status snapshot for a demo investigation."""
    store: OrderedDict[str, DemoInvestigationStatus] = request.app.state.demo_status
    if investigation_id not in store:
        raise HTTPException(status_code=404, detail="Demo investigation not found")
    return store[investigation_id]


__all__ = ["app"]
