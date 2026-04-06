"""Loki log search tool for the OpsAgent investigation agent.

Queries the Loki HTTP API for log entries matching a text pattern,
optionally filtered by service name.
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import UTC, datetime, timedelta

import requests
from langchain_core.tools import tool

logger = logging.getLogger(__name__)

LOKI_URL = "http://localhost:3100"


@tool
def search_logs(
    query: str,
    service_filter: str | None = None,
    time_range_minutes: int = 30,
    limit: int = 100,
) -> dict:
    """Search Loki for log entries matching a query pattern.

    Use this tool to find error messages, stack traces, or warning
    patterns in logs that corroborate or refute your hypotheses.
    Especially useful for identifying connection timeouts, OOM errors,
    or repeated retry patterns.

    Args:
        query: Text to search for in log messages.  Supports simple
            string patterns (e.g., "connection refused", "timeout",
            "OOM", "error", "WARN").
        service_filter: Restrict search to a specific service name.
            If None, searches all services.
        time_range_minutes: How far back to search.  Default 30 minutes.
        limit: Maximum log entries to return.  Default 100.

    Returns:
        dict with keys: entries, total_count, error_count, top_patterns.
    """
    try:
        end = datetime.now(UTC)
        start = end - timedelta(minutes=time_range_minutes)

        # Build LogQL query
        if service_filter:
            logql = f'{{service="{service_filter}"}} |= "{query}"'
        else:
            logql = f'{{job="docker"}} |= "{query}"'

        resp = requests.get(
            f"{LOKI_URL}/loki/api/v1/query_range",
            params={
                "query": logql,
                "start": str(int(start.timestamp())),
                "end": str(int(end.timestamp())),
                "limit": str(limit),
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        entries: list[dict] = []
        pattern_counter: Counter[str] = Counter()
        error_count = 0

        for stream in data.get("data", {}).get("result", []):
            stream_labels = stream.get("stream", {})
            service = stream_labels.get("service", stream_labels.get("job", "unknown"))

            for ts_ns, message in stream.get("values", []):
                ts_sec = int(ts_ns) / 1e9
                iso_ts = datetime.fromtimestamp(ts_sec, tz=UTC).isoformat()

                level = _extract_log_level(message)
                if level in ("ERROR", "CRITICAL"):
                    error_count += 1

                entries.append(
                    {
                        "timestamp": iso_ts,
                        "service": service,
                        "message": message[:500],
                        "level": level,
                    }
                )

                # Track patterns (first 80 chars as a rough pattern)
                pattern_counter[message[:80]] += 1

        top_patterns = [p for p, _ in pattern_counter.most_common(5)]

        return {
            "entries": entries,
            "total_count": len(entries),
            "error_count": error_count,
            "top_patterns": top_patterns,
        }
    except Exception:
        logger.exception("search_logs failed for query=%s", query)
        return {
            "entries": [],
            "total_count": 0,
            "error_count": 0,
            "top_patterns": [],
            "error": f"Failed to search logs for '{query}'",
        }


def _extract_log_level(message: str) -> str:
    """Best-effort extraction of log level from a log message."""
    upper = message.upper()
    for level in ("CRITICAL", "ERROR", "WARN", "WARNING", "INFO", "DEBUG"):
        if level in upper:
            return level if level != "WARNING" else "WARN"
    return "INFO"
