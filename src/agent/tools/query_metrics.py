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
    start_time: str | None = None,
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
        start_time: Optional ISO-8601 timestamp. When set, the query window
            is ``[start_time, start_time + time_range_minutes]`` instead of
            the default ``[now - time_range_minutes, now]``. Used by the
            evaluation harness to pin a per-test window and avoid cross-
            test metric pollution.

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
        now = datetime.now(UTC)
        if start_time:
            anchor = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
            if anchor.tzinfo is None:
                anchor = anchor.replace(tzinfo=UTC)
            # Extend the pinned window 60s BEFORE the anchor so baseline_mean
            # (first 60% of the window) reflects pre-fault normalcy, not the
            # fault itself. Without this, a service with an immediately-spiked
            # metric has baseline_mean ≈ spike_mean, killing the 10x trigger.
            start = anchor - timedelta(seconds=60)
            # End is anchor + time_range, clamped to now so we don't query
            # the future. Sparse detection uses the actual window length.
            theoretical_end = anchor + timedelta(minutes=time_range_minutes)
            end = min(theoretical_end, now)
        else:
            end = now
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

        # Application-level metrics (spanmetrics from OTel Collector) are only
        # available for trace-exporting services. Missing data for these is
        # NOT a crash signal — it means the service doesn't export traces.
        _app_metrics = {"request_rate", "error_rate", "latency_p99"}

        # Probe metrics (probe_up, probe_latency) are produced by the Service
        # Probe Exporter. If the exporter itself is down, we get no data for
        # ANY service's probe metric — that's infrastructure, not a fault.
        # The empty-response path returns a neutral note in that case.
        _probe_metrics = {"probe_up", "probe_latency"}

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
            if metric_name in _probe_metrics:
                return {
                    "timestamps": [],
                    "values": [],
                    "stats": {},
                    "anomalous": False,
                    "note": (
                        f"probe exporter unavailable — no {metric_name} data returned. "
                        f"The Service Probe Exporter may be down. "
                        f"Fall back to container-level metrics (cpu_usage, memory_usage) "
                        f"to assess {service_name}."
                    ),
                }
            # Empty container-level metric. We can't distinguish "service
            # never existed in this window" from "service crashed and stopped
            # reporting" without a baseline reference. The sparse/stale path
            # later in this function catches crashes that happen AFTER some
            # data was recorded. Return a neutral note here — a genuinely
            # dead service will also lack probe_up data (CRITICAL there)
            # and the LLM can correlate.
            return {
                "timestamps": [],
                "values": [],
                "stats": {},
                "anomalous": False,
                "note": (
                    f"No {metric_name} data returned for {service_name} in this window. "
                    f"Cannot distinguish a never-reporting service from a recently-crashed "
                    f"one without baseline data. Check probe_up for this service — "
                    f"if probe_up also has no data or is 0, the service is likely DOWN."
                ),
            }

        arr = np.array(values)
        mean = float(np.mean(arr))
        std = float(np.std(arr))
        current = values[-1]
        anomalous = bool(abs(current - mean) > 2 * std) if std > 0 else False

        # Baseline calibration. When start_time is pinned, we know the exact
        # fault anchor — so we use ALL points BEFORE the anchor as baseline
        # (maximizes signal-to-noise). Otherwise we fall back to the heuristic
        # first-60%-of-window.
        if start_time:
            # The anchor is 60s after ``start`` (we extended the window 60s
            # backward earlier). Count how many timestamps fall before it.
            anchor_ts = start + timedelta(seconds=60)
            baseline_end = sum(
                1 for ts in timestamps if datetime.fromisoformat(ts) < anchor_ts
            )
        else:
            baseline_end = int(len(values) * 0.6)

        baseline_mean = float(np.mean(arr[:baseline_end])) if baseline_end >= 2 else mean

        # Probe-specific anomaly detection.
        # probe_up=0 is the strongest possible signal that a service is DOWN.
        # Two triggers — either fires CRITICAL, but BOTH require a strong
        # baseline to avoid false-positive on services that are always flaky
        # (e.g. v1.10.0 currencyservice crash-loops in baseline with
        # probe_up mean ~0.2 — not a fault, just baseline noise).
        #   (a) 3+ of the last 4 readings are 0 AND baseline_mean >= 0.7
        #       (captures a service that WAS healthy, now degraded)
        #   (b) baseline_mean >= 0.9 AND the last 2 readings are both 0
        #       (captures a FRESH drop: service was healthy, just crashed)
        if metric_name == "probe_up" and len(values) >= 4:
            recent_down = sum(1 for v in values[-4:] if v == 0.0)
            was_healthy = baseline_mean >= 0.7
            fresh_drop = (
                baseline_mean >= 0.9
                and values[-1] == 0.0
                and values[-2] == 0.0
            )
            if (recent_down >= 3 and was_healthy) or fresh_drop:
                if fresh_drop and recent_down < 3:
                    reason = (
                        f"just dropped from baseline mean={baseline_mean:.2f} "
                        f"to 0.0 (last 2 readings are 0)"
                    )
                else:
                    reason = (
                        f"probe_up has been 0 for the last {recent_down} "
                        f"readings; baseline mean was {baseline_mean:.2f}"
                    )
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
                    "note": (
                        f"CRITICAL: {service_name} is DOWN — {reason}. "
                        f"The service is not responding to connection attempts. "
                        f"This service should be considered a top root cause candidate."
                    ),
                }

        # Probe latency spike detection.
        # A 10x+ increase vs the pre-anomaly baseline indicates the service is
        # alive but severely degraded (e.g., CPU throttling or network latency
        # injection). Comparing against baseline_mean (first 60% of window)
        # instead of full-window mean prevents the anomaly from diluting its
        # own detection threshold once the fault persists.
        if (
            metric_name == "probe_latency"
            and len(values) >= 4
            and baseline_mean > 0.0001
            and current > 10 * baseline_mean
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
                    "baseline_mean": baseline_mean,
                },
                "anomalous": True,
                "note": (
                    f"CRITICAL: {service_name} probe latency is severely elevated "
                    f"(current={current:.4f}s vs baseline_mean={baseline_mean:.4f}s, "
                    f"{current / baseline_mean:.0f}x increase). The service is responding "
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
        # 1. Stale data: most recent data point is >90s before the window
        #    end (service stopped reporting entirely). When ``start_time`` is
        #    pinned, the reference point is the end of the pinned window, not
        #    ``now`` — otherwise a 3-hour-old pinned window would mark every
        #    healthy service as stale.
        # 2. Missing data: significantly fewer data points than expected for the
        #    time range. With 15s scrape interval, a 10-min window should have
        #    ~40 points. If we get <70% of expected, data is dropping off
        #    (service went down partway through the window).
        last_ts = datetime.fromisoformat(timestamps[-1])
        reference_end = end  # end of the queried window (pinned or "now")
        staleness_seconds = (reference_end - last_ts).total_seconds()
        stale = staleness_seconds > 90

        # Use the ACTUAL queried window (end - start) to compute expected
        # points, not the requested time_range_minutes. This matters when
        # start_time pins the window into the future and end is clamped to
        # now — expected_points must reflect the elapsed duration, not the
        # unreached theoretical window.
        actual_window_seconds = (end - start).total_seconds()
        expected_points = max(1.0, actual_window_seconds / 15)  # 15s scrape interval
        data_coverage = len(values) / expected_points if expected_points > 0 else 1.0
        # 70% threshold: with 120s pre-investigation wait and rate()[1m] lookback,
        # a crashed service's data expires by ~75s post-crash. At 120s, coverage
        # drops to ~65% — well below 70%. Healthy services stay at 95-102%.
        # Previously used 90%, but that triggered false CRITICAL on services
        # recovering from a previous test's fault during cooldown periods.
        sparse = data_coverage < 0.70

        # Apply stale/sparse CRITICAL detection to container-level AND probe
        # metrics (both scraped at 15s intervals). Spanmetrics are excluded
        # because their data rate depends on traffic volume, not scrape
        # interval, so the coverage calculation would produce false CRITICAL.
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
