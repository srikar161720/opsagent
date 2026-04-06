"""LangGraph agent state definition for OpsAgent investigations.

Defines the shared state TypedDict passed between all graph nodes during
a root cause analysis investigation.
"""

from __future__ import annotations

from typing import Annotated

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
