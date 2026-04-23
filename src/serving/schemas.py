"""Pydantic request/response models for the OpsAgent HTTP API.

Kept in a separate module from ``api.py`` so tests (and the dashboard's
type-checked helpers) can import the schemas without spinning up the
FastAPI app — which would otherwise pull in ``AgentExecutor`` and ``TopologyGraph``
at module import time.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

DemoPhase = Literal[
    "queued",
    "injecting",
    "waiting",
    "investigating",
    "restoring",
    "completed",
    "failed",
]

DemoService = Literal[
    "cartservice",
    "checkoutservice",
    "frontend",
    "paymentservice",
    "productcatalogservice",
    "redis",
]


class AlertPayload(BaseModel):
    """A single anomaly alert submitted by the dashboard or external caller.

    Intentionally minimal. The API layer translates this into the richer
    executor-alert shape that ``AgentExecutor._format_alert()`` expects
    (see ``api._alert_payload_to_executor_alert``).
    """

    service: str = Field(..., description="Service name the alert refers to.")
    metric: str = Field(..., description="Metric that crossed its threshold.")
    value: float = Field(..., description="Observed value that triggered the alert.")
    threshold: float = Field(..., description="Configured alert threshold.")
    timestamp: str = Field(..., description="ISO-8601 timestamp of the anomaly.")


class InvestigationRequest(BaseModel):
    """Request body for ``POST /investigate``."""

    alert: AlertPayload
    time_range_minutes: int = Field(
        30,
        ge=1,
        le=180,
        description="Look-back window used by the agent's tool queries.",
    )


class RootCauseResult(BaseModel):
    """Structured root-cause summary for the dashboard."""

    service: str
    component: str | None = None
    confidence: float = Field(..., ge=0.0, le=1.0)


class InvestigationResponse(BaseModel):
    """Response body for ``POST /investigate`` and
    ``GET /investigations/{id}``.
    """

    investigation_id: str
    status: str = Field(..., description="'completed' | 'failed'.")
    root_cause: RootCauseResult | None = None
    top_3_predictions: list[str] = Field(default_factory=list)
    report: str | None = None
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    duration_seconds: float = 0.0
    started_at: str | None = None


class HealthComponent(BaseModel):
    """Per-dependency health verdict returned by ``GET /health``."""

    name: str
    status: str = Field(..., description="'connected' | 'available' | 'unreachable' | 'error'.")


class HealthStatus(BaseModel):
    """Aggregate health response."""

    status: str = Field(..., description="'healthy' | 'degraded'.")
    components: dict[str, str] = Field(default_factory=dict)


class TopologyNode(BaseModel):
    name: str
    attributes: dict[str, Any] = Field(default_factory=dict)


class TopologyEdge(BaseModel):
    source: str
    target: str
    attributes: dict[str, Any] = Field(default_factory=dict)


class TopologyResponse(BaseModel):
    """Response body for ``GET /topology`` (full graph or subgraph view)."""

    nodes: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)
    subgraph_of: str | None = Field(
        None,
        description="If this is a subgraph response, the service it was centred on.",
    )


class DemoInvestigationRequest(BaseModel):
    """Request body for ``POST /demo/investigate``.

    The user picks one of the six supported services; the API maps it to
    the canonical Session-13 fault script and orchestrates the full
    inject → wait → investigate → restore lifecycle.
    """

    service: DemoService = Field(
        ...,
        description="One of the six services with a mapped fault scenario.",
    )


class DemoInvestigationStatus(BaseModel):
    """Status snapshot for a demo investigation.

    Returned by ``GET /demo/investigations/{id}/status``. The dashboard
    polls this every ~2 s while a demo is active and uses ``phase`` to
    drive the real phase stepper (not the client-side animation).
    """

    investigation_id: str
    service: DemoService
    fault_type: str
    phase: DemoPhase
    phase_label: str
    progress_pct: int = Field(0, ge=0, le=100)
    started_at: str | None = None
    completed_at: str | None = None
    error: str | None = None
    result: InvestigationResponse | None = None


__all__ = [
    "AlertPayload",
    "DemoInvestigationRequest",
    "DemoInvestigationStatus",
    "DemoPhase",
    "DemoService",
    "HealthComponent",
    "HealthStatus",
    "InvestigationRequest",
    "InvestigationResponse",
    "RootCauseResult",
    "TopologyEdge",
    "TopologyNode",
    "TopologyResponse",
]
