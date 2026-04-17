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
# Container-level metrics from Docker Stats Exporter (available for all services).
# Application-level metrics from OTel Collector spanmetrics (available for
# services that export traces: frontend, checkoutservice, productcatalogservice,
# paymentservice, loadgenerator).
METRIC_PROMQL: dict[str, str] = {
    # Container-level metrics (Docker Stats Exporter)
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
    # Application-level metrics (OTel Collector spanmetrics)
    "request_rate": ('sum(rate(span_calls_total{service_name="{service}"}[1m]))'),
    "error_rate": (
        'sum(rate(span_calls_total{service_name="{service}",status_code="STATUS_CODE_ERROR"}[1m]))'
    ),
    "latency_p99": (
        "histogram_quantile(0.99, "
        'sum(rate(span_duration_milliseconds_bucket{service_name="{service}"}[1m])) by (le))'
    ),
    # Service probe metrics (Service Probe Exporter — available for all services)
    "probe_up": 'service_probe_up{service="{service}"}',
    "probe_latency": 'service_probe_duration_seconds{service="{service}"}',
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
        metric_name: One of:
            Container metrics (all services): cpu_usage, memory_usage,
            network_rx_bytes_rate, network_tx_bytes_rate,
            network_rx_errors_rate, network_tx_errors_rate.
            Application metrics (frontend, checkoutservice,
            productcatalogservice, paymentservice): request_rate,
            error_rate, latency_p99.
            Probe metrics (all services): probe_up (1=reachable,
            0=down), probe_latency (TCP connect time in seconds).
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

        # Extract timestamps and values from Prometheus response.
        # Filter out NaN values — histogram_quantile returns NaN when no
        # matching buckets exist (e.g., latency_p99 for services that don't
        # export traces). NaN is not valid JSON and causes Gemini API 400.
        timestamps: list[str] = []
        values: list[float] = []
        for series in raw:
            for ts, val in series.get("values", []):
                v = float(val)
                if np.isnan(v):
                    continue
                timestamps.append(datetime.fromtimestamp(float(ts), tz=UTC).isoformat())
                values.append(v)

        # Non-container metrics: missing data does NOT indicate a crash.
        # - Application metrics (spanmetrics): only available for trace-exporting services
        # - Probe metrics: gauges from the probe exporter (if exporter is down, no data)
        # Missing data for these should return a neutral response, not CRITICAL.
        _app_metrics = {"request_rate", "error_rate", "latency_p99", "probe_up", "probe_latency"}

        if not values:
            if metric_name in _app_metrics:
                return {
                    "timestamps": [],
                    "values": [],
                    "stats": {},
                    "anomalous": False,
                    "note": (
                        f"No application metrics available for {service_name}/{metric_name}. "
                        f"This service may not export OpenTelemetry traces. "
                        f"Use container-level metrics (cpu_usage, memory_usage) instead."
                    ),
                }
            return {
                "timestamps": [],
                "values": [],
                "stats": {},
                "anomalous": True,
                "note": (
                    f"CRITICAL: No metric data returned for {service_name}/{metric_name}. "
                    f"This strongly indicates the service is DOWN, CRASHED, or UNREACHABLE. "
                    f"A healthy service always reports metrics. "
                    f"This service should be considered a top root cause candidate."
                ),
            }

        arr = np.array(values)
        mean = float(np.mean(arr))
        std = float(np.std(arr))
        current = values[-1]
        anomalous = bool(abs(current - mean) > 2 * std) if std > 0 else False

        # Probe-specific anomaly detection.
        # probe_up=0 is the strongest possible signal that a service is DOWN.
        # This check is separate from the general 2σ test (which doesn't fire
        # because 0.0 is within 2σ of a mixed 1/0 series) and from the
        # sparse/stale CRITICAL (which is skipped for probe metrics).
        if metric_name == "probe_up" and len(values) >= 4:
            recent_down = sum(1 for v in values[-4:] if v == 0.0)
            was_up = mean > 0.1  # service was previously reachable
            if recent_down >= 3 and was_up:
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
                    "anomalous": True,
                    "note": (
                        f"CRITICAL: {service_name} is DOWN — probe_up has been 0 "
                        f"for the last {recent_down} readings. The service is not "
                        f"responding to connection attempts. "
                        f"This service should be considered a top root cause candidate."
                    ),
                }

        # Probe latency spike detection.
        # A 10x+ increase in probe_latency indicates the service is alive but
        # severely degraded (e.g., CPU throttling or network latency injection).
        if (
            metric_name == "probe_latency"
            and len(values) >= 4
            and mean > 0.0001
            and current > 10 * mean
        ):
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
                "anomalous": True,
                "note": (
                    f"CRITICAL: {service_name} probe latency is severely elevated "
                    f"(current={current:.4f}s vs mean={mean:.4f}s, "
                    f"{current / mean:.0f}x increase). The service is responding "
                    f"very slowly. This service should be considered a top root "
                    f"cause candidate."
                ),
            }

        # Detect frozen/paused containers: when a container is paused via
        # `docker pause`, the Docker Stats Exporter still reports metrics but
        # CPU rate drops to exactly 0 while memory stays constant. Check if
        # the last ~4 values (60s) are all zero for rate-based metrics, which
        # indicates the process is frozen (not just idle — idle services still
        # show small CPU fluctuations).
        _rate_metrics = {
            "cpu_usage",
            "network_rx_bytes_rate",
            "network_tx_bytes_rate",
        }
        if metric_name in _rate_metrics and len(values) >= 8:
            recent = values[-8:]
            zero_count = sum(1 for v in recent if v == 0.0)
            mostly_zero = zero_count >= 5  # 5 of last 8 are zero
            had_activity = mean > 0.0001  # service was active before
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
                    },
                    "anomalous": True,
                    "frozen": True,
                    "note": (
                        f"CRITICAL: {service_name}/{metric_name} dropped to zero "
                        f"(last 4 readings are 0.0, but historical mean was {mean:.6f}). "
                        f"This indicates the service process is FROZEN or PAUSED. "
                        f"This service should be considered a top root cause candidate."
                    ),
                }

        # Detect crashed/down services via two signals:
        #
        # 1. Stale data: most recent data point is >90s old (service stopped
        #    reporting entirely).
        # 2. Missing data: significantly fewer data points than expected for the
        #    time range. With 15s scrape interval, a 10-min window should have
        #    ~40 points. If we get <70% of expected, data is dropping off
        #    (service went down partway through the window).
        last_ts = datetime.fromisoformat(timestamps[-1])
        now = datetime.now(UTC)
        staleness_seconds = (now - last_ts).total_seconds()
        stale = staleness_seconds > 90

        expected_points = (time_range_minutes * 60) / 15  # 15s scrape interval
        data_coverage = len(values) / expected_points if expected_points > 0 else 1.0
        # 70% threshold: with 120s pre-investigation wait and rate()[1m] lookback,
        # a crashed service's data expires by ~75s post-crash. At 120s, coverage
        # drops to ~65% — well below 70%. Healthy services stay at 95-102%.
        # Previously used 90%, but that triggered false CRITICAL on services
        # recovering from a previous test's fault during cooldown periods.
        sparse = data_coverage < 0.70

        # Only apply stale/sparse CRITICAL detection to container-level metrics.
        # Application metrics (spanmetrics) have irregular data rates based on
        # traffic volume, not a fixed 15s scrape interval, so the coverage
        # calculation would produce false CRITICAL signals.
        if metric_name not in _app_metrics and (stale or sparse):
            reason_parts = []
            if stale:
                reason_parts.append(f"last data point is {staleness_seconds:.0f}s old")
            if sparse:
                reason_parts.append(
                    f"only {len(values)}/{int(expected_points)} expected data points "
                    f"({data_coverage:.0%} coverage)"
                )
            reason = "; ".join(reason_parts)

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
                "anomalous": True,
                "stale": stale,
                "sparse": sparse,
                "data_coverage": data_coverage,
                "staleness_seconds": staleness_seconds,
                "note": (
                    f"CRITICAL: Metrics for {service_name}/{metric_name} show "
                    f"signs the service is DOWN or DEGRADED ({reason}). "
                    f"A healthy service reports metrics every 15s with full coverage. "
                    f"This service should be considered a top root cause candidate."
                ),
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
