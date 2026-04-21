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
from src.agent.prompts.system_prompt import SYSTEM_PROMPT, SYSTEM_PROMPT_OFFLINE

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
        start_time: str | None = None,
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
            start_time: Optional ISO-8601 timestamp. When set, all tool
                queries use ``[start_time, start_time + time_range_minutes]``
                as their window instead of ``[now - time_range_minutes, now]``.
                The evaluation harness passes the fault-injection time so
                each test sees a window isolated from previous tests.

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

        # Select the system-prompt variant based on mode. The default
        # ``SYSTEM_PROMPT`` includes the Session-12 "currencyservice is
        # BROKEN IN BASELINE — never pick it as root cause" clause, which
        # is correct on the live OTel Demo (v1.10.0 SIGSEGV crash-loop
        # creates permanent probe_up=0 baseline noise). On RCAEval
        # offline runs there is no live image — the CSVs are pre-recorded
        # — so the clause is harmful: it caused 0/25 Recall@1 on
        # RE1-OB currencyservice cases in an earlier Session-15 run by
        # explicitly forbidding the LLM from naming the correct service.
        # ``SYSTEM_PROMPT_OFFLINE`` is identical but without the clause.
        system_prompt = SYSTEM_PROMPT_OFFLINE if metrics is not None else SYSTEM_PROMPT

        initial_state: dict[str, Any] = {
            "alert": alert,
            "anomaly_window": (ts, ts),
            "affected_services": affected_services,
            "start_time": start_time,
            # Offline mode: passing ``metrics=`` and ``logs=`` to
            # ``investigate()`` threads the preloaded DataFrames into state
            # so the graph's tool dispatchers can read from them instead of
            # hitting live Prometheus/Loki. ``None`` in both slots keeps
            # the live-mode path unchanged.
            "preloaded_metrics": metrics,
            "preloaded_logs": logs,
            "messages": [
                SystemMessage(content=system_prompt),
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
        """Format the alert payload into a readable initial message.

        The scope directive at the end is the highest-leverage anti-bias
        mitigation the agent has. Without it, the LLM tends to anchor on
        the services in the system-prompt examples (cartservice, frontend,
        redis, …) even when the actual incident involves a service that
        only exists in the pinned `affected_services` list — e.g. an
        RCAEval-OB `adservice_cpu` case whose metric DataFrame contains
        ``adservice`` but whose example fingerprints are OTel-Demo-centric.
        The directive makes the scoping explicit at the natural-language
        level, which the LLM respects far more reliably than vocabulary
        inference.
        """
        services = ", ".join(affected_services) if affected_services else "unknown"
        anomaly_score = alert.get("anomaly_score", "N/A")
        scope_directive = ""
        if affected_services:
            scope_directive = (
                f"\n\n**IMPORTANT — Scope your investigation strictly to the "
                f"Affected services listed above.** Your root-cause hypotheses "
                f"MUST name one of: {services}. Do NOT propose a service that "
                f"is not in this list, even if you recall it from previous "
                f"investigations or system-prompt examples."
            )
        return (
            f"INCIDENT ALERT\n"
            f"Timestamp: {timestamp}\n"
            f"Affected services: {services}\n"
            f"Anomaly score: {anomaly_score}\n"
            f"Alert details: {alert}\n\n"
            f"Please investigate and identify the root cause.{scope_directive}"
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

        # Fallback: pad from causal graph edges if still short.
        # Includes full OB vocabulary so RCAEval-OB runs can surface
        # adservice / emailservice / recommendationservice in top_3.
        # The list is ordered longest-first so e.g. "productcatalogservice_cpu"
        # matches "productcatalogservice", not a shorter prefix.
        if len(result) < 3:
            causal = state.get("causal_graph") or {}
            known_services = sorted(
                (
                    "cartservice",
                    "checkoutservice",
                    "currencyservice",
                    "frontend",
                    "paymentservice",
                    "productcatalogservice",
                    "redis",
                    "adservice",
                    "emailservice",
                    "recommendationservice",
                    "shippingservice",
                ),
                key=len,
                reverse=True,
            )
            for edge in causal.get("causal_edges", []):
                for key in ("source", "target"):
                    svc = edge.get(key, "")
                    # Strip metric suffix (e.g., "redis_cpu" → "redis")
                    for known in known_services:
                        if svc.startswith(known) and known not in result:
                            result.append(known)
                            break
                if len(result) >= 3:
                    break

        return result[:3]
