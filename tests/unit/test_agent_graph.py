"""Unit tests for the OpsAgent LangGraph workflow.

Tests graph compilation, routing logic, and node behavior.
LLM calls are mocked to avoid API key requirements in unit tests.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


class TestGetLLM:
    """Tests for the LLM factory function."""

    def test_uses_gemini_3_flash_preview(self) -> None:
        """_get_llm must instantiate ChatGoogleGenerativeAI with gemini-3-flash-preview."""
        from src.agent import graph

        with patch.object(graph, "ChatGoogleGenerativeAI") as mock_llm_cls:
            graph._get_llm()
            mock_llm_cls.assert_called_once()
            kwargs = mock_llm_cls.call_args.kwargs
            assert kwargs["model"] == "gemini-3-flash-preview"


class TestExtractText:
    """Tests for _extract_text — normalises LLM response.content across
    Gemini 2.x (string) and Gemini 3.x (list of content parts) formats."""

    def _resp(self, content):
        """Build a minimal object with a .content attribute."""

        class _R:
            pass

        r = _R()
        r.content = content
        return r

    def test_plain_string_content(self) -> None:
        """Gemini 2.x-style plain-string content is returned as-is."""
        from src.agent.graph import _extract_text

        assert _extract_text(self._resp("hello world")) == "hello world"

    def test_list_of_text_parts(self) -> None:
        """Gemini 3.x returns a list of {type, text, ...} dicts."""
        from src.agent.graph import _extract_text

        content = [
            {"type": "text", "text": "first chunk"},
            {"type": "text", "text": "second chunk"},
        ]
        result = _extract_text(self._resp(content))
        assert "first chunk" in result
        assert "second chunk" in result

    def test_list_skips_non_text_parts(self) -> None:
        """Gemini 3 thinking/signature parts must not leak into output."""
        from src.agent.graph import _extract_text

        content = [
            {"type": "thought", "text": "reasoning..."},
            {"type": "text", "text": "actual reply"},
            {"type": "text", "text": "continuation", "extras": {"signature": "zzz"}},
        ]
        result = _extract_text(self._resp(content))
        assert "reasoning..." not in result
        assert "actual reply" in result
        assert "continuation" in result

    def test_list_with_bare_strings(self) -> None:
        """Mixed list of dicts and plain strings is also handled."""
        from src.agent.graph import _extract_text

        content = [{"type": "text", "text": "a"}, "b"]
        assert "a" in _extract_text(self._resp(content))
        assert "b" in _extract_text(self._resp(content))

    def test_missing_or_none_content(self) -> None:
        """Absent or None content returns empty string, never raises."""
        from src.agent.graph import _extract_text

        assert _extract_text(self._resp(None)) == ""

        class _Empty:
            pass

        assert _extract_text(_Empty()) == ""


class TestSystemPrompt:
    """Tests for the investigation system prompt content."""

    def test_all_seven_services_referenced(self) -> None:
        """Every OTel Demo service must appear at least once in the prompt examples."""
        from src.agent.prompts.system_prompt import SYSTEM_PROMPT

        for svc in (
            "cartservice",
            "checkoutservice",
            "currencyservice",
            "frontend",
            "paymentservice",
            "productcatalogservice",
            "redis",
        ):
            assert svc in SYSTEM_PROMPT, f"Expected '{svc}' to appear in system prompt examples"

    def test_anti_bias_clause_present(self) -> None:
        """Explicit anti-bias instruction must be present."""
        from src.agent.prompts.system_prompt import SYSTEM_PROMPT

        assert "Do not default to any service" in SYSTEM_PROMPT


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


class TestSweepProbesNode:
    """Tests for sweep_probes_node: 4-metric probe + crash-log sweep."""

    _SIX_SERVICES = [
        "cartservice",
        "checkoutservice",
        "frontend",
        "paymentservice",
        "productcatalogservice",
        "redis",
    ]

    def _fake_metric_result(self, critical: bool = False) -> dict:
        return {
            "anomalous": critical,
            "stats": {"mean": 1.0},
            "note": "CRITICAL: simulated" if critical else "",
            "values": [1.0] * 40,
            "timestamps": [],
        }

    def _fake_log_result(self, critical_service: str | None = None) -> dict:
        out: dict = {
            "entries": [],
            "total_count": 0,
            "error_count": 0,
            "top_patterns": [],
            "crash_match_count": 5 if critical_service else 0,
        }
        if critical_service:
            out["critical_service"] = critical_service
            out["anomalous"] = True
            out["note"] = f"CRITICAL: {critical_service} crashing"
        return out

    def test_sweeps_all_affected_services(self) -> None:
        """Every service must be queried across 4 metrics AND one crash-log
        search. 6 services × 4 metrics + 6 log calls = 30 total sweep calls."""
        from src.agent import graph

        with (
            patch("src.agent.tools.query_metrics.query_metrics") as mock_qm,
            patch("src.agent.tools.search_logs.search_logs") as mock_lg,
        ):
            mock_qm.invoke = MagicMock(return_value=self._fake_metric_result())
            mock_lg.invoke = MagicMock(return_value=self._fake_log_result())
            state = {
                "alert": {},
                "affected_services": self._SIX_SERVICES,
                "anomaly_window": ("ts", "ts"),
                "tool_calls_remaining": 10,
                "evidence": [],
                "start_time": None,
            }
            result = graph.sweep_probes_node(state)

        # 6 services × 4 metrics = 24 metric calls
        assert mock_qm.invoke.call_count == 24
        # 6 services × 1 crash-log call = 6 log calls
        assert mock_lg.invoke.call_count == 6

        # Every metric call hits one of the 4 sweep metrics, evenly distributed
        metrics = [
            (call.args[0] if call.args else call.kwargs)["metric_name"]
            for call in mock_qm.invoke.call_args_list
        ]
        for m in ("probe_up", "probe_latency", "cpu_usage", "memory_usage"):
            assert metrics.count(m) == 6

        # Evidence has 24 metric + 6 log = 30 sweep entries
        sweep_evidence = [
            e for e in result["evidence"] if e.get("args", {}).get("pre_gathered")
        ]
        assert len(sweep_evidence) == 30

    def test_critical_flag_propagated_to_args(self) -> None:
        """When a metric result has CRITICAL in its note, the evidence
        entry's args['critical'] flag must be True — analyze_causation_node
        relies on this to bypass the finding-field truncation."""
        from src.agent import graph

        with (
            patch("src.agent.tools.query_metrics.query_metrics") as mock_qm,
            patch("src.agent.tools.search_logs.search_logs") as mock_lg,
        ):
            # Make probe_latency for frontend critical; everything else clean.
            def qm_side(qargs: dict) -> dict:
                svc = qargs["service_name"]
                metric = qargs["metric_name"]
                crit = svc == "frontend" and metric == "probe_latency"
                return self._fake_metric_result(critical=crit)

            mock_qm.invoke = MagicMock(side_effect=qm_side)
            mock_lg.invoke = MagicMock(return_value=self._fake_log_result())
            state = {
                "alert": {},
                "affected_services": self._SIX_SERVICES,
                "anomaly_window": ("ts", "ts"),
                "tool_calls_remaining": 10,
                "evidence": [],
                "start_time": None,
            }
            result = graph.sweep_probes_node(state)

        sweep = [e for e in result["evidence"] if e.get("args", {}).get("pre_gathered")]
        critical_entries = [e for e in sweep if e["args"].get("critical")]
        assert len(critical_entries) == 1
        assert critical_entries[0]["args"]["service_name"] == "frontend"
        assert critical_entries[0]["args"]["metric_name"] == "probe_latency"

    def test_log_crash_signal_propagates(self) -> None:
        """When search_logs returns a critical_service, the corresponding
        evidence entry's args['critical'] flag must be True."""
        from src.agent import graph

        with (
            patch("src.agent.tools.query_metrics.query_metrics") as mock_qm,
            patch("src.agent.tools.search_logs.search_logs") as mock_lg,
        ):
            mock_qm.invoke = MagicMock(return_value=self._fake_metric_result())
            # checkoutservice crashing; others clean.
            def lg_side(largs: dict) -> dict:
                svc = largs["service_filter"]
                return self._fake_log_result(
                    critical_service=svc if svc == "checkoutservice" else None
                )

            mock_lg.invoke = MagicMock(side_effect=lg_side)
            state = {
                "alert": {},
                "affected_services": self._SIX_SERVICES,
                "anomaly_window": ("ts", "ts"),
                "tool_calls_remaining": 10,
                "evidence": [],
                "start_time": None,
            }
            result = graph.sweep_probes_node(state)

        sweep_logs = [
            e
            for e in result["evidence"]
            if e.get("tool") == "search_logs" and e.get("args", {}).get("pre_gathered")
        ]
        critical_logs = [e for e in sweep_logs if e["args"].get("critical")]
        assert len(critical_logs) == 1
        assert critical_logs[0]["args"]["service_filter"] == "checkoutservice"

    def test_does_not_decrement_budget(self) -> None:
        """Sweep is mandatory infra — does not consume tool_calls_remaining.
        The node should not emit a 'tool_calls_remaining' key at all."""
        from src.agent import graph

        fake_result = {"anomalous": False, "stats": {}, "note": "", "values": [1.0]}
        with patch("src.agent.tools.query_metrics.query_metrics") as mock_qm:
            mock_qm.invoke = MagicMock(return_value=fake_result)
            state = {
                "alert": {},
                "affected_services": ["cartservice", "redis"],
                "anomaly_window": ("ts", "ts"),
                "tool_calls_remaining": 10,
                "evidence": [],
                "start_time": None,
            }
            result = graph.sweep_probes_node(state)

        assert "tool_calls_remaining" not in result

    def test_threads_start_time(self) -> None:
        """If state['start_time'] is set, it must be passed to each query."""
        from src.agent import graph

        fake_result = {"anomalous": False, "stats": {}, "note": "", "values": [1.0]}
        with patch("src.agent.tools.query_metrics.query_metrics") as mock_qm:
            mock_qm.invoke = MagicMock(return_value=fake_result)
            state = {
                "alert": {},
                "affected_services": ["cartservice"],
                "anomaly_window": ("ts", "ts"),
                "tool_calls_remaining": 10,
                "evidence": [],
                "start_time": "2026-04-17T00:00:00+00:00",
            }
            graph.sweep_probes_node(state)

        call_args = mock_qm.invoke.call_args.args[0]
        assert call_args["start_time"] == "2026-04-17T00:00:00+00:00"


class TestKnockoutNode:
    """Tests for knockout_node — downstream-falsification sanity check."""

    def _sweep_entry(self, service: str, critical: bool, metric: str = "probe_up") -> dict:
        return {
            "tool": "query_metrics",
            "args": {
                "service_name": service,
                "metric_name": metric,
                "pre_gathered": True,
                "critical": critical,
            },
            "finding": "",
            "timestamp": "",
        }

    def test_skipped_when_confidence_high(self) -> None:
        """Confidence ≥ 0.75 means the CRITICAL override already fired —
        knockout must not second-guess it."""
        from src.agent.graph import knockout_node

        state = {
            "root_cause": "redis",
            "root_cause_confidence": 0.78,
            "hypotheses": [{"service": "cartservice", "confidence": 0.9}],
            "evidence": [self._sweep_entry("cartservice", critical=True)],
        }
        result = knockout_node(state)
        assert "root_cause" not in result  # passthrough — don't overwrite state
        assert "skipped" in result["messages"][0].content.lower()

    def test_skipped_when_root_cause_unknown(self) -> None:
        from src.agent.graph import knockout_node

        for rc in ("", "unknown", "inconclusive"):
            state = {
                "root_cause": rc,
                "root_cause_confidence": 0.3,
                "hypotheses": [],
                "evidence": [],
            }
            result = knockout_node(state)
            assert "root_cause" not in result

    def test_passthrough_when_rootcause_has_more_critical(self) -> None:
        """root_cause=frontend has 2 CRITICAL signals, best alternative
        (productcatalogservice) has 0 — keep root_cause."""
        from src.agent.graph import knockout_node

        state = {
            "root_cause": "frontend",
            "root_cause_confidence": 0.50,
            "hypotheses": [
                {"service": "frontend", "confidence": 0.6},
                {"service": "productcatalogservice", "confidence": 0.4},
            ],
            "evidence": [
                self._sweep_entry("frontend", critical=True, metric="probe_latency"),
                self._sweep_entry("frontend", critical=True, metric="cpu_usage"),
                self._sweep_entry("productcatalogservice", critical=False),
            ],
        }
        result = knockout_node(state)
        assert "root_cause" not in result
        assert "keeping root_cause" in result["messages"][0].content.lower()

    def test_flip_when_alternative_has_more_critical(self) -> None:
        """root_cause=productcatalogservice has 0 CRITICAL signals; the
        top-3 candidate frontend has 2 CRITICAL signals → flip to frontend."""
        from src.agent.graph import knockout_node

        state = {
            "root_cause": "productcatalogservice",
            "root_cause_confidence": 0.35,
            "hypotheses": [
                {"service": "productcatalogservice", "confidence": 0.35},
                {"service": "frontend", "confidence": 0.30},
                {"service": "paymentservice", "confidence": 0.20},
            ],
            "evidence": [
                self._sweep_entry("frontend", critical=True, metric="probe_latency"),
                self._sweep_entry("frontend", critical=True, metric="cpu_usage"),
                self._sweep_entry("productcatalogservice", critical=False),
                self._sweep_entry("paymentservice", critical=False),
            ],
        }
        result = knockout_node(state)
        assert result["root_cause"] == "frontend"
        # Confidence raised moderately (stays <0.75 since we're not in
        # the CRITICAL-override band).
        assert 0.45 <= result["root_cause_confidence"] < 0.75

    def test_tie_keeps_root_cause(self) -> None:
        """Tie on CRITICAL-signal count → keep root_cause (don't flip)."""
        from src.agent.graph import knockout_node

        state = {
            "root_cause": "redis",
            "root_cause_confidence": 0.45,
            "hypotheses": [
                {"service": "redis", "confidence": 0.4},
                {"service": "cartservice", "confidence": 0.4},
            ],
            "evidence": [
                self._sweep_entry("redis", critical=True),
                self._sweep_entry("cartservice", critical=True),
            ],
        }
        result = knockout_node(state)
        assert "root_cause" not in result

    def test_ignores_non_sweep_evidence(self) -> None:
        """LLM-invoked tool calls (no pre_gathered flag) don't carry a
        reliable critical flag and must be ignored by the knockout scorer."""
        from src.agent.graph import knockout_node

        state = {
            "root_cause": "redis",
            "root_cause_confidence": 0.4,
            "hypotheses": [{"service": "cartservice", "confidence": 0.5}],
            "evidence": [
                # Non-sweep entry with critical flag set — should be ignored.
                {
                    "tool": "query_metrics",
                    "args": {
                        "service_name": "cartservice",
                        "metric_name": "probe_up",
                        "critical": True,
                    },  # no pre_gathered
                    "finding": "",
                },
                self._sweep_entry("redis", critical=True),
            ],
        }
        result = knockout_node(state)
        # Root cause should be redis (1 critical) vs cartservice (0 critical
        # counted), so no flip.
        assert "root_cause" not in result


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


class TestGenerateReportNode:
    """Tests for generate_report_node: Python-side template substitution."""

    def _fake_response(self, content: str):
        """Build a minimal object that looks like a LangChain AIMessage."""

        class _Resp:
            def __init__(self, c: str) -> None:
                self.content = c

        return _Resp(content)

    def test_causal_graph_ascii_substituted(self) -> None:
        """The structural placeholder {causal_graph_ascii} must be substituted
        in Python, not left literal for the LLM to (mis)handle."""
        from src.agent import graph

        fake_llm = type(
            "_FakeLLM",
            (),
            {
                "invoke": lambda self, msgs: self._fake_response(
                    '{"summary":"s","evidence_chain":"e","root_cause_component":"c",'
                    '"root_cause_issue":"i","immediate_actions":"a","longterm_actions":"l",'
                    '"relevant_docs":"d"}'
                )
            },
        )
        fake_llm._fake_response = lambda self, c: type("_R", (), {"content": c})()

        state = {
            "alert": {"title": "Test", "timestamp": "2026-01-01T00:00:00Z", "severity": "high"},
            "causal_graph": {
                "graph_ascii": "A --> B [0.9]",
                "counterfactual": "If A had not failed, B would survive.",
            },
            "evidence": [],
            "hypotheses": [],
            "root_cause": "cartservice",
            "root_cause_confidence": 0.75,
            "relevant_runbooks": [],
            "messages": [],
        }
        with patch.object(graph, "_get_llm", return_value=fake_llm()):
            result = graph.generate_report_node(state)
        report = result["rca_report"]
        assert "A --> B [0.9]" in report
        assert "{causal_graph_ascii}" not in report
        assert "{counterfactual_explanation}" not in report

    def test_no_unfilled_placeholders(self) -> None:
        """Even with a malformed LLM response (non-JSON), no raw {placeholder}
        should leak through."""
        from src.agent import graph

        class _FakeLLM:
            def invoke(self, msgs):
                return type("_R", (), {"content": "not json at all"})()

        state = {
            "alert": {"title": "Test", "timestamp": "ts", "severity": "high"},
            "causal_graph": {},
            "evidence": [],
            "hypotheses": [],
            "root_cause": "redis",
            "root_cause_confidence": 0.5,
            "relevant_runbooks": [],
            "messages": [],
        }
        with patch.object(graph, "_get_llm", return_value=_FakeLLM()):
            result = graph.generate_report_node(state)
        import re as _re

        leftover = _re.findall(r"\{[a-z_]+\}", result["rca_report"])
        assert leftover == [], f"Unfilled placeholders leaked: {leftover}"

    def test_parse_report_fields_strips_markdown(self) -> None:
        """_parse_report_fields accepts ```json ... ``` fenced output."""
        from src.agent.graph import _parse_report_fields

        raw = '```json\n{"summary": "ok", "evidence_chain": "bullet"}\n```'
        result = _parse_report_fields(raw)
        assert result["summary"] == "ok"
        assert result["evidence_chain"] == "bullet"

    def test_parse_report_fields_rejects_non_json(self) -> None:
        """Non-JSON input yields an empty dict (sentinels then become N/A)."""
        from src.agent.graph import _parse_report_fields

        assert _parse_report_fields("hello world") == {}


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

    def test_parse_hypotheses_invalid_json_with_known_services(self) -> None:
        from src.agent.graph import _parse_hypotheses

        result = _parse_hypotheses("I think cartservice and redis are involved", [])
        services = [h["service"] for h in result]
        assert "cartservice" in services
        assert "redis" in services

    def test_parse_hypotheses_invalid_json_no_services(self) -> None:
        from src.agent.graph import _parse_hypotheses

        existing = [{"service": "old", "confidence": 0.5}]
        result = _parse_hypotheses("no json and no known service names here", existing)
        assert result == existing

    def test_parse_hypotheses_markdown_fenced(self) -> None:
        from src.agent.graph import _parse_hypotheses

        content = '```json\n[{"service": "redis", "confidence": 0.9}]\n```'
        result = _parse_hypotheses(content, [])
        assert len(result) == 1
        assert result[0]["service"] == "redis"
        assert result[0]["confidence"] == 0.9

    def test_parse_hypotheses_markdown_fenced_no_lang(self) -> None:
        from src.agent.graph import _parse_hypotheses

        content = '```\n[{"service": "frontend", "confidence": 0.7}]\n```'
        result = _parse_hypotheses(content, [])
        assert len(result) == 1
        assert result[0]["service"] == "frontend"

    def test_parse_hypotheses_empty_content(self) -> None:
        from src.agent.graph import _parse_hypotheses

        existing = [{"service": "x"}]
        result = _parse_hypotheses("", existing)
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
