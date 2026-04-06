"""Shared test fixtures for OpsAgent test suite."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
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


# ── HDFS synthetic data ────────────────────────────────────────────────


@pytest.fixture()
def hdfs_data_dir(tmp_path: Path) -> Path:
    """Synthetic HDFS data directory with small HDFS.log + anomaly_label.csv.

    Creates 4 blocks:
      - blk_1000, blk_2000, blk_3000: Normal (multiple log lines each)
      - blk_9999: Anomalous (short block, will be left-padded)
    """
    # Synthetic HDFS.log (header + message format)
    # Helper to build HDFS-format log lines
    hdr = "081109 203615 148 INFO"

    def _recv(blk: str, ip: str) -> str:
        return (
            f"{hdr} dfs.DataNode$PacketResponder:"
            f" Receiving block {blk} src: /{ip}:50010"
            f" dest: /{ip}:50010"
        )

    def _done(blk: str, ip: str) -> str:
        return (
            f"{hdr} dfs.DataNode$PacketResponder: Received block {blk} of size 67108864 from /{ip}"
        )

    def _add(blk: str, ip: str) -> str:
        return (
            f"{hdr} dfs.FSNamesystem:"
            f" BLOCK* NameSystem.addStoredBlock:"
            f" blockMap updated: {ip}:50010"
            f" is added to {blk} size 67108864"
        )

    def _term(blk: str) -> str:
        return f"{hdr} dfs.DataNode$PacketResponder: PacketResponder 1 for block {blk} terminating"

    def _verify(blk: str) -> str:
        return f"{hdr} dfs.DataNode$PacketResponder: Verification succeeded for {blk}"

    log_lines = [
        _recv("blk_1000", "10.0.0.1"),
        _done("blk_1000", "10.0.0.1"),
        _add("blk_1000", "10.0.0.1"),
        _term("blk_1000"),
        _verify("blk_1000"),
        _recv("blk_2000", "10.0.0.2"),
        _done("blk_2000", "10.0.0.2"),
        _add("blk_2000", "10.0.0.2"),
        _term("blk_2000"),
        _recv("blk_3000", "10.0.0.3"),
        _done("blk_3000", "10.0.0.3"),
        _add("blk_3000", "10.0.0.3"),
        _term("blk_3000"),
        _verify("blk_3000"),
        _recv("blk_9999", "10.0.0.9"),
        _done("blk_9999", "10.0.0.9"),
    ]
    (tmp_path / "HDFS.log").write_text("\n".join(log_lines) + "\n")

    # Anomaly labels: blk_9999 is anomalous, others are normal
    label_lines = [
        "BlockId,Label",
        "blk_1000,Normal",
        "blk_2000,Normal",
        "blk_3000,Normal",
        "blk_9999,Anomaly",
    ]
    (tmp_path / "anomaly_label.csv").write_text("\n".join(label_lines) + "\n")

    return tmp_path


# ── Agent fixtures ────────────────────────────────────────────────────────


@pytest.fixture()
def sample_alert() -> dict[str, Any]:
    """Alert dict matching AnomalyDetector output format."""
    return {
        "title": "LSTM-AE Anomaly Detected",
        "severity": "high",
        "timestamp": datetime.now(UTC).isoformat(),
        "affected_services": ["cartservice", "checkoutservice"],
        "anomaly_score": 0.45,
        "threshold": 0.253,
    }


@pytest.fixture()
def sample_prometheus_range_response_factory():
    """Factory for Prometheus range query API responses."""

    def _make(
        values: list[tuple[float, str]] | None = None,
        metric_labels: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        if values is None:
            values = [(1704067200 + i * 15, str(0.05 + i * 0.01)) for i in range(10)]
        if metric_labels is None:
            metric_labels = {"service": "frontend"}
        return {
            "status": "success",
            "data": {
                "resultType": "matrix",
                "result": [{"metric": metric_labels, "values": values}],
            },
        }

    return _make


@pytest.fixture()
def sample_loki_response_factory():
    """Factory for Loki query_range API responses."""

    def _make(
        entries: list[tuple[str, str]] | None = None,
        stream_labels: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        if entries is None:
            entries = [
                ("1704067200000000000", "INFO: Request processed successfully"),
                ("1704067201000000000", "ERROR: Connection refused to redis:6379"),
                ("1704067202000000000", "WARN: Retry attempt 3 for upstream"),
            ]
        if stream_labels is None:
            stream_labels = {"service": "cartservice", "job": "docker"}
        return {
            "status": "success",
            "data": {
                "resultType": "streams",
                "result": [{"stream": stream_labels, "values": entries}],
            },
        }

    return _make


@pytest.fixture()
def sample_agent_config() -> dict[str, Any]:
    """Agent config dict matching configs/agent_config.yaml structure."""
    return {
        "agent": {
            "llm": {
                "model": "gemini-2.5-flash-lite",
                "temperature": 0.1,
                "max_output_tokens": 4096,
            },
            "investigation": {
                "max_tool_calls": 10,
                "confidence_threshold": 0.7,
                "timeout_seconds": 300,
            },
            "tools": {
                "query_metrics": {
                    "prometheus_url": "http://localhost:9090",
                    "default_time_range_minutes": 30,
                },
                "search_logs": {
                    "loki_url": "http://localhost:3100",
                    "default_limit": 100,
                },
                "search_runbooks": {
                    "chroma_persist_dir": "data/chromadb/",
                    "collection_name": "runbooks",
                    "top_k": 3,
                },
                "discover_causation": {
                    "alpha": 0.05,
                    "lags": [1, 2, 5],
                },
            },
        }
    }


@pytest.fixture()
def sample_causal_metrics_df() -> pd.DataFrame:
    """Synthetic multi-service metric DataFrame for causal discovery tests.

    Creates A → B → C causal chain with noise.
    """
    rng = np.random.default_rng(42)
    n = 100
    a = rng.normal(0, 1, n)
    b = 0.8 * a + rng.normal(0, 0.3, n)
    c = 0.6 * b + rng.normal(0, 0.3, n)
    return pd.DataFrame(
        {
            "service_a_cpu": a,
            "service_b_cpu": b,
            "service_c_cpu": c,
        }
    )
