"""Unit tests for the AgentExecutor class.

LLM and graph invocations are mocked to avoid external dependencies.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import yaml


class TestAgentExecutorInit:
    """Tests for AgentExecutor construction."""

    def test_from_config_loads_yaml(self, tmp_path: Path, sample_agent_config: dict) -> None:
        from src.agent.executor import AgentExecutor

        config_file = tmp_path / "agent_config.yaml"
        config_file.write_text(yaml.dump(sample_agent_config))

        with patch("src.agent.executor.build_graph") as mock_build:
            mock_build.return_value = MagicMock()
            executor = AgentExecutor.from_config(str(config_file))

        assert executor.config["agent"]["investigation"]["max_tool_calls"] == 10

    def test_init_stores_config(self, sample_agent_config: dict) -> None:
        from src.agent.executor import AgentExecutor

        with patch("src.agent.executor.build_graph") as mock_build:
            mock_build.return_value = MagicMock()
            executor = AgentExecutor(sample_agent_config)

        assert executor.config == sample_agent_config
        assert executor.graph is not None


class TestAgentExecutorInvestigate:
    """Tests for the investigate() method."""

    def _make_executor(self, config: dict) -> Any:
        from src.agent.executor import AgentExecutor

        with patch("src.agent.executor.build_graph") as mock_build:
            mock_graph = MagicMock()
            mock_graph.invoke.return_value = {
                "root_cause": "cartservice",
                "root_cause_confidence": 0.85,
                "hypotheses": [
                    {"service": "cartservice", "confidence": 0.85},
                    {"service": "redis", "confidence": 0.6},
                    {"service": "frontend", "confidence": 0.3},
                    {"service": "checkoutservice", "confidence": 0.2},
                ],
                "rca_report": "Test RCA report content",
                "recommended_actions": ["Restart cartservice"],
            }
            mock_build.return_value = mock_graph
            return AgentExecutor(config)

    def test_investigate_returns_expected_keys(
        self, sample_agent_config: dict, sample_alert: dict
    ) -> None:
        executor = self._make_executor(sample_agent_config)
        result = executor.investigate(alert=sample_alert)

        expected_keys = {
            "root_cause",
            "root_cause_confidence",
            "top_3_predictions",
            "confidence",
            "rca_report",
            "recommended_actions",
        }
        assert set(result.keys()) == expected_keys

    def test_investigate_returns_root_cause(
        self, sample_agent_config: dict, sample_alert: dict
    ) -> None:
        executor = self._make_executor(sample_agent_config)
        result = executor.investigate(alert=sample_alert)
        assert result["root_cause"] == "cartservice"
        assert result["root_cause_confidence"] == 0.85

    def test_investigate_offline_mode(self, sample_agent_config: dict) -> None:
        executor = self._make_executor(sample_agent_config)
        alert = {"title": "eval", "severity": "evaluation"}
        metrics = {"cartservice": {"cpu": [0.5]}, "redis": {"cpu": [0.3]}}

        result = executor.investigate(
            alert=alert,
            metrics=metrics,
            anomaly_timestamp="2024-01-01T00:00:00Z",
        )
        assert result["root_cause"] is not None

    def test_investigate_graph_failure(self, sample_agent_config: dict, sample_alert: dict) -> None:
        from src.agent.executor import AgentExecutor

        with patch("src.agent.executor.build_graph") as mock_build:
            mock_graph = MagicMock()
            mock_graph.invoke.side_effect = RuntimeError("Graph failed")
            mock_build.return_value = mock_graph
            executor = AgentExecutor(sample_agent_config)

        result = executor.investigate(alert=sample_alert)
        assert result["root_cause"] == "unknown"
        assert "failed" in result["rca_report"].lower()


class TestFormatAlert:
    """Tests for the _format_alert helper."""

    def test_format_alert_includes_services(self, sample_agent_config: dict) -> None:
        from src.agent.executor import AgentExecutor

        with patch("src.agent.executor.build_graph"):
            executor = AgentExecutor(sample_agent_config)

        msg = executor._format_alert(
            {"anomaly_score": 0.5},
            ["cartservice", "redis"],
            "2024-01-01T00:00:00Z",
        )
        assert "cartservice" in msg
        assert "redis" in msg
        assert "INCIDENT ALERT" in msg

    def test_format_alert_empty_services(self, sample_agent_config: dict) -> None:
        from src.agent.executor import AgentExecutor

        with patch("src.agent.executor.build_graph"):
            executor = AgentExecutor(sample_agent_config)

        msg = executor._format_alert({}, [], None)
        assert "unknown" in msg


class TestExtractTop3:
    """Tests for the _extract_top3 helper."""

    def test_extract_top3_sorted(self, sample_agent_config: dict) -> None:
        from src.agent.executor import AgentExecutor

        with patch("src.agent.executor.build_graph"):
            executor = AgentExecutor(sample_agent_config)

        state = {
            "hypotheses": [
                {"service": "a", "confidence": 0.3},
                {"service": "b", "confidence": 0.9},
                {"service": "c", "confidence": 0.6},
                {"service": "d", "confidence": 0.1},
            ]
        }
        top3 = executor._extract_top3(state)
        assert top3 == ["b", "c", "a"]

    def test_extract_top3_fewer_than_3(self, sample_agent_config: dict) -> None:
        from src.agent.executor import AgentExecutor

        with patch("src.agent.executor.build_graph"):
            executor = AgentExecutor(sample_agent_config)

        state = {"hypotheses": [{"service": "only_one", "confidence": 0.8}]}
        top3 = executor._extract_top3(state)
        assert top3 == ["only_one"]

    def test_extract_top3_empty_hypotheses_falls_back_to_root_cause(
        self, sample_agent_config: dict
    ) -> None:
        from src.agent.executor import AgentExecutor

        with patch("src.agent.executor.build_graph"):
            executor = AgentExecutor(sample_agent_config)

        state = {"hypotheses": [], "root_cause": "cartservice"}
        top3 = executor._extract_top3(state)
        assert "cartservice" in top3

    def test_extract_top3_empty_no_root_cause(self, sample_agent_config: dict) -> None:
        from src.agent.executor import AgentExecutor

        with patch("src.agent.executor.build_graph"):
            executor = AgentExecutor(sample_agent_config)

        state = {"hypotheses": [], "root_cause": "unknown"}
        top3 = executor._extract_top3(state)
        assert top3 == []

    def test_extract_top3_pads_from_causal_edges(self, sample_agent_config: dict) -> None:
        from src.agent.executor import AgentExecutor

        with patch("src.agent.executor.build_graph"):
            executor = AgentExecutor(sample_agent_config)

        state = {
            "hypotheses": [],
            "root_cause": "cartservice",
            "causal_graph": {
                "causal_edges": [
                    {"source": "redis_cpu", "target": "cartservice_memory"},
                    {"source": "frontend_cpu", "target": "redis_net_rx"},
                ],
            },
        }
        top3 = executor._extract_top3(state)
        assert top3[0] == "cartservice"
        assert len(top3) == 3  # padded from causal edges
