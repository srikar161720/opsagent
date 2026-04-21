"""Unit tests for the AgentState TypedDict definition."""

from __future__ import annotations

from typing import get_type_hints


class TestAgentState:
    """Tests for AgentState TypedDict structure."""

    def test_state_is_typed_dict(self) -> None:
        from src.agent.state import AgentState

        # TypedDict subclasses dict
        assert issubclass(AgentState, dict)

    def test_state_has_all_expected_fields(self) -> None:
        from src.agent.state import AgentState

        annotations = AgentState.__annotations__
        expected_fields = {
            "alert",
            "anomaly_window",
            "affected_services",
            "start_time",
            # Offline-mode fields threaded in by AgentExecutor.investigate
            # when metrics=/logs= kwargs are provided. Default None for
            # live-mode invocations.
            "preloaded_metrics",
            "preloaded_logs",
            "messages",
            "hypotheses",
            "evidence",
            "tool_calls_remaining",
            "causal_graph",
            "root_cause",
            "root_cause_confidence",
            "rca_report",
            "recommended_actions",
            "relevant_runbooks",
        }
        assert set(annotations.keys()) == expected_fields

    def test_messages_field_is_annotated(self) -> None:
        from src.agent.state import AgentState

        hints = get_type_hints(AgentState, include_extras=True)
        msg_hint = hints["messages"]
        # Annotated types have __metadata__
        assert hasattr(msg_hint, "__metadata__")

    def test_state_can_be_constructed(self) -> None:
        from src.agent.state import AgentState

        state: AgentState = {
            "alert": {"title": "test"},
            "anomaly_window": ("2024-01-01", "2024-01-01"),
            "affected_services": ["svc_a"],
            "start_time": None,
            "preloaded_metrics": None,
            "preloaded_logs": None,
            "messages": [],
            "hypotheses": [],
            "evidence": [],
            "tool_calls_remaining": 10,
            "causal_graph": None,
            "root_cause": None,
            "root_cause_confidence": 0.0,
            "rca_report": None,
            "recommended_actions": [],
            "relevant_runbooks": [],
        }
        assert state["tool_calls_remaining"] == 10
        assert state["root_cause"] is None
        assert state["preloaded_metrics"] is None
        assert state["preloaded_logs"] is None

    def test_state_field_types(self) -> None:
        from src.agent.state import AgentState

        hints = get_type_hints(AgentState)
        assert hints["alert"] is dict
        assert hints["tool_calls_remaining"] is int
        assert hints["root_cause_confidence"] is float
