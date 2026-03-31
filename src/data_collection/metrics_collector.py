"""Prometheus HTTP API client for metric collection.

Provides instant and range queries against a Prometheus server,
used by both the real-time Fast Loop and offline evaluation.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import requests

logger = logging.getLogger(__name__)


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
            Dict mapping metric_name → list of float values.
        """
        result: dict[str, list[float]] = {}
        for metric_name, query_template in metric_queries.items():
            query = query_template.replace("{service}", service)
            if start is not None and end is not None:
                raw = self.range_query(query, start, end, step)
                values: list[float] = []
                for series in raw:
                    values.extend(float(v) for _, v in series.get("values", []))
            else:
                raw = self.instant_query(query)
                values = [float(r["value"][1]) for r in raw if "value" in r]
            result[metric_name] = values
        return result
