"""Offline-mode helpers for the OpsAgent graph's tool dispatchers.

When the graph runs against preloaded case data (e.g. from the RCAEval
evaluation harness) instead of a live Docker stack, the three tools that
normally hit Prometheus/Loki over HTTP are short-circuited to read from
in-memory DataFrames instead. Each helper returns a dict with the EXACT
same shape as its live counterpart so downstream graph nodes don't need
to branch on which mode they're in.

Three helpers:

- :func:`query_preloaded_metrics` — mirrors
  :func:`src.agent.tools.query_metrics.query_metrics`.
- :func:`search_preloaded_logs` — mirrors
  :func:`src.agent.tools.search_logs.search_logs`.
- :func:`discover_causation_from_df` — mirrors
  :func:`src.agent.tools.discover_causation.discover_causation`.

Column-name normalization maps RCAEval's three dialects (RE1-OB simple,
RE1-SS/TT container, RE2/RE3 container) to OpsAgent canonical metric
names (``cpu_usage``, ``memory_usage``, ``network_rx_bytes_rate``, …) so
the helpers can be called with canonical names regardless of source
dataset.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd

from src.agent.tools.discover_causation import _run_pc_from_columns
from src.agent.tools.search_logs import _detect_crash_signal
from src.preprocessing.rcaeval_adapter import _SIMPLE_METRIC_RENAME

logger = logging.getLogger(__name__)


# ── Metric-name normalization ────────────────────────────────────────────────

# Reverse of rcaeval_adapter._SIMPLE_METRIC_RENAME — maps canonical OpsAgent
# names back to the simple RE1-OB suffixes after rename. After the adapter
# renames columns, a RE1-OB DataFrame already contains ``cpu_usage`` etc.
# directly, so this dict is effectively used for alias lookups.
_SIMPLE_CANONICAL_ALIASES: dict[str, list[str]] = {
    "cpu_usage": ["cpu_usage", "cpu"],
    "memory_usage": ["memory_usage", "mem", "memory_working_set_bytes"],
    "load_average": ["load_average", "load"],
    "latency_p99": ["latency_p99", "latency"],
    "error_rate": ["error_rate", "error"],
}

# Regex fragments matching RCAEval container-metric column names for each
# canonical OpsAgent metric. Matched against the service-stripped column
# name (the adapter's ``_split_container_format`` removes the
# ``{service}_`` prefix). Both ``-`` and ``_`` separators are accepted
# because RCAEval's three variants use slightly different punctuation.
_CONTAINER_CANONICAL_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "cpu_usage": [
        re.compile(r"container[-_]cpu[-_]usage[-_]seconds[-_]total", re.IGNORECASE),
        re.compile(r"container[-_]cpu[-_]user[-_]seconds[-_]total", re.IGNORECASE),
        re.compile(r"container[-_]cpu[-_]system[-_]seconds[-_]total", re.IGNORECASE),
    ],
    "memory_usage": [
        re.compile(r"container[-_]memory[-_]working[-_]set[-_]bytes", re.IGNORECASE),
        re.compile(r"container[-_]memory[-_]usage[-_]bytes", re.IGNORECASE),
        re.compile(r"container[-_]memory[-_]rss", re.IGNORECASE),
    ],
    "network_rx_bytes_rate": [
        re.compile(r"container[-_]network[-_]receive[-_]bytes[-_]total", re.IGNORECASE),
        re.compile(r"network[-_]rx[-_]bytes", re.IGNORECASE),
    ],
    "network_tx_bytes_rate": [
        re.compile(r"container[-_]network[-_]transmit[-_]bytes[-_]total", re.IGNORECASE),
        re.compile(r"network[-_]tx[-_]bytes", re.IGNORECASE),
    ],
    "network_rx_errors_rate": [
        re.compile(r"container[-_]network[-_]receive[-_]errors[-_]total", re.IGNORECASE),
        re.compile(r"network[-_]rx[-_]errors", re.IGNORECASE),
    ],
    "network_tx_errors_rate": [
        re.compile(r"container[-_]network[-_]transmit[-_]errors[-_]total", re.IGNORECASE),
        re.compile(r"network[-_]tx[-_]errors", re.IGNORECASE),
    ],
    "latency_p99": [
        re.compile(r"latency[-_]p99", re.IGNORECASE),
        re.compile(r"duration[-_]seconds[-_]p99", re.IGNORECASE),
    ],
    "error_rate": [
        re.compile(r"error[-_]rate", re.IGNORECASE),
        re.compile(r"requests[-_]total.*[{_]status", re.IGNORECASE),
    ],
    "request_rate": [
        re.compile(r"request[-_]rate", re.IGNORECASE),
        re.compile(r"requests[-_]total(?!.*error)", re.IGNORECASE),
    ],
}

# Metrics that never exist in RCAEval (OTel-Demo-specific exporter output).
# query_preloaded_metrics returns a neutral "no data available" note for
# these so the caller can degrade gracefully.
_OFFLINE_UNAVAILABLE_METRICS = frozenset(
    {
        "probe_up",
        "probe_latency",
        "memory_limit",
        "memory_utilization",
    }
)

# Sanity assertion: every simple rename value lives in the aliases dict so
# unit tests that rely on the RE1-OB path can resolve without surprise.
for _canonical in _SIMPLE_METRIC_RENAME.values():
    assert _canonical in _SIMPLE_CANONICAL_ALIASES, (
        f"simple canonical {_canonical!r} missing from aliases"
    )


def canonical_metric_series(
    service_df: pd.DataFrame,
    canonical_metric: str,
) -> pd.Series:
    """Extract the Series best matching ``canonical_metric`` from a service DF.

    Tries three strategies in order:

    1. **Simple-alias match** — if any column name is in the canonical's
       alias list (e.g. ``cpu_usage`` → match column ``cpu_usage`` or ``cpu``).
    2. **Container-pattern match** — if any column matches a regex fragment
       from ``_CONTAINER_CANONICAL_PATTERNS[canonical_metric]``.
    3. Return an empty Series if no match.

    The returned Series is numeric; non-numeric values (strings, NaN) are
    coerced to NaN and then dropped before the caller applies statistics.
    """
    if canonical_metric in _SIMPLE_CANONICAL_ALIASES:
        for alias in _SIMPLE_CANONICAL_ALIASES[canonical_metric]:
            if alias in service_df.columns:
                return pd.to_numeric(service_df[alias], errors="coerce").dropna()

    patterns = _CONTAINER_CANONICAL_PATTERNS.get(canonical_metric, [])
    for pattern in patterns:
        for col in service_df.columns:
            if pattern.search(col):
                return pd.to_numeric(service_df[col], errors="coerce").dropna()

    return pd.Series(dtype=float)


# ── query_metrics offline helper ─────────────────────────────────────────────


def _timestamp_series(service_df: pd.DataFrame, n_values: int) -> list[str]:
    """Extract ISO-8601 timestamps from a service DF, or synthesise them."""
    if "timestamp" in service_df.columns and len(service_df) >= n_values:
        ts_series = service_df["timestamp"]
        out: list[str] = []
        for value in ts_series.iloc[:n_values]:
            if isinstance(value, int | float) and not np.isnan(float(value)):
                out.append(datetime.fromtimestamp(float(value), tz=UTC).isoformat())
            else:
                out.append(str(value))
        return out

    # Fall back: synthesize a monotonically-increasing timestamp sequence
    # at 15s intervals so the downstream stale/sparse checks have
    # something plausible to work with.
    base = datetime.now(UTC) - timedelta(seconds=15 * n_values)
    return [(base + timedelta(seconds=15 * i)).isoformat() for i in range(n_values)]


def _slice_window(
    values: list[float],
    timestamps: list[str],
    time_range_minutes: int,
    start_time: str | None,
) -> tuple[list[float], list[str]]:
    """Apply the pinned-window slicing if ``start_time`` is set.

    Mirrors the window semantics used in ``query_metrics.py``: the slice
    spans ``[anchor - 60s, anchor + time_range_minutes]`` when
    ``start_time`` is pinned, otherwise the full series is returned.
    """
    if not start_time or not timestamps:
        return values, timestamps

    try:
        anchor = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        if anchor.tzinfo is None:
            anchor = anchor.replace(tzinfo=UTC)
    except (ValueError, AttributeError):
        return values, timestamps

    window_start = anchor - timedelta(seconds=60)
    window_end = anchor + timedelta(minutes=time_range_minutes)

    kept_values: list[float] = []
    kept_ts: list[str] = []
    for value, ts_str in zip(values, timestamps, strict=False):
        try:
            ts_dt = datetime.fromisoformat(ts_str)
            if ts_dt.tzinfo is None:
                ts_dt = ts_dt.replace(tzinfo=UTC)
        except ValueError:
            # Unparseable timestamp — keep the point rather than silently
            # dropping the whole series.
            kept_values.append(value)
            kept_ts.append(ts_str)
            continue
        if window_start <= ts_dt <= window_end:
            kept_values.append(value)
            kept_ts.append(ts_str)

    return kept_values, kept_ts


def _no_data_response(
    service_name: str,
    metric_name: str,
    reason: str,
) -> dict:
    """Build the neutral "no data available" response shape.

    Mirrors ``query_metrics.py``'s behaviour for empty container metrics:
    ``anomalous=False`` plus a descriptive note. Keeps the downstream
    CRITICAL-override path from misfiring on missing data.
    """
    return {
        "timestamps": [],
        "values": [],
        "stats": {},
        "anomalous": False,
        "note": reason,
        "service": service_name,
        "metric": metric_name,
    }


def query_preloaded_metrics(
    service_name: str,
    metric_name: str,
    time_range_minutes: int = 30,
    start_time: str | None = None,
    preloaded_metrics: dict[str, pd.DataFrame] | None = None,
) -> dict:
    """Offline analogue of :func:`src.agent.tools.query_metrics.query_metrics`.

    Reads from ``preloaded_metrics[service_name]`` instead of Prometheus.
    Applies the subset of CRITICAL detectors that make sense for historical
    data: 2σ anomaly flag and frozen-metric detection (5+ of last 8 rate
    values at zero with prior activity). Probe / memory-utilization
    detectors are dropped because those signals don't exist in RCAEval data.

    Args:
        service_name: Canonical service name (e.g. ``"cartservice"``).
        metric_name: Canonical metric name from
            :data:`src.agent.tools.query_metrics.METRIC_PROMQL`.
        time_range_minutes: Window length for ``start_time`` pinning.
        start_time: Optional ISO-8601 anchor; same semantics as the live tool.
        preloaded_metrics: Dict of per-service DataFrames from
            :meth:`src.preprocessing.rcaeval_adapter.RCAEvalDataAdapter.load_case`.

    Returns:
        Same dict shape as the live tool: ``{timestamps, values, stats,
        anomalous, note?, service, metric}``.
    """
    if preloaded_metrics is None:
        return _no_data_response(
            service_name,
            metric_name,
            f"offline mode with no preloaded metrics for {service_name}",
        )

    if service_name not in preloaded_metrics:
        return _no_data_response(
            service_name,
            metric_name,
            f"No preloaded data for service {service_name}. "
            f"Available services: {sorted(preloaded_metrics.keys())[:5]}",
        )

    # Metrics that don't exist in RCAEval data. Return neutral response so
    # downstream string-scans for "CRITICAL" don't misfire.
    if metric_name in _OFFLINE_UNAVAILABLE_METRICS:
        return _no_data_response(
            service_name,
            metric_name,
            f"{metric_name} is an OTel-Demo-specific exporter signal and is "
            f"not available in offline RCAEval data for {service_name}. "
            f"Use container-level metrics (cpu_usage, memory_usage, "
            f"network_rx_bytes_rate, network_tx_bytes_rate) instead.",
        )

    svc_df = preloaded_metrics[service_name]
    series = canonical_metric_series(svc_df, metric_name)
    if series.empty:
        return _no_data_response(
            service_name,
            metric_name,
            f"No column matching canonical metric {metric_name!r} found in "
            f"preloaded data for {service_name}.",
        )

    all_values = [float(v) for v in series.tolist()]
    all_timestamps = _timestamp_series(svc_df, len(all_values))
    values, timestamps = _slice_window(all_values, all_timestamps, time_range_minutes, start_time)

    if not values:
        return _no_data_response(
            service_name,
            metric_name,
            f"No data points in the pinned window "
            f"[start_time={start_time}, +{time_range_minutes}min] for "
            f"{service_name}/{metric_name}.",
        )

    arr = np.array(values)
    mean = float(np.mean(arr))
    std = float(np.std(arr))
    current = values[-1]
    anomalous = bool(abs(current - mean) > 2 * std) if std > 0 else False

    # Baseline split. With start_time pinned we know the anchor; otherwise
    # fall back to the first 60% of the window (matches live query_metrics).
    if start_time:
        try:
            anchor = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
            if anchor.tzinfo is None:
                anchor = anchor.replace(tzinfo=UTC)
            baseline_end = 0
            for ts_str in timestamps:
                try:
                    ts_dt = datetime.fromisoformat(ts_str)
                    if ts_dt.tzinfo is None:
                        ts_dt = ts_dt.replace(tzinfo=UTC)
                except ValueError:
                    break
                if ts_dt < anchor:
                    baseline_end += 1
        except (ValueError, AttributeError):
            baseline_end = int(len(values) * 0.6)
    else:
        baseline_end = int(len(values) * 0.6)

    baseline_mean = float(np.mean(arr[:baseline_end])) if baseline_end >= 2 else mean

    # Frozen-metric detection for rate-style metrics. Offline data won't
    # have Prometheus rate() semantics so the heuristic is weaker here, but
    # a crashed service's post-fault rows typically show 5+ trailing zeros
    # when the collector stopped feeding them.
    _rate_metrics = {
        "cpu_usage",
        "network_rx_bytes_rate",
        "network_tx_bytes_rate",
    }
    if metric_name in _rate_metrics and len(values) >= 8:
        recent = values[-8:]
        zero_count = sum(1 for v in recent if v == 0.0)
        mostly_zero = zero_count >= 5
        had_activity = mean > 0.0001
        if mostly_zero and had_activity:
            return {
                "timestamps": timestamps,
                "values": values,
                "stats": {
                    "min": float(np.min(arr)),
                    "max": float(np.max(arr)),
                    "mean": mean,
                    "std": std,
                    "current": current,
                    "baseline_mean": baseline_mean,
                },
                "anomalous": True,
                "frozen": True,
                "note": (
                    f"CRITICAL: {service_name}/{metric_name} dropped to zero in "
                    f"the offline window (last 8 readings: {zero_count} zeros; "
                    f"historical mean was {mean:.6f}). This indicates the "
                    f"service process is FROZEN or PAUSED during the fault window. "
                    f"This service should be considered a top root cause candidate."
                ),
                "service": service_name,
                "metric": metric_name,
            }

    return {
        "timestamps": timestamps,
        "values": values,
        "stats": {
            "min": float(np.min(arr)),
            "max": float(np.max(arr)),
            "mean": mean,
            "std": std,
            "current": current,
            "baseline_mean": baseline_mean,
        },
        "anomalous": anomalous,
        "service": service_name,
        "metric": metric_name,
    }


# ── search_logs offline helper ───────────────────────────────────────────────


def _empty_log_response() -> dict:
    """Default empty-logs response, matching the live tool's error path."""
    return {
        "entries": [],
        "total_count": 0,
        "error_count": 0,
        "top_patterns": [],
        "crash_match_count": 0,
    }


def _detect_log_service_column(logs_df: pd.DataFrame) -> str | None:
    """Detect which column in a logs DataFrame carries the service name."""
    for candidate in ("service", "container", "pod", "container_name", "job"):
        if candidate in logs_df.columns:
            return candidate
    return None


def _detect_log_message_column(logs_df: pd.DataFrame) -> str | None:
    """Detect which column in a logs DataFrame carries the log message body."""
    for candidate in ("message", "log", "body", "content", "line"):
        if candidate in logs_df.columns:
            return candidate
    return None


def _detect_log_timestamp_column(logs_df: pd.DataFrame) -> str | None:
    """Detect the log timestamp column."""
    for candidate in ("timestamp", "time", "ts", "@timestamp"):
        if candidate in logs_df.columns:
            return candidate
    return None


def search_preloaded_logs(
    query: str,
    service_filter: str | None = None,
    time_range_minutes: int = 30,
    limit: int = 100,
    start_time: str | None = None,
    preloaded_logs: pd.DataFrame | None = None,
) -> dict:
    """Offline analogue of :func:`src.agent.tools.search_logs.search_logs`.

    Scans a pre-loaded log DataFrame for messages matching ``query``.
    Supports the same OR-alternation syntax as the live tool. Returns the
    same dict shape so ``gather_evidence_node`` and ``sweep_probes_node``
    can process the result identically.

    Gracefully returns an empty response when ``preloaded_logs is None``
    (the RE1 case where ``logs.csv`` doesn't exist).
    """
    if preloaded_logs is None or preloaded_logs.empty:
        return _empty_log_response()

    message_col = _detect_log_message_column(preloaded_logs)
    if message_col is None:
        logger.warning(
            "search_preloaded_logs: no message column found (tried message, "
            "log, body, content, line)",
        )
        return _empty_log_response()

    service_col = _detect_log_service_column(preloaded_logs)
    ts_col = _detect_log_timestamp_column(preloaded_logs)

    df = preloaded_logs
    if service_filter and service_col:
        df = df[df[service_col].astype(str).str.contains(service_filter, na=False)]

    if df.empty:
        return _empty_log_response()

    # Build the regex from the query — mirrors search_logs._build_logql's
    # OR-alternation handling. Single-term queries become a literal-
    # substring match (case-insensitive).
    parts = [p.strip().strip('"').strip("`") for p in re.split(r"\s+OR\s+", query)]
    parts = [p for p in parts if p]
    pattern: re.Pattern[str] | None
    if len(parts) > 1:
        pattern = re.compile(
            "|".join(re.escape(p) for p in parts),
            re.IGNORECASE,
        )
    else:
        term = query.strip().strip('"').strip("`")
        pattern = re.compile(re.escape(term), re.IGNORECASE) if term else None

    entries: list[dict] = []
    pattern_counter: Counter[str] = Counter()
    error_count = 0

    # Iterate row-wise; capped at ``limit`` matching entries.
    for _, row in df.iterrows():
        if len(entries) >= limit:
            break
        message = str(row[message_col]) if pd.notna(row[message_col]) else ""
        if pattern is not None and not pattern.search(message):
            continue

        ts_value = str(row[ts_col]) if ts_col and pd.notna(row[ts_col]) else ""
        svc_value = (
            str(row[service_col]) if service_col and pd.notna(row[service_col]) else "unknown"
        )

        level = _guess_log_level(message)
        if level in ("ERROR", "CRITICAL"):
            error_count += 1

        entries.append(
            {
                "timestamp": ts_value,
                "service": svc_value,
                "message": message[:500],
                "level": level,
            }
        )
        pattern_counter[message[:80]] += 1

    top_patterns = [p for p, _ in pattern_counter.most_common(5)]

    is_critical, crash_match_count, crash_excerpts = _detect_crash_signal(entries, service_filter)

    out: dict = {
        "entries": entries,
        "total_count": len(entries),
        "error_count": error_count,
        "top_patterns": top_patterns,
        "crash_match_count": crash_match_count,
    }
    if is_critical:
        out["critical_service"] = service_filter
        out["anomalous"] = True
        out["note"] = (
            f"CRITICAL: {service_filter} logs show {crash_match_count} "
            f"crash/fault pattern matches (OOMKilled, SIGSEGV, panic, "
            f"fatal, std::logic_error, exit 137/139, or similar). "
            f"This service is crashing or has crashed recently. "
            f"Sample: {crash_excerpts[0] if crash_excerpts else 'n/a'}"
        )
    return out


def _guess_log_level(message: str) -> str:
    """Best-effort extraction of log level from a message body."""
    upper = message.upper()
    for level in ("CRITICAL", "ERROR", "WARN", "WARNING", "INFO", "DEBUG"):
        if level in upper:
            return level if level != "WARNING" else "WARN"
    return "INFO"


# ── discover_causation offline helper ────────────────────────────────────────

# Metrics used for offline causal discovery. Excludes ``probe_up`` and
# ``probe_latency`` (not in RCAEval data) — the live tool drops those from
# ``_CAUSAL_METRICS`` for the same reason on non-instrumented systems.
_OFFLINE_CAUSAL_METRICS = (
    "cpu_usage",
    "memory_usage",
    "network_rx_bytes_rate",
    "network_tx_bytes_rate",
)


def discover_causation_from_df(
    services: list[str],
    time_range_minutes: int = 30,
    start_time: str | None = None,
    critical_services: list[str] | None = None,
    preloaded_metrics: dict[str, pd.DataFrame] | None = None,
) -> dict:
    """Offline analogue of :func:`src.agent.tools.discover_causation.discover_causation`.

    Builds the ``columns`` dict from preloaded DataFrames instead of
    Prometheus, then delegates to the shared
    :func:`src.agent.tools.discover_causation._run_pc_from_columns`
    helper. Returns the same dict shape as the live tool.

    Restricted to 4 canonical metrics (``cpu_usage``, ``memory_usage``,
    ``network_rx_bytes_rate``, ``network_tx_bytes_rate``) because
    ``probe_up`` / ``probe_latency`` don't exist in RCAEval data.
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

    if len(services) > 5:
        logger.warning(
            "Capping causal discovery to 5 services (received %d).",
            len(services),
        )
        services = services[:5]

    if preloaded_metrics is None:
        return {
            "causal_edges": [],
            "root_cause": "inconclusive",
            "root_cause_confidence": 0.0,
            "counterfactual": "Offline mode called with no preloaded metrics.",
            "graph_ascii": "",
        }

    pc_services = [s for s in services if s not in critical_set]
    columns: dict[str, list[float]] = {}
    for service in pc_services:
        if service not in preloaded_metrics:
            continue
        svc_df = preloaded_metrics[service]
        for metric in _OFFLINE_CAUSAL_METRICS:
            series = canonical_metric_series(svc_df, metric)
            if series.empty:
                continue
            all_values = [float(v) for v in series.tolist()]
            all_timestamps = _timestamp_series(svc_df, len(all_values))
            values, _ts = _slice_window(all_values, all_timestamps, time_range_minutes, start_time)
            if values:
                # Match the live tool's column-key naming ("{service}_cpu"
                # etc.) but strip the ``_usage`` / ``_rate`` / ``_bytes``
                # suffixes to keep lag expansion compact. Live tool uses
                # short keys ``cpu``, ``memory``, ``net_rx``, ``net_tx`` —
                # we align to that for parity of downstream logic.
                short_key = {
                    "cpu_usage": "cpu",
                    "memory_usage": "memory",
                    "network_rx_bytes_rate": "net_rx",
                    "network_tx_bytes_rate": "net_tx",
                }[metric]
                columns[f"{service}_{short_key}"] = values

    return _run_pc_from_columns(
        columns=columns,
        services=services,
        pc_services=pc_services,
        critical_set=critical_set,
    )


__all__ = [
    "canonical_metric_series",
    "query_preloaded_metrics",
    "search_preloaded_logs",
    "discover_causation_from_df",
]


# Keep ``Any`` re-export for users importing type helpers from this module.
_ = Any
