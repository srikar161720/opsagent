"""Unit tests for the OpsAgent LangGraph workflow.

Tests graph compilation, routing logic, and node behavior.
LLM calls are mocked to avoid API key requirements in unit tests.
"""

from __future__ import annotations


class TestBuildGraph:
    """Tests for graph compilation."""

    def test_build_graph_returns_compiled(self) -> None:
        from src.agent.graph import build_graph

        graph = build_graph()
        assert graph is not None
        assert hasattr(graph, "invoke")

    def test_graph_has_expected_type(self) -> None:
        from src.agent.graph import build_graph

        graph = build_graph()
        type_name = type(graph).__name__
        assert "CompiledStateGraph" in type_name or "Compiled" in type_name


class TestShouldContinue:
    """Tests for the should_continue routing function."""

    def test_returns_end_on_high_confidence(self) -> None:
        from src.agent.graph import should_continue

        state = {
            "root_cause_confidence": 0.85,
            "tool_calls_remaining": 5,
        }
        assert should_continue(state) == "end"

    def test_returns_end_on_zero_budget(self) -> None:
        from src.agent.graph import should_continue

        state = {
            "root_cause_confidence": 0.3,
            "tool_calls_remaining": 0,
        }
        assert should_continue(state) == "end"

    def test_returns_continue_when_low_confidence_and_budget(self) -> None:
        from src.agent.graph import should_continue

        state = {
            "root_cause_confidence": 0.4,
            "tool_calls_remaining": 5,
        }
        assert should_continue(state) == "continue"

    def test_returns_end_at_exact_threshold(self) -> None:
        from src.agent.graph import should_continue

        state = {
            "root_cause_confidence": 0.7,
            "tool_calls_remaining": 3,
        }
        assert should_continue(state) == "end"

    def test_returns_continue_just_below_threshold(self) -> None:
        from src.agent.graph import should_continue

        state = {
            "root_cause_confidence": 0.69,
            "tool_calls_remaining": 1,
        }
        assert should_continue(state) == "continue"


class TestAnalyzeContextNode:
    """Tests for the analyze_context_node."""

    def test_sets_messages(self) -> None:
        from src.agent.graph import analyze_context_node

        state = {
            "alert": {
                "title": "Test Alert",
                "severity": "high",
                "anomaly_score": 0.5,
            },
            "affected_services": ["cartservice"],
            "tool_calls_remaining": 10,
        }
        result = analyze_context_node(state)
        assert "messages" in result
        assert len(result["messages"]) > 0

    def test_preserves_tool_budget(self) -> None:
        from src.agent.graph import analyze_context_node

        state = {
            "alert": {"title": "Test"},
            "affected_services": ["frontend"],
            "tool_calls_remaining": 10,
        }
        result = analyze_context_node(state)
        assert result["tool_calls_remaining"] == 10


class TestHelperFunctions:
    """Tests for graph helper functions."""

    def test_parse_hypotheses_valid_json(self) -> None:
        from src.agent.graph import _parse_hypotheses

        content = (
            'Some text [{"service": "redis", "reason": "test",'
            ' "confidence": 0.8, "status": "investigating"}] more text'
        )
        result = _parse_hypotheses(content, [])
        assert len(result) == 1
        assert result[0]["service"] == "redis"

    def test_parse_hypotheses_invalid_json(self) -> None:
        from src.agent.graph import _parse_hypotheses

        existing = [{"service": "old", "confidence": 0.5}]
        result = _parse_hypotheses("no json here", existing)
        assert result == existing

    def test_extract_actions(self) -> None:
        from src.agent.graph import _extract_actions

        report = """
RECOMMENDED ACTIONS
───────────────────
Immediate:
1. Restart the service
2. Clear the cache

Long-term:
3. Add monitoring
═══════════════════
"""
        actions = _extract_actions(report)
        assert len(actions) >= 2
        assert "Restart the service" in actions
