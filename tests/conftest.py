"""Shared test fixtures for OpsAgent test suite."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from unittest.mock import MagicMock

import pytest

# ── Sample log lines ────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def sample_hdfs_log_lines() -> list[str]:
    """Representative HDFS log lines (header already stripped)."""
    return [
        "Receiving block blk_-1608999687919862906 src: /10.251.73.220:50010"
        " dest: /10.251.73.220:50010",
        "BLOCK* NameSystem.addStoredBlock: blockMap updated:"
        " 10.251.203.80:50010 is added to blk_-1608999687919862906 size 67108864",
        "PacketResponder 1 for block blk_-1608999687919862906 terminating",
        "Receiving block blk_38865049064139660 src: /10.251.107.227:50010"
        " dest: /10.251.107.227:50010",
        "BLOCK* NameSystem.allocateBlock: /user/root/rand/_temporary/"
        "attempt_200811092030_0001_r_000826_0/part-00826. blk_38865049064139660",
        "Received block blk_38865049064139660 of size 67108864 from /10.251.107.227",
        "Verification succeeded for blk_38865049064139660",
        "Deleting block blk_8229193803249955061 file"
        " /mnt/hadoop/mapred/system/job_200811092030_0001/job.jar",
    ]


@pytest.fixture(scope="session")
def sample_otel_log_lines() -> list[str]:
    """Representative OTel Demo log messages."""
    return [
        "Failed to connect to redis:6379 after 3 retries",
        "Successfully placed order for user abc123",
        "Payment processed for order 456 amount 29.99 USD",
        "Product catalog loaded with 15 items",
        "Cart service received addItem request for user xyz789",
        "Currency conversion: 100.00 USD to EUR",
        "Error: checkout failed for user def456 - insufficient funds",
        "Frontend received request GET /api/products",
    ]


# ── Sample window dicts ─────────────────────────────────────────────────


@pytest.fixture()
def sample_window_dict() -> dict[str, Any]:
    """A window dict matching WindowAggregator output format."""
    return {
        "window_start": datetime(2024, 1, 1, 12, 0, 0),
        "window_end": datetime(2024, 1, 1, 12, 1, 0),
        "logs": [
            {"timestamp": datetime(2024, 1, 1, 12, 0, 5), "template_id": 0, "service": "frontend"},
            {
                "timestamp": datetime(2024, 1, 1, 12, 0, 10),
                "template_id": 1,
                "service": "cartservice",
            },
            {"timestamp": datetime(2024, 1, 1, 12, 0, 15), "template_id": 0, "service": "frontend"},
            {"timestamp": datetime(2024, 1, 1, 12, 0, 30), "template_id": 2, "service": "redis"},
        ],
        "metrics": {
            "cpu_usage": [0.05, 0.07, 0.06, 0.08],
            "memory_usage": [200.0, 201.0, 202.0, 200.5],
        },
    }


@pytest.fixture()
def empty_window_dict() -> dict[str, Any]:
    """An empty window dict with no logs or metrics."""
    return {
        "window_start": datetime(2024, 1, 1, 12, 1, 0),
        "window_end": datetime(2024, 1, 1, 12, 2, 0),
        "logs": [],
        "metrics": {},
    }


# ── Mock factories ──────────────────────────────────────────────────────


@pytest.fixture()
def mock_kafka_message():
    """Factory for creating mock confluent_kafka Message objects."""

    def _make(
        value: bytes = b'{"service": "frontend", "message": "test"}',
        partition: int = 0,
        offset: int = 0,
        timestamp_ms: int = 1704067200000,
        error: Any = None,
    ) -> MagicMock:
        msg = MagicMock()
        msg.value.return_value = value
        msg.partition.return_value = partition
        msg.offset.return_value = offset
        msg.timestamp.return_value = (1, timestamp_ms)  # (TIMESTAMP_CREATE_TIME, ms)
        msg.error.return_value = error
        msg.topic.return_value = "opsagent-logs"
        return msg

    return _make


@pytest.fixture()
def mock_prometheus_response():
    """Factory for creating mock Prometheus API JSON responses."""

    def _make(
        result: list[dict[str, Any]] | None = None,
        status: str = "success",
    ) -> dict[str, Any]:
        if result is None:
            result = [
                {
                    "metric": {"service": "frontend"},
                    "value": [1704067200, "0.05"],
                }
            ]
        return {"status": status, "data": {"resultType": "vector", "result": result}}

    return _make
