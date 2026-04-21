"""Causal discovery orchestration tool for the OpsAgent investigation agent.

Orchestrates the full causal discovery pipeline: metric collection,
time-lag creation, PC algorithm execution, and counterfactual scoring.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import numpy as np
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

# Metrics to fetch per service for causal analysis.
# Spanmetrics (request_rate / error_rate / latency) were REMOVED because they
# are only available for services that export traces (frontend, checkoutservice,
# productcatalogservice, paymentservice). Including them caused PC to bail on
# every multi-service investigation because non-trace services returned empty
# series. Container + probe metrics are available for ALL 7 services.
# Column count: 6 metrics × 5 services × 2 lags = 60 columns — manageable.
_CAUSAL_METRICS: dict[str, str] = {
    # Container-level metrics (available for all services)
    "cpu": 'rate(container_cpu_usage_seconds_total{service="{service}"}[1m])',
    "memory": 'container_memory_working_set_bytes{service="{service}"}',
    "net_rx": 'rate(container_network_receive_bytes_total{service="{service}"}[1m])',
    "net_tx": 'rate(container_network_transmit_bytes_total{service="{service}"}[1m])',
    # Service probe metrics (available for all services)
    "probe_up": 'service_probe_up{service="{service}"}',
    "probe_latency": 'service_probe_duration_seconds{service="{service}"}',
}


@tool
def discover_causation(
    services: list[str],
    time_range_minutes: int = 30,
    start_time: str | None = None,
    critical_services: list[str] | None = None,
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
        start_time: Optional ISO-8601 timestamp. When set, the query window
            is ``[start_time, start_time + time_range_minutes]`` instead of
            the default ``[now - time_range_minutes, now]``. Used by the
            evaluation harness to pin a per-test window.
        critical_services: Services flagged CRITICAL elsewhere in the
            investigation (probe_up=0, stale metrics, etc.). These services
            are EXCLUDED from the PC input because their metrics are frozen
            or missing (PC would see constant columns and either drop them
            or produce spurious causation signals). Instead, the tool
            synthesizes high-confidence edges ``critical_svc → other_svc``
            after PC runs, reflecting the assumption that a down service
            is the likely upstream cause of anomalies in surviving services.

    Returns:
        dict with keys: causal_edges, root_cause, root_cause_confidence,
        counterfactual, graph_ascii.
    """
    critical_set = set(critical_services or [])
    if len(services) < 2:
        return {
            "causal_edges": [],
            "root_cause": services[0] if services else "unknown",
            "root_cause_confidence": 0.0,
            "counterfactual": "Insufficient services for causal analysis (need >= 2).",
            "graph_ascii": "",
            "error": "At least 2 services are required for causal discovery.",
        }

    # Hard-cap at 5 services to prevent combinatorial explosion in PC.
    # With 4 metrics × 5 services × 4 lag levels = 80 columns — manageable.
    # More than 5 services produces 100+ columns and can take 30+ minutes.
    if len(services) > 5:
        logger.warning(
            "Capping causal discovery to 5 services (received %d). "
            "Pass your top suspects, not the full system.",
            len(services),
        )
        services = services[:5]

    try:
        # 1. Fetch metrics from Prometheus for each service
        collector = MetricsCollector()
        now = datetime.now(UTC)
        if start_time:
            anchor = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
            if anchor.tzinfo is None:
                anchor = anchor.replace(tzinfo=UTC)
            # Extend window 60s before the anchor (fault injection time) so
            # the baseline-stats calculation (first 60% of window) reflects
            # pre-fault normalcy. Matches query_metrics semantics.
            start = anchor - timedelta(seconds=60)
            end = min(anchor + timedelta(minutes=time_range_minutes), now)
        else:
            end = now
            start = end - timedelta(minutes=time_range_minutes)

        columns: dict[str, list[float]] = {}
        # PC input excludes critical services — their metrics are frozen or
        # missing and would produce a singular correlation matrix or
        # spurious signals. We still fetch their data so we can reason about
        # them in the synthesized edges below.
        pc_services = [s for s in services if s not in critical_set]
        for service in pc_services:
            svc_data = collector.get_service_metrics(
                service, _CAUSAL_METRICS, start, end, step="15s"
            )
            for metric_name, values in svc_data.items():
                columns[f"{service}_{metric_name}"] = values

        return _run_pc_from_columns(
            columns=columns,
            services=services,
            pc_services=pc_services,
            critical_set=critical_set,
        )
    except Exception:
        logger.exception("discover_causation failed")
        return _inconclusive("Causal discovery encountered an error.")


def _run_pc_from_columns(
    columns: dict[str, list[float]],
    services: list[str],
    pc_services: list[str],
    critical_set: set[str],
) -> dict:
    """Core PC + counterfactual pipeline on an already-built ``columns`` dict.

    Extracted from ``discover_causation()`` so both the live (Prometheus-
    sourced) path and the offline (preloaded-DataFrame) path share identical
    downstream logic. The input ``columns`` dict keys are
    ``f"{service}_{metric}"`` and values are aligned numeric series.

    Args:
        columns: service-metric time series, already aligned in length.
        services: full user-supplied service list (used for service-name
            extraction from metric columns).
        pc_services: services actually passed to PC (``services`` minus
            ``critical_set``).
        critical_set: services flagged CRITICAL elsewhere. Used for edge
            synthesis + root-cause override.

    Returns:
        Same dict shape as ``discover_causation.invoke()``:
        ``{causal_edges, root_cause, root_cause_confidence, counterfactual,
        graph_ascii}``.
    """
    # Align all columns to the shortest length.
    # Soft gate: allow partial data. Previously required ALL columns >= 10
    # points, which bailed whenever any metric was briefly unavailable.
    # New rule: min_len >= 6 AND at most 30% of columns are "short" (<6).
    if not columns:
        return _inconclusive("No metric data returned from Prometheus.")

    lengths = [len(v) for v in columns.values()]
    min_len = min(lengths)
    short_count = sum(1 for length in lengths if length < 6)
    short_frac = short_count / len(lengths)
    if min_len < 6 or short_frac > 0.30:
        return _inconclusive(
            f"Insufficient data points (min={min_len}, "
            f"{short_count}/{len(lengths)} columns are short). "
            f"Need min>=6 and <=30% short columns."
        )

    # Truncate all columns to the shortest usable length so the DataFrame
    # is rectangular. Use min_len — not a "compromise" value — to keep the
    # alignment exact.
    metrics_df = pd.DataFrame({k: v[:min_len] for k, v in columns.items()})

    # Drop zero-variance columns before causal discovery.
    # network_rx/tx_errors_rate are zero during normal operation, and
    # crashed services return constant (all-zero) metrics. These cause
    # a singular correlation matrix in Fisher's Z test.
    variance = metrics_df.var()
    zero_var_cols = variance[variance < 1e-12].index.tolist()
    if zero_var_cols:
        logger.info(
            "Dropping %d zero-variance columns: %s",
            len(zero_var_cols),
            zero_var_cols[:5],
        )
        metrics_df = metrics_df.drop(columns=zero_var_cols)

    if metrics_df.shape[1] < 2:
        return _inconclusive("Fewer than 2 non-constant metric columns after filtering.")

    # 2. Create time-lagged features
    # Use lags=[1, 2] instead of [1, 2, 5] to reduce column count.
    lagged_df = create_time_lags(metrics_df, lags=[1, 2])
    # Lowered from 10 to 8 to match the softer input gate above.
    if len(lagged_df) < 8:
        return _inconclusive("Too few rows after time-lag creation.")

    # Drop zero-variance lagged columns
    lag_var = lagged_df.var()
    zero_var_lagged = lag_var[lag_var < 1e-12].index.tolist()
    if zero_var_lagged:
        lagged_df = lagged_df.drop(columns=zero_var_lagged)

    # Drop perfectly correlated columns (r > 0.999).
    # Lagged copies of slow-changing metrics (e.g. memory) are nearly
    # identical, making the correlation matrix singular even though no
    # single column is zero-variance.
    lagged_df = _drop_correlated_columns(lagged_df, threshold=0.999)

    if lagged_df.shape[1] < 2:
        return _inconclusive("Fewer than 2 independent columns after correlation filtering.")

    # Add tiny jitter as a safety net against remaining near-singularities
    rng = np.random.default_rng(42)
    lagged_df = lagged_df + rng.normal(0, 1e-8, lagged_df.shape)

    # 3. Run PC algorithm (depth capped at 3 to avoid combinatorial
    #    explosion with many columns)
    cg = discover_causal_graph(lagged_df, alpha=0.05, max_conditioning_set=3)

    # 4. Parse directed edges
    edges = parse_causal_graph(cg, list(lagged_df.columns))
    # Note: if edges is empty AND no critical_services are provided, we
    # bail out later. If critical_services ARE provided, we proceed with
    # synthesized edges even when PC found nothing.
    if not edges and not critical_set:
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
            key=lambda s: max((e.confidence for e in scored_edges if e.source == s), default=0.0),
        )
    elif scored_edges:
        top = max(scored_edges, key=lambda e: e.confidence)
        root_cause = top.source
    else:
        root_cause = "unknown"

    # Extract service name from metric column name (e.g. "cartservice_cpu" → "cartservice")
    root_service = _extract_service(root_cause, services)

    # 7b. CRITICAL-service priors: synthesize high-confidence edges from
    # each critical (DOWN) service to each surviving service. Downstream
    # targets are derived from PC edges when available, otherwise from
    # the non-critical service list (so synthesis still runs when PC is
    # inconclusive). Override root_cause to a critical service.
    if critical_set:
        downstream_services: set[str] = set()
        if scored_edges:
            downstream_services = {_extract_service(e.target, services) for e in scored_edges}
            downstream_services.update({_extract_service(e.source, services) for e in scored_edges})
        # Fall back to the non-critical service list if PC gave us nothing.
        if not downstream_services:
            downstream_services = set(pc_services)
        # Remove critical services from the set — don't synthesize self-edges.
        downstream_services -= critical_set
        for crit_svc in critical_set:
            for tgt_svc in downstream_services:
                scored_edges.append(
                    CausalEdge(
                        source=f"{crit_svc}_probe_up",
                        target=f"{tgt_svc}_probe_up",
                        confidence=0.90,
                        lag=1,
                        evidence=(
                            f"{crit_svc} is DOWN per probe_up CRITICAL — "
                            f"synthesized edge to downstream {tgt_svc}"
                        ),
                    )
                )
        # Override root cause to the critical service with strong confidence.
        # Pick the first critical service (stable via sorted set below).
        root_service = sorted(critical_set)[0]
        best_confidence = max(best_confidence, 0.75)
        best_explanation = (
            f"Root cause override: {root_service} is DOWN (probe_up CRITICAL). "
            f"Synthesized edges from {root_service} to "
            f"{len(downstream_services)} downstream service(s). " + best_explanation
        )

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


def _drop_correlated_columns(df: pd.DataFrame, threshold: float = 0.999) -> pd.DataFrame:
    """Drop columns that are perfectly (or near-perfectly) correlated.

    For each pair with |r| > threshold, the second column is dropped.
    This prevents singular correlation matrices in Fisher's Z test.
    """
    corr = df.corr().abs()
    upper = corr.where(np.triu(np.ones(corr.shape, dtype=bool), k=1))
    to_drop = [col for col in upper.columns if any(upper[col] > threshold)]
    if to_drop:
        logger.info("Dropping %d correlated columns: %s", len(to_drop), to_drop[:5])
    return df.drop(columns=to_drop)


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
