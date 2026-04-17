"""AgentExecutor — entry point for OpsAgent investigations.

Wraps the compiled LangGraph and provides the ``investigate()`` method
called by both the FastAPI endpoint and the RCAEval evaluation runner.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml
from langchain_core.messages import HumanMessage, SystemMessage

from src.agent.graph import build_graph
from src.agent.prompts.system_prompt import SYSTEM_PROMPT

logger = logging.getLogger(__name__)


class AgentExecutor:
    """High-level wrapper around the compiled LangGraph investigation agent."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.graph = build_graph()

    @classmethod
    def from_config(cls, config_path: str) -> AgentExecutor:
        """Create an AgentExecutor from a YAML config file."""
        with Path(config_path).open() as f:
            config = yaml.safe_load(f)
        return cls(config)

    def investigate(
        self,
        alert: dict[str, Any],
        metrics: dict[str, Any] | None = None,
        logs: dict[str, Any] | None = None,
        anomaly_timestamp: str | None = None,
    ) -> dict[str, Any]:
        """Run a full RCA investigation.

        Supports two calling patterns:

        1. **Live / Fault Injection** — alert is provided; metrics/logs
           fetched by agent tools::

               agent.investigate(alert=alert)

        2. **Offline / RCAEval** — pre-loaded metrics and logs passed
           directly::

               agent.investigate(
                   alert={"timestamp": ts, "severity": "evaluation"},
                   metrics=case["metrics"],
                   logs=case["logs"],
                   anomaly_timestamp=case["anomaly_timestamp"],
               )

        Args:
            alert: Alert payload dict from AnomalyDetector.  Required.
            metrics: Optional dict of metric data keyed by service name.
            logs: Optional dict of log entries keyed by service name.
            anomaly_timestamp: ISO 8601 timestamp of anomaly detection.

        Returns:
            Dict with keys: root_cause, root_cause_confidence,
            top_3_predictions, confidence, rca_report, recommended_actions.
        """
        # Determine affected services
        if metrics is not None:
            affected_services = list(metrics.keys())
        else:
            affected_services = alert.get("affected_services", [])

        ts = anomaly_timestamp or alert.get("timestamp", "")

        agent_config = self.config.get("agent", {})
        investigation = agent_config.get("investigation", {})
        max_tool_calls = investigation.get("max_tool_calls", 10)

        initial_state: dict[str, Any] = {
            "alert": alert,
            "anomaly_window": (ts, ts),
            "affected_services": affected_services,
            "messages": [
                SystemMessage(content=SYSTEM_PROMPT),
                HumanMessage(content=self._format_alert(alert, affected_services, ts)),
            ],
            "hypotheses": [],
            "evidence": [],
            "tool_calls_remaining": max_tool_calls,
            "causal_graph": None,
            "root_cause": None,
            "root_cause_confidence": 0.0,
            "rca_report": None,
            "recommended_actions": [],
            "relevant_runbooks": [],
        }

        try:
            final_state = self.graph.invoke(initial_state)
        except Exception:
            logger.exception("Investigation failed")
            final_state = initial_state
            final_state["rca_report"] = "Investigation failed due to an error."
            final_state["root_cause"] = "unknown"

        return {
            "root_cause": final_state.get("root_cause"),
            "root_cause_confidence": final_state.get("root_cause_confidence", 0.0),
            "top_3_predictions": self._extract_top3(final_state),
            "confidence": final_state.get("root_cause_confidence", 0.0),
            "rca_report": final_state.get("rca_report"),
            "recommended_actions": final_state.get("recommended_actions", []),
        }

    def _format_alert(
        self,
        alert: dict[str, Any],
        affected_services: list[str],
        timestamp: str | None,
    ) -> str:
        """Format the alert payload into a readable initial message."""
        services = ", ".join(affected_services) if affected_services else "unknown"
        anomaly_score = alert.get("anomaly_score", "N/A")
        return (
            f"INCIDENT ALERT\n"
            f"Timestamp: {timestamp}\n"
            f"Affected services: {services}\n"
            f"Anomaly score: {anomaly_score}\n"
            f"Alert details: {alert}\n\n"
            f"Please investigate and identify the root cause."
        )

    def _extract_top3(self, state: dict[str, Any]) -> list[str]:
        """Extract top 3 hypothesis service names sorted by confidence.

        Falls back to root_cause and causal graph edges when hypotheses
        are empty (e.g., due to JSON parsing failure in the LLM response).
        """
        hypotheses = state.get("hypotheses", [])
        sorted_h = sorted(hypotheses, key=lambda h: h.get("confidence", 0), reverse=True)
        result = [h["service"] for h in sorted_h[:3] if "service" in h]

        # Fallback: ensure root_cause is in the list
        root = state.get("root_cause")
        if root and root not in ("unknown", "inconclusive") and root not in result:
            result.insert(0, root)

        # Fallback: pad from causal graph edges if still short
        if len(result) < 3:
            causal = state.get("causal_graph") or {}
            for edge in causal.get("causal_edges", []):
                for key in ("source", "target"):
                    svc = edge.get(key, "")
                    # Strip metric suffix (e.g., "redis_cpu" → "redis")
                    for known in (
                        "cartservice",
                        "checkoutservice",
                        "currencyservice",
                        "frontend",
                        "paymentservice",
                        "productcatalogservice",
                        "redis",
                    ):
                        if svc.startswith(known) and known not in result:
                            result.append(known)
                            break
                if len(result) >= 3:
                    break

        return result[:3]
