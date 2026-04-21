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

    def test_uses_max_retries_6(self) -> None:
        """_get_llm must set max_retries=6 for Gemini API rate-limit resilience.

        Tenacity's exponential backoff at 6 retries gives ~1+2+4+8+16+32 = 63s
        of tolerance per LLM call — long enough to clear a per-minute rate-
        limit window on gemini-3-flash-preview. Bumped from =3 in Session 15
        after RCAEval RE1-OB hit sustained 429 RESOURCE_EXHAUSTED."""
        from src.agent import graph

        with patch.object(graph, "ChatGoogleGenerativeAI") as mock_llm_cls:
            graph._get_llm()
            kwargs = mock_llm_cls.call_args.kwargs
            assert kwargs["max_retries"] == 6, (
                f"max_retries must be 6 (got {kwargs.get('max_retries')!r}). "
                f"Kept in sync with tests/evaluation/baseline_comparison.py's "
                f"LLMWithoutToolsBaseline.predict()."
            )

    def test_live_factory_uses_gemini_3_flash_preview(self) -> None:
        """_get_llm_live() uses Gemini 3 Flash Preview — preserves Session
        13's 100% Recall@1 on live OTel Demo fault injection."""
        from src.agent import graph

        with patch.object(graph, "ChatGoogleGenerativeAI") as mock_llm_cls:
            graph._get_llm_live()
            kwargs = mock_llm_cls.call_args.kwargs
            assert kwargs["model"] == "gemini-3-flash-preview"
            assert kwargs["max_retries"] == 6

    def test_offline_factory_uses_gemini_2_5_flash(self) -> None:
        """_get_llm_offline() uses Gemini 2.5 Flash (production model) —
        avoids the preview-model rate-limit caps that made RCAEval runs
        fail with 429 RESOURCE_EXHAUSTED even after RPD reset."""
        from src.agent import graph

        with patch.object(graph, "ChatGoogleGenerativeAI") as mock_llm_cls:
            graph._get_llm_offline()
            kwargs = mock_llm_cls.call_args.kwargs
            assert kwargs["model"] == "gemini-2.5-flash", (
                f"Offline LLM must be gemini-2.5-flash (production, higher "
                f"quotas). Got {kwargs.get('model')!r}."
            )
            assert kwargs["max_retries"] == 6

    def test_dispatcher_picks_live_when_no_preloaded_metrics(self) -> None:
        """State without preloaded_metrics → live Gemini 3 Preview LLM."""
        from src.agent import graph

        state = {"preloaded_metrics": None}
        with (
            patch.object(graph, "_get_llm_live") as mock_live,
            patch.object(graph, "_get_llm_offline") as mock_offline,
        ):
            graph._get_llm_for_state(state)
            mock_live.assert_called_once()
            mock_offline.assert_not_called()

    def test_dispatcher_picks_offline_when_preloaded_metrics_set(self) -> None:
        """State with preloaded_metrics → offline Gemini 2.5 Flash LLM.

        This is the Path B fix: RCAEval evaluator passes preloaded
        DataFrames, so the agent automatically uses the production
        model instead of the rate-limited preview model."""
        from src.agent import graph

        state = {"preloaded_metrics": {"cartservice": object()}}
        with (
            patch.object(graph, "_get_llm_live") as mock_live,
            patch.object(graph, "_get_llm_offline") as mock_offline,
        ):
            graph._get_llm_for_state(state)
            mock_live.assert_not_called()
            mock_offline.assert_called_once()

    def test_backward_compat_get_llm_is_live_alias(self) -> None:
        """_get_llm() (no _for_state suffix) returns the live-mode LLM.

        Kept as backward-compat for any existing callers / older tests.
        Must be equivalent to _get_llm_live()."""
        from src.agent import graph

        with patch.object(graph, "_get_llm_live", return_value="SENTINEL") as mock_live:
            result = graph._get_llm()
            assert result == "SENTINEL"
            mock_live.assert_called_once()


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

    def test_memory_metrics_documented(self) -> None:
        """The two new memory-saturation metrics must be listed in the
        "Available Metrics" section so the LLM knows they exist."""
        from src.agent.prompts.system_prompt import SYSTEM_PROMPT

        assert "memory_limit" in SYSTEM_PROMPT
        assert "memory_utilization" in SYSTEM_PROMPT

    def test_memory_pressure_example_updated(self) -> None:
        """The old OOMKilled-focused Example 3 was inaccurate — Go/JVM
        runtimes adapt to soft memory pressure without emitting OOMKilled
        log lines. The new example must steer the LLM to memory_utilization
        CRITICAL instead of OOM logs."""
        from src.agent.prompts.system_prompt import SYSTEM_PROMPT

        # Old copy-paste string from the pre-fix prompt
        assert "OOMKilled entries" not in SYSTEM_PROMPT
        # New guidance must explicitly reference the utilization metric near
        # the memory_pressure example.
        assert "memory_utilization" in SYSTEM_PROMPT
        assert "memory pressure" in SYSTEM_PROMPT.lower()

    def test_live_prompt_retains_currencyservice_exclusion(self) -> None:
        """Session 12's currencyservice-exclusion clause MUST stay in the
        live-mode prompt. Removing it would regress Session 13's 100%
        Recall@1 on the OTel Demo fault suite (the v1.10.0 SIGSEGV
        crash-loop creates permanent probe_up=0 baseline noise that the
        LLM would otherwise misattribute)."""
        from src.agent.prompts.system_prompt import SYSTEM_PROMPT

        assert "currencyservice is BROKEN IN BASELINE" in SYSTEM_PROMPT
        assert "never pick it as root cause" in SYSTEM_PROMPT

    def test_offline_prompt_removes_currencyservice_exclusion(self) -> None:
        """The offline prompt used by RCAEval evaluation MUST NOT contain
        the currencyservice-exclusion clause. RCAEval-OB cases have
        currencyservice as a legitimate fault target; the clause caused
        0/25 Recall@1 on those cases before the split."""
        from src.agent.prompts.system_prompt import SYSTEM_PROMPT_OFFLINE

        assert "currencyservice is BROKEN IN BASELINE" not in SYSTEM_PROMPT_OFFLINE
        assert "never pick it as root cause" not in SYSTEM_PROMPT_OFFLINE
        assert "BASELINE NOISE" not in SYSTEM_PROMPT_OFFLINE

    def test_offline_prompt_preserves_all_other_content(self) -> None:
        """OFFLINE differs from SYSTEM_PROMPT ONLY in the currencyservice
        clause. Every other paragraph, example, directive, and metric
        reference must be identical."""
        from src.agent.prompts.system_prompt import SYSTEM_PROMPT, SYSTEM_PROMPT_OFFLINE

        # All 7 examples preserved
        for marker in (
            "Example 1: Crashed service",
            "Example 2: Slow service",
            "Example 3: Memory pressure",
            "Example 4: CPU throttling",
            "Example 5: Connection exhaustion",
            "Example 6: Network partition",
            "Example 7: Config error",
        ):
            assert marker in SYSTEM_PROMPT_OFFLINE, f"Missing from OFFLINE: {marker}"

        # Redis guidance (distinct from the currencyservice clause) stays
        assert "Redis has naturally high CPU variance" in SYSTEM_PROMPT_OFFLINE
        # Anti-bias directive stays
        assert "Do not default to any service" in SYSTEM_PROMPT_OFFLINE
        # Memory guidance stays
        assert "memory_utilization" in SYSTEM_PROMPT_OFFLINE
        # Bullet-separator whitespace is clean (no double-blank-line scar
        # from the removed clause)
        assert "\n\n\n\n" not in SYSTEM_PROMPT_OFFLINE

        # The only difference should be the clause length (~633 chars)
        diff = len(SYSTEM_PROMPT) - len(SYSTEM_PROMPT_OFFLINE)
        assert 500 < diff < 800, (
            f"Unexpected size diff {diff} between SYSTEM_PROMPT and "
            f"SYSTEM_PROMPT_OFFLINE — only the currencyservice clause "
            f"(~633 chars) should differ"
        )


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
        """Every service must be queried across 5 metrics AND one crash-log
        search. 6 services × 5 metrics + 6 log calls = 36 total sweep calls.
        The 5th channel is memory_utilization, which catches memory saturation
        faults where the service adapts (no crash, no probe_up=0) but working
        set clamps at the cgroup limit."""
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

        # 6 services × 5 metrics = 30 metric calls
        assert mock_qm.invoke.call_count == 30
        # 6 services × 1 crash-log call = 6 log calls
        assert mock_lg.invoke.call_count == 6

        # Every metric call hits one of the 5 sweep metrics, evenly distributed
        metrics = [
            (call.args[0] if call.args else call.kwargs)["metric_name"]
            for call in mock_qm.invoke.call_args_list
        ]
        for m in (
            "probe_up",
            "probe_latency",
            "cpu_usage",
            "memory_usage",
            "memory_utilization",
        ):
            assert metrics.count(m) == 6

        # Evidence has 30 metric + 6 log = 36 sweep entries
        sweep_evidence = [e for e in result["evidence"] if e.get("args", {}).get("pre_gathered")]
        assert len(sweep_evidence) == 36

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
        with patch.object(graph, "_get_llm_for_state", return_value=fake_llm()):
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
        with patch.object(graph, "_get_llm_for_state", return_value=_FakeLLM()):
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


class TestFormHypothesisScopeFilter:
    """Tests for the post-LLM scope filter in ``form_hypothesis_node``.

    The filter drops hypotheses naming services outside ``affected_services``
    so Gemini 3's OTel-Demo-biased priors can't leak shippingservice /
    emailservice / recommendationservice into RCAEval-OB runs where those
    services don't exist in the case data.
    """

    def _make_fake_llm(self, hypothesis_json: str):
        """Build a fake LLM that returns a fixed hypothesis JSON string."""

        class _FakeLLM:
            def invoke(self, msgs):
                return type("_R", (), {"content": hypothesis_json})()

        return _FakeLLM()

    def test_filter_drops_out_of_scope_hypotheses(self) -> None:
        """Hypotheses naming services outside affected_services are dropped."""
        from src.agent import graph

        payload = (
            '[{"service": "shippingservice", "reason": "x", "confidence": 0.8}, '
            '{"service": "cartservice", "reason": "y", "confidence": 0.7}]'
        )
        state = {
            "alert": {},
            "affected_services": ["cartservice", "checkoutservice"],
            "evidence": [],
            "hypotheses": [],
            "messages": [],
        }
        with patch.object(graph, "_get_llm_for_state", return_value=self._make_fake_llm(payload)):
            result = graph.form_hypothesis_node(state)

        services = [h["service"] for h in result["hypotheses"]]
        assert "shippingservice" not in services
        assert "cartservice" in services

    def test_filter_preserves_all_in_scope(self) -> None:
        """When every hypothesis is in scope, none are dropped."""
        from src.agent import graph

        payload = (
            '[{"service": "adservice", "reason": "x", "confidence": 0.9}, '
            '{"service": "checkoutservice", "reason": "y", "confidence": 0.6}]'
        )
        state = {
            "alert": {},
            "affected_services": [
                "adservice",
                "cartservice",
                "checkoutservice",
            ],
            "evidence": [],
            "hypotheses": [],
            "messages": [],
        }
        with patch.object(graph, "_get_llm_for_state", return_value=self._make_fake_llm(payload)):
            result = graph.form_hypothesis_node(state)

        services = [h["service"] for h in result["hypotheses"]]
        assert services == ["adservice", "checkoutservice"]

    def test_filter_keeps_original_when_all_would_drop(self) -> None:
        """If the filter would empty the list, keep the original hypotheses
        so downstream nodes still have signal (top_3 extraction can then
        fall back to the causal graph for in-scope services)."""
        from src.agent import graph

        payload = '[{"service": "shippingservice", "reason": "x", "confidence": 0.8}]'
        state = {
            "alert": {},
            "affected_services": ["cartservice", "checkoutservice"],
            "evidence": [],
            "hypotheses": [],
            "messages": [],
        }
        with patch.object(graph, "_get_llm_for_state", return_value=self._make_fake_llm(payload)):
            result = graph.form_hypothesis_node(state)

        services = [h["service"] for h in result["hypotheses"]]
        assert services == ["shippingservice"], (
            "When every hypothesis is out of scope, keep the original list "
            "rather than silencing the LLM entirely"
        )

    def test_filter_noop_when_no_affected_services(self) -> None:
        """No filter runs when affected_services is empty (rare live case)."""
        from src.agent import graph

        payload = '[{"service": "anyservice", "reason": "x", "confidence": 0.5}]'
        state = {
            "alert": {},
            "affected_services": [],
            "evidence": [],
            "hypotheses": [],
            "messages": [],
        }
        with patch.object(graph, "_get_llm_for_state", return_value=self._make_fake_llm(payload)):
            result = graph.form_hypothesis_node(state)

        services = [h["service"] for h in result["hypotheses"]]
        assert services == ["anyservice"]


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


class TestDispatchers:
    """Tests for the tool-dispatcher functions that route live-vs-offline.

    Cardinal rule: when ``state["preloaded_metrics"]`` is None, the
    dispatcher is a pure passthrough to the live tool. When it's set, the
    dispatcher routes to the corresponding offline_data helper.
    """

    def test_query_metrics_dispatcher_routes_offline_when_preloaded(self) -> None:
        from src.agent import graph

        state = {"preloaded_metrics": {"cartservice": MagicMock()}, "preloaded_logs": None}
        args = {
            "service_name": "cartservice",
            "metric_name": "cpu_usage",
            "time_range_minutes": 10,
        }
        with patch.object(graph.offline_data, "query_preloaded_metrics") as mock_offline:
            mock_offline.return_value = {"anomalous": False, "values": []}
            graph._dispatch_query_metrics(state, args)
            assert mock_offline.called
            # Preloaded metrics threaded through to the helper
            kwargs = mock_offline.call_args.kwargs
            assert kwargs["preloaded_metrics"] is state["preloaded_metrics"]

    def test_query_metrics_dispatcher_routes_live_when_no_preload(self) -> None:
        from src.agent import graph

        state = {"preloaded_metrics": None, "preloaded_logs": None}
        args = {"service_name": "cartservice", "metric_name": "cpu_usage"}
        mock_tool = MagicMock()
        mock_tool.invoke.return_value = {"anomalous": False}
        with patch("src.agent.tools.query_metrics.query_metrics", mock_tool):
            graph._dispatch_query_metrics(state, args)
            mock_tool.invoke.assert_called_once_with(args)

    def test_search_logs_dispatcher_routes_offline_when_preloaded(self) -> None:
        from src.agent import graph

        # Offline mode is triggered by preloaded_metrics being truthy —
        # logs may still be None (RE1 has no logs).
        state = {
            "preloaded_metrics": {"cart": MagicMock()},
            "preloaded_logs": None,
        }
        args = {"query": "error", "service_filter": "cart", "time_range_minutes": 10}
        with patch.object(graph.offline_data, "search_preloaded_logs") as mock_offline:
            mock_offline.return_value = {"entries": [], "crash_match_count": 0}
            graph._dispatch_search_logs(state, args)
            assert mock_offline.called
            # Preloaded logs threaded through (None is valid — helper
            # returns empty response)
            kwargs = mock_offline.call_args.kwargs
            assert kwargs["preloaded_logs"] is None

    def test_search_logs_dispatcher_routes_live_when_no_preload(self) -> None:
        from src.agent import graph

        state = {"preloaded_metrics": None, "preloaded_logs": None}
        args = {"query": "error"}
        mock_tool = MagicMock()
        mock_tool.invoke.return_value = {"entries": []}
        with patch("src.agent.tools.search_logs.search_logs", mock_tool):
            graph._dispatch_search_logs(state, args)
            mock_tool.invoke.assert_called_once_with(args)

    def test_discover_causation_dispatcher_routes_offline_when_preloaded(self) -> None:
        from src.agent import graph

        state = {"preloaded_metrics": {"a": MagicMock(), "b": MagicMock()}, "preloaded_logs": None}
        args = {"services": ["a", "b"], "time_range_minutes": 10}
        with patch.object(graph.offline_data, "discover_causation_from_df") as mock_offline:
            mock_offline.return_value = {
                "causal_edges": [],
                "root_cause": "a",
                "root_cause_confidence": 0.0,
                "counterfactual": "",
                "graph_ascii": "",
            }
            graph._dispatch_discover_causation(state, args)
            assert mock_offline.called

    def test_discover_causation_dispatcher_routes_live_when_no_preload(self) -> None:
        from src.agent import graph

        state = {"preloaded_metrics": None, "preloaded_logs": None}
        args = {"services": ["a", "b"], "time_range_minutes": 10}
        mock_tool = MagicMock()
        mock_tool.invoke.return_value = {
            "causal_edges": [],
            "root_cause": "a",
            "root_cause_confidence": 0.0,
            "counterfactual": "",
            "graph_ascii": "",
        }
        with patch.object(graph, "discover_causation", mock_tool):
            graph._dispatch_discover_causation(state, args)
            mock_tool.invoke.assert_called_once_with(args)

    def test_dispatchers_registry_covers_live_infra_tools(self) -> None:
        """_DISPATCHERS_BY_NAME must cover every live-infra tool."""
        from src.agent import graph

        assert "query_metrics" in graph._DISPATCHERS_BY_NAME
        assert "search_logs" in graph._DISPATCHERS_BY_NAME
        assert "discover_causation" in graph._DISPATCHERS_BY_NAME
        # Non-infra tools (get_topology, search_runbooks) are NOT in the
        # dispatcher map — they're invoked via _TOOLS_BY_NAME directly.
        assert "get_topology" not in graph._DISPATCHERS_BY_NAME
        assert "search_runbooks" not in graph._DISPATCHERS_BY_NAME
