"""Causal discovery orchestration tool for the OpsAgent investigation agent.

Orchestrates the full causal discovery pipeline: metric collection,
time-lag creation, PC algorithm execution, and counterfactual scoring.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import pandas as pd
from langchain_core.tools import tool

from src.causal_discovery.counterfactual import (
    calculate_counterfactual_confidence,
    compute_baseline_stats,
)
from src.causal_discovery.graph_utils import CausalEdge, CausalGraph
from src.causal_discovery.pc_algorithm import (
    create_time_lags,
    discover_causal_graph,
    parse_causal_graph,
)
from src.data_collection.metrics_collector import MetricsCollector

logger = logging.getLogger(__name__)

# Metrics to fetch per service for causal analysis
_CAUSAL_METRICS: dict[str, str] = {
    "cpu": 'rate(container_cpu_usage_seconds_total{service="{service}"}[1m])',
    "memory": 'container_memory_working_set_bytes{service="{service}"}',
    "net_rx": 'rate(container_network_receive_bytes_total{service="{service}"}[1m])',
    "net_tx": 'rate(container_network_transmit_bytes_total{service="{service}"}[1m])',
}


@tool
def discover_causation(
    services: list[str],
    time_range_minutes: int = 30,
) -> dict:
    """Run the PC causal discovery algorithm to identify causal relationships
    between services and compute counterfactual confidence scores.

    Use this tool AFTER narrowing suspects with query_metrics and search_logs.
    It is computationally expensive -- call at most once or twice per
    investigation.  Provide the top 3-5 suspect services, not the full system.

    This tool distinguishes OpsAgent from correlation-based tools: it
    identifies directional causal links (A causes B) rather than mere
    co-occurrence.

    Args:
        services: List of service names to include in the causal analysis.
            Include both suspected root cause AND affected downstream
            services.  Typically 3-5 services.
        time_range_minutes: Time window of metric data to analyze.
            Default 30 minutes.

    Returns:
        dict with keys: causal_edges, root_cause, root_cause_confidence,
        counterfactual, graph_ascii.
    """
    if len(services) < 2:
        return {
            "causal_edges": [],
            "root_cause": services[0] if services else "unknown",
            "root_cause_confidence": 0.0,
            "counterfactual": "Insufficient services for causal analysis (need >= 2).",
            "graph_ascii": "",
            "error": "At least 2 services are required for causal discovery.",
        }

    try:
        # 1. Fetch metrics from Prometheus for each service
        collector = MetricsCollector()
        end = datetime.now(UTC)
        start = end - timedelta(minutes=time_range_minutes)

        columns: dict[str, list[float]] = {}
        for service in services:
            svc_data = collector.get_service_metrics(
                service, _CAUSAL_METRICS, start, end, step="15s"
            )
            for metric_name, values in svc_data.items():
                columns[f"{service}_{metric_name}"] = values

        # Align all columns to the shortest length
        if not columns:
            return _inconclusive("No metric data returned from Prometheus.")

        min_len = min(len(v) for v in columns.values())
        if min_len < 10:
            return _inconclusive(f"Insufficient data points ({min_len}). Need at least 10.")

        metrics_df = pd.DataFrame({k: v[:min_len] for k, v in columns.items()})

        # 2. Create time-lagged features
        lagged_df = create_time_lags(metrics_df, lags=[1, 2, 5])
        if len(lagged_df) < 10:
            return _inconclusive("Too few rows after time-lag creation.")

        # 3. Run PC algorithm (depth capped at 4 to avoid combinatorial
        #    explosion — 32 columns at unrestricted depth can take 30+ min)
        cg = discover_causal_graph(lagged_df, alpha=0.05, max_conditioning_set=4)

        # 4. Parse directed edges
        edges = parse_causal_graph(cg, list(lagged_df.columns))
        if not edges:
            return _inconclusive("No directed causal edges discovered.")

        # 5. Compute baseline stats (first 60% of data)
        baseline_end = int(len(metrics_df) * 0.6)
        baseline_df = metrics_df.iloc[:baseline_end]
        baseline_stats = compute_baseline_stats(baseline_df)

        # 6. Counterfactual scoring for edges between original (non-lagged) columns
        anomaly_window = (baseline_end, len(metrics_df) - 1)
        scored_edges: list[CausalEdge] = []
        best_explanation = ""
        best_confidence = 0.0

        for edge in edges:
            # Extract base service names (strip _lag suffixes)
            src_base = _strip_lag(edge.source)
            tgt_base = _strip_lag(edge.target)

            if src_base in metrics_df.columns and tgt_base in metrics_df.columns:
                conf, explanation = calculate_counterfactual_confidence(
                    metrics_df, src_base, tgt_base, anomaly_window, baseline_stats
                )
                scored_edges.append(
                    CausalEdge(
                        source=edge.source,
                        target=edge.target,
                        confidence=conf,
                        lag=edge.lag,
                        evidence=explanation,
                    )
                )
                if conf > best_confidence:
                    best_confidence = conf
                    best_explanation = explanation
            else:
                scored_edges.append(edge)

        # 7. Identify root cause: highest-confidence source with no incoming edges
        sources = {e.source for e in scored_edges}
        targets = {e.target for e in scored_edges}
        root_candidates = sources - targets
        if root_candidates:
            root_cause = max(
                root_candidates,
                key=lambda s: max(
                    (e.confidence for e in scored_edges if e.source == s), default=0.0
                ),
            )
        elif scored_edges:
            top = max(scored_edges, key=lambda e: e.confidence)
            root_cause = top.source
        else:
            root_cause = "unknown"

        # Extract service name from metric column name (e.g. "cartservice_cpu" → "cartservice")
        root_service = _extract_service(root_cause, services)

        # 8. Build CausalGraph
        causal_graph = CausalGraph(
            edges=scored_edges,
            root_cause=root_service,
            root_cause_confidence=best_confidence,
        )

        return {
            "causal_edges": [
                {
                    "source": e.source,
                    "target": e.target,
                    "confidence": e.confidence,
                    "lag": e.lag,
                }
                for e in causal_graph.top_edges(10)
            ],
            "root_cause": root_service,
            "root_cause_confidence": best_confidence,
            "counterfactual": best_explanation or "No counterfactual analysis available.",
            "graph_ascii": causal_graph.to_ascii(),
        }
    except Exception:
        logger.exception("discover_causation failed")
        return _inconclusive("Causal discovery encountered an error.")


def _inconclusive(reason: str) -> dict:
    """Return an inconclusive result dict."""
    return {
        "causal_edges": [],
        "root_cause": "inconclusive",
        "root_cause_confidence": 0.0,
        "counterfactual": reason,
        "graph_ascii": "",
    }


def _strip_lag(column_name: str) -> str:
    """Strip _lagN suffix from a column name."""
    if "_lag" in column_name:
        return column_name.rsplit("_lag", 1)[0]
    return column_name


def _extract_service(column_name: str, known_services: list[str]) -> str:
    """Extract the service name from a metric column name.

    Tries to match against the known service list first, then falls back
    to splitting on underscore.
    """
    for svc in sorted(known_services, key=len, reverse=True):
        if column_name.startswith(svc):
            return svc
    return column_name.rsplit("_", 1)[0] if "_" in column_name else column_name
