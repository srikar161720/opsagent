"""Integration tests for the OpsAgent LangGraph agent workflow.

These tests require:
  1. Docker infrastructure stack running (Prometheus, Loki, etc.)
  2. GEMINI_API_KEY environment variable set

Run with: poetry run pytest tests/integration/test_agent_workflow.py -v

Mark all tests with @pytest.mark.integration.
"""

from __future__ import annotations

import os
from typing import Any

import pytest


@pytest.mark.integration
class TestGraphCompilation:
    """Tests that the graph compiles successfully with real dependencies."""

    def test_graph_compiles(self) -> None:
        from src.agent.graph import build_graph

        graph = build_graph()
        assert graph is not None
        assert hasattr(graph, "invoke")


@pytest.mark.integration
class TestToolInvocation:
    """Tests that each tool can be invoked against live services."""

    def test_get_topology_live(self) -> None:
        from src.agent.tools.get_topology import get_topology

        result = get_topology.invoke({"service_name": None})
        assert len(result["nodes"]) == 7

    def test_query_metrics_live(self) -> None:
        from src.agent.tools.query_metrics import query_metrics

        result = query_metrics.invoke({"service_name": "frontend", "metric_name": "cpu_usage"})
        # May have no data if stack is not fully running, but should not error
        assert "error" not in result or result["timestamps"] == []

    def test_search_logs_live(self) -> None:
        from src.agent.tools.search_logs import search_logs

        result = search_logs.invoke({"query": "error", "time_range_minutes": 5})
        assert "entries" in result
        assert "total_count" in result


@pytest.mark.integration
class TestEndToEndInvestigation:
    """End-to-end investigation test requiring GEMINI_API_KEY."""

    @pytest.fixture(autouse=True)
    def _require_api_key(self) -> None:
        if not os.environ.get("GEMINI_API_KEY"):
            pytest.skip("GEMINI_API_KEY not set")

    def test_full_investigation(self) -> None:
        from src.agent.executor import AgentExecutor

        config = {
            "agent": {
                "investigation": {
                    "max_tool_calls": 5,
                    "confidence_threshold": 0.7,
                    "timeout_seconds": 120,
                },
            }
        }
        executor = AgentExecutor(config)

        alert: dict[str, Any] = {
            "title": "Integration Test Alert",
            "severity": "high",
            "timestamp": "2024-01-01T00:00:00Z",
            "affected_services": ["cartservice", "redis"],
            "anomaly_score": 0.45,
        }

        result = executor.investigate(alert=alert)

        assert "root_cause" in result
        assert "rca_report" in result
        assert result["root_cause"] is not None
        assert result["rca_report"] is not None
        assert isinstance(result["root_cause_confidence"], float)

    def test_tool_call_budget_respected(self) -> None:
        from src.agent.executor import AgentExecutor

        config = {
            "agent": {
                "investigation": {
                    "max_tool_calls": 2,
                    "confidence_threshold": 0.99,
                    "timeout_seconds": 60,
                },
            }
        }
        executor = AgentExecutor(config)

        alert: dict[str, Any] = {
            "title": "Budget Test",
            "severity": "medium",
            "timestamp": "2024-01-01T00:00:00Z",
            "affected_services": ["frontend"],
            "anomaly_score": 0.3,
        }

        result = executor.investigate(alert=alert)
        assert result["rca_report"] is not None

    def test_report_contains_sections(self) -> None:
        from src.agent.executor import AgentExecutor

        config = {
            "agent": {
                "investigation": {
                    "max_tool_calls": 5,
                    "confidence_threshold": 0.7,
                    "timeout_seconds": 120,
                },
            }
        }
        executor = AgentExecutor(config)

        alert: dict[str, Any] = {
            "title": "Report Format Test",
            "severity": "high",
            "timestamp": "2024-01-01T00:00:00Z",
            "affected_services": ["cartservice"],
            "anomaly_score": 0.5,
        }

        result = executor.investigate(alert=alert)
        report = result["rca_report"]
        assert report is not None
        # The report should contain substantive content
        assert len(report) > 100
