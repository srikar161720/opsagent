"""LangGraph agent state definition for OpsAgent investigations.

Defines the shared state TypedDict passed between all graph nodes during
a root cause analysis investigation.
"""

from __future__ import annotations

from typing import Annotated, Any

from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class AgentState(TypedDict):
    """Shared state for the OpsAgent investigation graph.

    Fields are grouped into four categories:

    **Input** — populated from the anomaly alert before the graph starts.
    **Investigation** — accumulated during the multi-step investigation.
    **Causal** — populated by the causal discovery node.
    **Output** — the final RCA report and recommendations.

    The ``messages`` field uses the ``add_messages`` reducer so new messages
    are appended (not overwritten).  All other fields use last-write-wins.
    """

    # ── Input ─────────────────────────────────────────────────────────────
    alert: dict
    anomaly_window: tuple
    affected_services: list[str]
    # Optional pinned start time for all metric/log queries during this
    # investigation. When set, tools query the window [start_time,
    # start_time + time_range_minutes] instead of [now - time_range_minutes,
    # now]. This isolates per-test metric windows from cross-test pollution.
    start_time: str | None

    # Offline-mode fields. When set, the graph's tool dispatchers read from
    # these pre-loaded DataFrames instead of making live HTTP calls to
    # Prometheus/Loki. Used by ``tests/evaluation/rcaeval_evaluation.py`` to
    # run the full agent pipeline against RCAEval's historical case data.
    # Both default to ``None`` for live-mode invocations, which preserves
    # the bit-for-bit behaviour of the OTel Demo fault-injection path.
    preloaded_metrics: dict[str, Any] | None
    preloaded_logs: Any | None

    # ── Investigation state ───────────────────────────────────────────────
    messages: Annotated[list, add_messages]
    hypotheses: list[dict]
    evidence: list[dict]
    tool_calls_remaining: int

    # ── Causal analysis ───────────────────────────────────────────────────
    causal_graph: dict | None
    root_cause: str | None
    root_cause_confidence: float

    # ── Output ────────────────────────────────────────────────────────────
    rca_report: str | None
    recommended_actions: list[str]
    relevant_runbooks: list[dict]
