"""Tests for src.serving.dashboard_helpers and src.serving.theme.

Pure helpers — no Streamlit scriptrunner, no live API. The Streamlit module
itself is NOT tested here (it's a thin UI wrapper); we smoke-test its
importability instead.
"""

from __future__ import annotations

from datetime import UTC, datetime

from src.serving.dashboard_helpers import (
    DEMO_PHASE_LABELS,
    DEMO_PHASE_TO_INT,
    DEMO_SERVICE_CHOICES,
    DEMO_SERVICE_FAULT_LABELS,
    METRIC_CHOICES,
    SERVICE_CHOICES,
    build_alert_payload,
    build_demo_investigation_request,
    build_investigation_request,
    build_topology_dot,
    component_pill_severity,
    extract_root_cause_issue,
    format_confidence,
    format_duration_seconds,
    format_top3_ranked_html,
    health_pill_for_overall,
    parse_evidence_from_report,
    summarize_investigations,
)
from src.serving.theme import (
    PALETTE,
    PHASE_LABELS,
    evidence_timeline,
    hero_bar,
    investigation_card,
    kpi_tile,
    phase_stepper,
    root_cause_card,
    section_divider,
    status_pill,
)

# ---------------------------------------------------------------------------
# Theme builders
# ---------------------------------------------------------------------------


class TestStatusPill:
    def test_known_severity(self) -> None:
        html = status_pill("CRITICAL", "critical")
        assert "status-pill" in html
        assert "status-pill--critical" in html
        assert "CRITICAL" in html

    def test_unknown_severity_falls_back_to_muted(self) -> None:
        assert "status-pill--muted" in status_pill("?", "nonsense")


class TestKPITile:
    def test_minimal(self) -> None:
        html = kpi_tile("Runs", "35")
        assert "kpi-tile__label" in html
        assert "Runs" in html
        assert "35" in html
        assert "kpi-tile__trend" not in html

    def test_with_trend_up(self) -> None:
        html = kpi_tile("Runs", "35", "↗ +5", trend_direction="up")
        assert "kpi-tile__trend--up" in html
        assert "↗ +5" in html


class TestRootCauseCard:
    def test_at_75pct(self) -> None:
        html = root_cause_card("cartservice", "memory pressure", 0.75)
        assert "cartservice" in html
        assert "75%" in html
        # Stroke-dashoffset for the SVG fg ring: 276.5 * (1 - 0.75) = 69.125
        assert "69.12" in html or "69.125" in html

    def test_clamps_out_of_range(self) -> None:
        html = root_cause_card("svc", "issue", confidence=1.5)
        assert "100%" in html

    def test_negative_clamps_to_zero(self) -> None:
        html = root_cause_card("svc", "issue", confidence=-0.3)
        assert "0%" in html

    def test_empty_issue_skips_issue_div(self) -> None:
        """Empty issue should suppress the issue slot entirely — otherwise
        a blank div can render unwanted spacing / borders."""
        html = root_cause_card("cartservice", "", 0.75)
        assert "cartservice" in html
        assert 'class="rc-card__issue"' not in html

    def test_whitespace_issue_skips_issue_div(self) -> None:
        html = root_cause_card("cartservice", "   \n  ", 0.75)
        assert 'class="rc-card__issue"' not in html

    def test_body_column_wrapped_in_rc_card_body(self) -> None:
        """The body column needs a dedicated class so CSS can set flex-basis
        + min-width:0 on it (otherwise the ring overflows narrow containers)."""
        html = root_cause_card("cartservice", "memory pressure", 0.75)
        assert 'class="rc-card__body"' in html


class TestPhaseStepper:
    def test_none_active_renders_all_pending(self) -> None:
        html = phase_stepper(None)
        assert html.count("phase-step--active") == 0
        assert html.count("phase-step--done") == 0

    def test_mid_investigation(self) -> None:
        html = phase_stepper(2)  # EVIDENCE active
        # Phases 0, 1 done; 2 active; 3, 4 pending.
        assert html.count("phase-step--done") == 2
        assert html.count("phase-step--active") == 1

    def test_labels_match_constant(self) -> None:
        html = phase_stepper(0)
        for label in PHASE_LABELS:
            assert label in html

    def test_custom_labels_render(self) -> None:
        """Guided-demo UI passes its own 5 labels; they must round-trip."""
        html = phase_stepper(1, labels=DEMO_PHASE_LABELS)
        for label in DEMO_PHASE_LABELS:
            assert label in html
        # Legacy labels should be absent when a custom list is given.
        for legacy_only in ("Sweep", "Hypotheses", "Causation"):
            assert legacy_only not in html

    def test_active_phase_at_length_renders_all_done(self) -> None:
        """`completed` (phase == len(labels)) shows every step as done."""
        html = phase_stepper(len(DEMO_PHASE_LABELS), labels=DEMO_PHASE_LABELS)
        assert html.count("phase-step--done") == len(DEMO_PHASE_LABELS)
        assert html.count("phase-step--active") == 0


class TestEvidenceTimeline:
    def test_empty(self) -> None:
        html = evidence_timeline([])
        assert 'class="timeline"' in html

    def test_renders_entries(self) -> None:
        entries = [
            {"time": "03:59", "text": "probe_up=0 CRITICAL", "severity": "critical"},
            {"time": "04:00", "text": "frontend 500 errors", "severity": "warn"},
        ]
        html = evidence_timeline(entries)
        assert html.count("timeline-entry") >= 2
        assert "03:59" in html
        assert "04:00" in html
        assert "CRITICAL" in html

    def test_unknown_severity_renders(self) -> None:
        html = evidence_timeline([{"time": "t", "text": "noted", "severity": "mystery"}])
        assert "timeline-entry" in html
        assert "noted" in html


class TestHeroBar:
    def test_contains_title_and_pill(self) -> None:
        html = hero_bar("Overview", "subtitle", "API HEALTHY", "success")
        assert "Overview" in html
        assert "subtitle" in html
        assert "API HEALTHY" in html
        assert "status-pill--success" in html


class TestInvestigationCard:
    def test_completed(self) -> None:
        inv = {
            "investigation_id": "inv_abc",
            "status": "completed",
            "root_cause": {"service": "cartservice", "confidence": 0.75},
            "duration_seconds": 24.2,
        }
        html = investigation_card(inv)
        assert "inv_abc" in html
        assert "cartservice" in html
        assert "75%" in html
        assert "24.2s" in html
        assert "status-pill--success" in html

    def test_failed(self) -> None:
        html = investigation_card(
            {
                "investigation_id": "inv_f",
                "status": "failed",
                "root_cause": None,
                "duration_seconds": 1.1,
            }
        )
        assert "status-pill--error" in html


class TestSectionDivider:
    def test_contains_label(self) -> None:
        assert "Recent" in section_divider("Recent")


class TestPaletteConstants:
    def test_palette_has_all_semantic_colors(self) -> None:
        for name in ("primary", "bg", "text", "success", "warn", "error", "critical"):
            assert getattr(PALETTE, name).startswith(("#", "rgba"))


# ---------------------------------------------------------------------------
# dashboard_helpers — health & component pills
# ---------------------------------------------------------------------------


class TestHealthPills:
    def test_healthy(self) -> None:
        assert health_pill_for_overall("healthy") == ("API HEALTHY", "success")

    def test_degraded(self) -> None:
        assert health_pill_for_overall("degraded") == ("API DEGRADED", "warn")

    def test_other(self) -> None:
        label, sev = health_pill_for_overall("unreachable")
        assert sev == "error"


class TestComponentPillSeverity:
    def test_connected(self) -> None:
        assert component_pill_severity("connected") == "success"

    def test_available(self) -> None:
        assert component_pill_severity("available") == "success"

    def test_unreachable(self) -> None:
        assert component_pill_severity("unreachable") == "error"

    def test_unconfigured(self) -> None:
        assert component_pill_severity("unconfigured") == "warn"

    def test_other(self) -> None:
        assert component_pill_severity("wizardmode") == "muted"


# ---------------------------------------------------------------------------
# dashboard_helpers — summaries & formatters
# ---------------------------------------------------------------------------


class TestSummarizeInvestigations:
    def test_empty(self) -> None:
        summary = summarize_investigations([])
        assert summary["total"] == 0
        assert summary["completed"] == 0
        assert summary["mean_confidence"] == 0.0
        assert summary["mean_duration_seconds"] == 0.0

    def test_mixed(self) -> None:
        investigations = [
            {
                "status": "completed",
                "root_cause": {"service": "cartservice", "confidence": 0.75},
                "duration_seconds": 20.0,
            },
            {
                "status": "completed",
                "root_cause": {"service": "redis", "confidence": 0.75},
                "duration_seconds": 30.0,
            },
            {"status": "failed", "root_cause": None, "duration_seconds": 1.0},
        ]
        summary = summarize_investigations(investigations)
        assert summary["total"] == 3
        assert summary["completed"] == 2
        assert abs(summary["mean_confidence"] - 0.75) < 1e-6
        assert abs(summary["mean_duration_seconds"] - 25.0) < 1e-6


class TestFormatters:
    def test_format_duration_under_a_minute(self) -> None:
        assert format_duration_seconds(24.5) == "24.5s"

    def test_format_duration_over_a_minute(self) -> None:
        assert format_duration_seconds(75.0) == "1m 15s"

    def test_format_duration_zero(self) -> None:
        assert format_duration_seconds(0.0) == "—"

    def test_format_confidence(self) -> None:
        assert format_confidence(0.75) == "75%"
        assert format_confidence(0.0) == "0%"
        assert format_confidence(1.0) == "100%"


# ---------------------------------------------------------------------------
# dashboard_helpers — evidence parsing
# ---------------------------------------------------------------------------


_SAMPLE_REPORT = """\
═══════════════════════════════════════════════════════════════════
                   ROOT CAUSE ANALYSIS REPORT
═══════════════════════════════════════════════════════════════════

EXECUTIVE SUMMARY
───────────────────────────────────────────────────────────────────
The cartservice experienced a critical outage...

ROOT CAUSE  (Confidence: 75%)
───────────────────────────────────────────────────────────────────
Service  : cartservice

EVIDENCE CHAIN  (chronological)
───────────────────────────────────────────────────────────────────
- **03:57:42 UTC**: cartservice memory utilization begins a sharp climb
- **03:58:12 UTC**: network receive rate drops to zero (sparse data)
- **03:59:12 UTC**: probe_up drops to 0 (CRITICAL)
- **03:59:37 UTC**: frontend logs record 500 errors and ECONNREFUSED

CAUSAL ANALYSIS
───────────────────────────────────────────────────────────────────
"""


class TestParseEvidence:
    def test_none(self) -> None:
        assert parse_evidence_from_report(None) == []

    def test_empty(self) -> None:
        assert parse_evidence_from_report("") == []

    def test_parses_four_entries(self) -> None:
        entries = parse_evidence_from_report(_SAMPLE_REPORT)
        assert len(entries) == 4
        assert entries[0]["time"] == "03:57:42 UTC"
        assert "memory utilization" in entries[0]["text"]
        assert entries[0]["severity"] == "info"

    def test_infers_critical_severity(self) -> None:
        entries = parse_evidence_from_report(_SAMPLE_REPORT)
        critical_entries = [e for e in entries if e["severity"] == "critical"]
        assert len(critical_entries) >= 1
        assert any("CRITICAL" in e["text"] for e in critical_entries)

    def test_infers_warn_severity(self) -> None:
        entries = parse_evidence_from_report(_SAMPLE_REPORT)
        # '500 errors' and 'sparse' both map to warn.
        warn_entries = [e for e in entries if e["severity"] == "warn"]
        assert len(warn_entries) >= 1


# ---------------------------------------------------------------------------
# dashboard_helpers — topology DOT builder
# ---------------------------------------------------------------------------


class TestBuildTopologyDot:
    def _topology(self) -> dict[str, object]:
        return {
            "nodes": [
                {"name": "cartservice"},
                {"name": "redis"},
                {"name": "frontend"},
            ],
            "edges": [
                {"source": "redis", "target": "cartservice"},
                {"source": "cartservice", "target": "frontend"},
            ],
        }

    def test_basic(self) -> None:
        dot = build_topology_dot(self._topology())
        assert dot.startswith("digraph topology {")
        assert dot.endswith("}")
        assert '"cartservice"' in dot
        assert '"redis" -> "cartservice";' in dot
        assert "layout=circo" in dot

    def test_empty(self) -> None:
        dot = build_topology_dot({"nodes": [], "edges": []})
        assert "no data" in dot

    def test_highlight_thickens_border(self) -> None:
        dot = build_topology_dot(self._topology(), highlight="cartservice")
        # The cartservice line should have penwidth=3.
        lines = dot.splitlines()
        cartline = next(line for line in lines if '"cartservice"' in line and "fillcolor" in line)
        assert "penwidth=3" in cartline


# ---------------------------------------------------------------------------
# dashboard_helpers — alert payload builder
# ---------------------------------------------------------------------------


class TestBuildAlertPayload:
    def test_fields(self) -> None:
        now = datetime(2026, 4, 21, 0, 0, 0, tzinfo=UTC)
        payload = build_alert_payload("cartservice", "latency_p99", 500, 200, now=now)
        assert payload == {
            "service": "cartservice",
            "metric": "latency_p99",
            "value": 500.0,
            "threshold": 200.0,
            "timestamp": "2026-04-21T00:00:00+00:00",
        }

    def test_wraps_in_request(self) -> None:
        now = datetime(2026, 4, 21, 0, 0, 0, tzinfo=UTC)
        req = build_investigation_request(
            "cartservice",
            "latency_p99",
            500,
            200,
            time_range_minutes=45,
            now=now,
        )
        assert req["alert"]["service"] == "cartservice"
        assert req["time_range_minutes"] == 45


# ---------------------------------------------------------------------------
# Static constants
# ---------------------------------------------------------------------------


class TestConstants:
    def test_services_include_all_ob(self) -> None:
        for svc in ("cartservice", "frontend", "adservice", "emailservice"):
            assert svc in SERVICE_CHOICES

    def test_metrics_include_probe_signals(self) -> None:
        for metric in ("probe_up", "probe_latency", "memory_utilization"):
            assert metric in METRIC_CHOICES


# ---------------------------------------------------------------------------
# Guided-demo helpers
# ---------------------------------------------------------------------------


class TestBuildDemoInvestigationRequest:
    def test_returns_dict_with_service_field(self) -> None:
        body = build_demo_investigation_request("cartservice")
        assert body == {"service": "cartservice"}

    def test_all_six_demo_services_roundtrip(self) -> None:
        for svc in DEMO_SERVICE_CHOICES:
            body = build_demo_investigation_request(svc)
            assert body["service"] == svc


class TestFormatTop3RankedHtml:
    def test_three_predictions_render_as_ordered_list(self) -> None:
        html = format_top3_ranked_html(["cartservice", "redis", "checkoutservice"])
        assert html.startswith('<ol class="rank-list">')
        assert html.endswith("</ol>")
        assert "<li>cartservice</li>" in html
        assert "<li>redis</li>" in html
        assert "<li>checkoutservice</li>" in html

    def test_empty_list_renders_em_dash(self) -> None:
        """Used directly as the kpi_tile value slot; empty state must be
        a plain em-dash, not an empty <ol> (which would leave a gap)."""
        assert format_top3_ranked_html([]) == "—"

    def test_more_than_three_truncates_to_three(self) -> None:
        html = format_top3_ranked_html(
            ["a", "b", "c", "d", "e"]
        )
        assert html.count("<li>") == 3
        assert "<li>a</li>" in html
        assert "<li>c</li>" in html
        assert "<li>d</li>" not in html

    def test_preserves_input_order(self) -> None:
        html = format_top3_ranked_html(["z", "a", "m"])
        # CSS `::before counter(rank)` will prefix 1./2./3. at render time;
        # the HTML itself must keep the caller's order.
        assert html.index("<li>z</li>") < html.index("<li>a</li>") < html.index("<li>m</li>")


class TestExtractRootCauseIssue:
    _FULL_REPORT = (
        "═══════════════════════════════════════════════════════════════════\n"
        "                   ROOT CAUSE ANALYSIS REPORT\n"
        "═══════════════════════════════════════════════════════════════════\n"
        "\n"
        "INCIDENT : Anomaly Detected — Automated Investigation Triggered\n"
        "TIMESTAMP: 2026-04-22T23:38:18+00:00\n"
        "SEVERITY : high\n"
        "\n"
        "───────────────────────────────────────────────────────────────────\n"
        "EXECUTIVE SUMMARY\n"
        "───────────────────────────────────────────────────────────────────\n"
        "The cartservice experienced a critical failure and became "
        "unreachable, leading to a total service outage.\n"
        "\n"
        "───────────────────────────────────────────────────────────────────\n"
        "ROOT CAUSE  (Confidence: 75%)\n"
        "───────────────────────────────────────────────────────────────────\n"
        "Service  : cartservice\n"
        "Component: Container Runtime / Memory Management\n"
        "Issue    : cartservice container crashed or became unresponsive "
        "due to memory pressure, resulting in a complete service outage.\n"
    )

    def test_none_returns_empty(self) -> None:
        assert extract_root_cause_issue(None) == ""

    def test_empty_returns_empty(self) -> None:
        assert extract_root_cause_issue("") == ""

    def test_prefers_structured_issue_field(self) -> None:
        issue = extract_root_cause_issue(self._FULL_REPORT)
        assert issue.startswith("cartservice container crashed")
        # Must NOT return a line composed of box-drawing dividers.
        assert "═" not in issue
        assert "─" not in issue

    def test_falls_back_to_executive_summary(self) -> None:
        report_without_issue = (
            "═══════════════════════════════════════\n"
            "                   ROOT CAUSE ANALYSIS REPORT\n"
            "═══════════════════════════════════════\n"
            "\n"
            "EXECUTIVE SUMMARY\n"
            "───────────────────────────────────────\n"
            "The frontend is experiencing elevated 500 errors due to a "
            "latency spike.\n"
        )
        issue = extract_root_cause_issue(report_without_issue)
        assert issue.startswith("The frontend is experiencing")

    def test_skips_divider_lines(self) -> None:
        """Divider-only lines must never be returned as the issue."""
        report = (
            "═══════════════════\n"
            "───────────────────\n"
            "Issue    : real summary here\n"
        )
        assert extract_root_cause_issue(report) == "real summary here"

    def test_truncates_to_200_chars(self) -> None:
        long = "x" * 500
        report = f"Issue    : {long}\n"
        result = extract_root_cause_issue(report)
        assert len(result) <= 200


class TestDemoConstants:
    def test_six_services(self) -> None:
        assert len(DEMO_SERVICE_CHOICES) == 6
        assert "currencyservice" not in DEMO_SERVICE_CHOICES
        assert set(DEMO_SERVICE_CHOICES) == {
            "cartservice",
            "checkoutservice",
            "frontend",
            "paymentservice",
            "productcatalogservice",
            "redis",
        }

    def test_fault_label_for_every_service(self) -> None:
        assert set(DEMO_SERVICE_FAULT_LABELS.keys()) == set(DEMO_SERVICE_CHOICES)
        # Labels are human-readable (no snake_case leakage).
        for label in DEMO_SERVICE_FAULT_LABELS.values():
            assert "_" not in label
            assert label[0].isupper()

    def test_phase_labels_have_five_entries(self) -> None:
        assert len(DEMO_PHASE_LABELS) == 5
        assert DEMO_PHASE_LABELS[0] == "Injecting"
        assert DEMO_PHASE_LABELS[-1] == "Completed"

    def test_phase_to_int_mapping(self) -> None:
        assert DEMO_PHASE_TO_INT["queued"] == -1
        assert DEMO_PHASE_TO_INT["injecting"] == 0
        assert DEMO_PHASE_TO_INT["waiting"] == 1
        assert DEMO_PHASE_TO_INT["investigating"] == 2
        assert DEMO_PHASE_TO_INT["restoring"] == 3
        assert DEMO_PHASE_TO_INT["completed"] == len(DEMO_PHASE_LABELS)
        assert DEMO_PHASE_TO_INT["failed"] == -1
