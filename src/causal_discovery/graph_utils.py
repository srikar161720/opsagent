"""Causal graph dataclasses for structured RCA output.

Defines the canonical output types consumed by the LangGraph agent's
``discover_causation`` tool and serialized into RCA reports.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CausalEdge:
    """A single directed causal relationship between two services."""

    source: str
    """Cause service/metric name (e.g. ``"cartservice_cpu"``)."""

    target: str
    """Effect service/metric name (e.g. ``"frontend_latency"``)."""

    confidence: float
    """Counterfactual confidence in ``[0.0, 1.0]``."""

    lag: int = 0
    """Time lag in windows at which this edge was detected."""

    evidence: str = ""
    """Human-readable supporting evidence for the RCA report."""


@dataclass
class CausalGraph:
    """Collection of causal edges with an identified root cause."""

    edges: list[CausalEdge] = field(default_factory=list)
    root_cause: str = ""
    """Most likely root cause service/metric."""

    root_cause_confidence: float = 0.0
    """Confidence for the root cause claim."""

    def to_ascii(self) -> str:
        """Render a simple ASCII causal graph for embedding in the RCA report.

        Example output::

            cartservice [ROOT CAUSE — confidence: 87%]
              └─[lag=1w, conf=82%]→ checkoutservice
              └─[lag=2w, conf=71%]→ frontend
        """
        if not self.edges:
            return "  (no causal edges discovered)"

        lines = [f"  {self.root_cause} [ROOT CAUSE — confidence: {self.root_cause_confidence:.0%}]"]
        for edge in self.edges:
            if edge.source == self.root_cause:
                lines.append(f"    └─[lag={edge.lag}w, conf={edge.confidence:.0%}]→ {edge.target}")
        return "\n".join(lines)

    def top_edges(self, n: int = 3) -> list[CausalEdge]:
        """Return the *n* highest-confidence edges, sorted descending."""
        return sorted(self.edges, key=lambda e: e.confidence, reverse=True)[:n]
