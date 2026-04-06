"""Prometheus metric query tool for the OpsAgent investigation agent.

Wraps the MetricsCollector to fetch time-series metric data for a
specific service and compute summary statistics for the LLM.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import numpy as np
from langchain_core.tools import tool

from src.data_collection.metrics_collector import MetricsCollector

logger = logging.getLogger(__name__)

# Maps logical metric names to PromQL templates.
# {service} is replaced with the actual service name at query time.
# These match the Docker Stats Exporter metrics scraped by Prometheus.
METRIC_PROMQL: dict[str, str] = {
    "cpu_usage": 'rate(container_cpu_usage_seconds_total{service="{service}"}[1m])',
    "memory_usage": 'container_memory_working_set_bytes{service="{service}"}',
    "network_rx_bytes_rate": (
        'rate(container_network_receive_bytes_total{service="{service}"}[1m])'
    ),
    "network_tx_bytes_rate": (
        'rate(container_network_transmit_bytes_total{service="{service}"}[1m])'
    ),
    "network_rx_errors_rate": (
        'rate(container_network_receive_errors_total{service="{service}"}[1m])'
    ),
    "network_tx_errors_rate": (
        'rate(container_network_transmit_errors_total{service="{service}"}[1m])'
    ),
}


@tool
def query_metrics(
    service_name: str,
    metric_name: str,
    time_range_minutes: int = 30,
) -> dict:
    """Query Prometheus for a specific service metric over a time window.

    Use this tool to retrieve time-series metric data for a service you
    suspect is involved in the incident.  Call once per (service, metric)
    pair.  Useful for confirming hypotheses: if a service shows elevated
    cpu_usage or memory before downstream services degrade, that supports
    it being the root cause.

    Args:
        service_name: Canonical service name (e.g., "cartservice",
            "frontend", "checkoutservice").  Must match OTel Demo service
            names exactly.
        metric_name: One of: cpu_usage, memory_usage,
            network_rx_bytes_rate, network_tx_bytes_rate,
            network_rx_errors_rate, network_tx_errors_rate.
        time_range_minutes: How far back from now to query.  Default 30.

    Returns:
        dict with keys: timestamps, values, stats, anomalous.
    """
    if metric_name not in METRIC_PROMQL:
        return {
            "error": f"Unknown metric '{metric_name}'. "
            f"Available: {', '.join(sorted(METRIC_PROMQL))}",
            "timestamps": [],
            "values": [],
            "stats": {},
            "anomalous": False,
        }

    try:
        collector = MetricsCollector()
        end = datetime.now(UTC)
        start = end - timedelta(minutes=time_range_minutes)
        promql = METRIC_PROMQL[metric_name].replace("{service}", service_name)
        raw = collector.range_query(promql, start, end, step="15s")

        # Extract timestamps and values from Prometheus response
        timestamps: list[str] = []
        values: list[float] = []
        for series in raw:
            for ts, val in series.get("values", []):
                timestamps.append(datetime.fromtimestamp(float(ts), tz=UTC).isoformat())
                values.append(float(val))

        if not values:
            return {
                "timestamps": [],
                "values": [],
                "stats": {},
                "anomalous": False,
                "note": f"No data for {service_name}/{metric_name}",
            }

        arr = np.array(values)
        mean = float(np.mean(arr))
        std = float(np.std(arr))
        current = values[-1]
        anomalous = bool(abs(current - mean) > 2 * std) if std > 0 else False

        return {
            "timestamps": timestamps,
            "values": values,
            "stats": {
                "min": float(np.min(arr)),
                "max": float(np.max(arr)),
                "mean": mean,
                "std": std,
                "current": current,
            },
            "anomalous": anomalous,
        }
    except Exception:
        logger.exception("query_metrics failed for %s/%s", service_name, metric_name)
        return {
            "error": f"Failed to query metrics for {service_name}/{metric_name}",
            "timestamps": [],
            "values": [],
            "stats": {},
            "anomalous": False,
        }
