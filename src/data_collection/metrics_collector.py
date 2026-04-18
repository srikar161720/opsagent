"""Prometheus HTTP API client for metric collection.

Provides instant and range queries against a Prometheus server,
used by both the real-time Fast Loop and offline evaluation.
"""

from __future__ import annotations

import logging
import math
import re
from datetime import datetime
from typing import Any

import requests

logger = logging.getLogger(__name__)


def _parse_step_seconds(step: str) -> float:
    """Parse a Prometheus step string (e.g. "15s", "1m", "30") into seconds.

    Returns 15.0 if the string cannot be parsed — matches the widely-used
    default scrape interval.
    """
    match = re.match(r"^\s*(\d+(?:\.\d+)?)\s*([smhd])?\s*$", step)
    if not match:
        return 15.0
    value = float(match.group(1))
    unit = match.group(2) or "s"
    multipliers = {"s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}
    return value * multipliers[unit]


class MetricsCollector:
    """Query Prometheus via its HTTP API."""

    def __init__(self, base_url: str = "http://localhost:9090") -> None:
        self.base_url = base_url.rstrip("/")

    def instant_query(self, query: str) -> list[dict[str, Any]]:
        """Execute a PromQL instant query (``/api/v1/query``).

        Returns the ``result`` list from the Prometheus response, where each
        element is a dict with ``metric`` and ``value`` keys.
        """
        resp = requests.get(
            f"{self.base_url}/api/v1/query",
            params={"query": query},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "success":
            logger.error("Prometheus query failed: %s", data.get("error", "unknown"))
            return []
        result: list[dict[str, Any]] = data["data"]["result"]
        return result

    def range_query(
        self,
        query: str,
        start: datetime,
        end: datetime,
        step: str = "15s",
    ) -> list[dict[str, Any]]:
        """Execute a PromQL range query (``/api/v1/query_range``).

        Returns the ``result`` list, where each element contains ``metric``
        and ``values`` (list of ``[timestamp, value]`` pairs).
        """
        resp = requests.get(
            f"{self.base_url}/api/v1/query_range",
            params={
                "query": query,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "step": step,
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "success":
            logger.error("Prometheus range query failed: %s", data.get("error", "unknown"))
            return []
        result: list[dict[str, Any]] = data["data"]["result"]
        return result

    def get_service_metrics(
        self,
        service: str,
        metric_queries: dict[str, str],
        start: datetime | None = None,
        end: datetime | None = None,
        step: str = "15s",
    ) -> dict[str, list[float]]:
        """Convenience method: fetch multiple metrics for a single service.

        Args:
            service:        Service name (used for query interpolation).
            metric_queries: Mapping of metric_name → PromQL template.
                            The string ``{service}`` in each template is replaced
                            with the *service* argument.
            start:          Range query start (if None, uses instant query).
            end:            Range query end (if None, uses instant query).
            step:           Range query step interval.

        Returns:
            Dict mapping metric_name → list of float values. For range queries,
            a missing metric (no Prometheus series — e.g. ``error_rate`` when no
            errors occurred) is zero-filled to the expected number of steps so
            downstream consumers (causal discovery) don't bail on empty data.
            NaN values (e.g. ``histogram_quantile`` on empty buckets) are
            coerced to 0.0. For instant queries, a missing metric yields
            ``[0.0]``.
        """
        # Compute expected step count for zero-fill sizing on range queries.
        if start is not None and end is not None:
            step_seconds = _parse_step_seconds(step)
            window_seconds = (end - start).total_seconds()
            expected_steps = max(1, int(window_seconds / step_seconds))
        else:
            expected_steps = 1

        result: dict[str, list[float]] = {}
        for metric_name, query_template in metric_queries.items():
            query = query_template.replace("{service}", service)
            if start is not None and end is not None:
                raw = self.range_query(query, start, end, step)
                values: list[float] = []
                for series in raw:
                    for _, raw_val in series.get("values", []):
                        try:
                            v = float(raw_val)
                        except (TypeError, ValueError):
                            v = 0.0
                        values.append(0.0 if math.isnan(v) else v)
                if not values:
                    # Empty series → zero-fill so PC/discovery doesn't bail out.
                    values = [0.0] * expected_steps
            else:
                raw = self.instant_query(query)
                values = []
                for r in raw:
                    if "value" not in r:
                        continue
                    try:
                        v = float(r["value"][1])
                    except (TypeError, ValueError):
                        v = 0.0
                    values.append(0.0 if math.isnan(v) else v)
                if not values:
                    values = [0.0]
            result[metric_name] = values
        return result
