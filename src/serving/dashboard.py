"""OpsAgent Streamlit dashboard.

Thin UI layer on top of the OpsAgent API. All non-trivial logic lives in
``src.serving.dashboard_helpers`` (pure, testable). This module is the
Streamlit entry point.

Run locally with::

    poetry run streamlit run src/serving/dashboard.py --server.port 8501

Respects ``OPSAGENT_API_BASE`` (default ``http://localhost:8000``) so the
same file works on the host machine and inside Docker Compose.
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from typing import Any

import requests
import streamlit as st

from src.serving.dashboard_helpers import (
    DEFAULT_API_BASE,
    DEMO_PHASE_LABELS,
    DEMO_PHASE_TO_INT,
    DEMO_SERVICE_CHOICES,
    DEMO_SERVICE_FAULT_LABELS,
    build_demo_investigation_request,
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
    inject_theme,
    investigation_card,
    kpi_tile,
    phase_stepper,
    root_cause_card,
    section_divider,
    status_pill,
)

# ---------------------------------------------------------------------------
# Page setup & theme
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="OpsAgent — Autonomous RCA Console",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

inject_theme()

API_BASE = os.environ.get("OPSAGENT_API_BASE", DEFAULT_API_BASE)


# ---------------------------------------------------------------------------
# API client helpers (thin wrappers; do not put retry/backoff logic here)
# ---------------------------------------------------------------------------


def api_get(path: str, timeout: float = 4.0, **params: Any) -> dict[str, Any] | list[Any] | None:
    try:
        r = requests.get(f"{API_BASE}{path}", params=params or None, timeout=timeout)
        r.raise_for_status()
        data: dict[str, Any] | list[Any] = r.json()
        return data
    except requests.RequestException:
        return None


def api_post(path: str, payload: dict[str, Any], timeout: float = 150.0) -> dict[str, Any] | None:
    try:
        r = requests.post(f"{API_BASE}{path}", json=payload, timeout=timeout)
        r.raise_for_status()
        data: dict[str, Any] = r.json()
        return data
    except requests.RequestException as exc:
        st.session_state["last_api_error"] = str(exc)
        return None


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------


with st.sidebar:
    st.markdown("# ⚡ OpsAgent")
    st.caption("Autonomous Root-Cause Analysis")
    page = st.radio(
        "Navigation",
        ["Overview", "Investigate", "History", "Metrics", "Settings"],
        index=0,
        label_visibility="collapsed",
    )
    st.markdown("---")
    st.caption(f"API: `{API_BASE}`")
    st.caption("v0.1.0 · Phase 6 demo")


# ---------------------------------------------------------------------------
# Shared: health probe (cached for 10 s to avoid hammering the API)
# ---------------------------------------------------------------------------


@st.cache_data(ttl=10)
def _fetch_health() -> dict[str, Any]:
    data = api_get("/health")
    if data is None:
        return {"status": "unreachable", "components": {}}
    return data  # type: ignore[return-value]


@st.cache_data(ttl=10)
def _fetch_topology() -> dict[str, Any]:
    data = api_get("/topology")
    if data is None:
        return {"nodes": [], "edges": []}
    return data  # type: ignore[return-value]


@st.cache_data(ttl=5)
def _fetch_recent_investigations(limit: int = 20) -> list[dict[str, Any]]:
    data = api_get("/investigations", limit=limit)
    if data is None:
        return []
    return data  # type: ignore[return-value]


def _render_hero(title: str, subtitle: str) -> None:
    """Render the top-of-page hero bar with live API status."""
    health = _fetch_health()
    label, severity = health_pill_for_overall(health.get("status", "unknown"))
    st.markdown(hero_bar(title, subtitle, label, severity), unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Page: Overview
# ---------------------------------------------------------------------------


def render_overview() -> None:
    _render_hero(
        "System Overview",
        "Service health, topology, and recent investigations at a glance.",
    )

    # --- Component-health grid ---
    health = _fetch_health()
    components = health.get("components", {})
    if components:
        cols = st.columns(len(components))
        for col, (name, status) in zip(cols, components.items(), strict=False):
            severity = component_pill_severity(str(status))
            col.markdown(
                f"<div style='text-align:center; padding:8px 0;'>"
                f"<div style='font-family:JetBrains Mono,monospace; "
                f"font-size:11px; color:{PALETTE.text_muted}; "
                f"text-transform:uppercase; letter-spacing:0.1em; "
                f"margin-bottom:8px;'>{name}</div>"
                f"{status_pill(str(status).upper(), severity)}"
                "</div>",
                unsafe_allow_html=True,
            )
    else:
        st.warning(
            f"OpsAgent API is not reachable. Is uvicorn running at `{API_BASE}`? (`make run`)"
        )

    # --- KPI tiles ---
    st.markdown(section_divider("Investigation Metrics"), unsafe_allow_html=True)
    investigations = _fetch_recent_investigations(limit=20)
    summary = summarize_investigations(investigations)
    kpi1, kpi2, kpi3 = st.columns(3)
    kpi1.markdown(
        kpi_tile(
            "Investigations run",
            f"{summary['total']}",
            f"{summary['completed']} completed",
        ),
        unsafe_allow_html=True,
    )
    kpi2.markdown(
        kpi_tile(
            "Avg confidence",
            format_confidence(summary["mean_confidence"]),
            "across completed runs",
        ),
        unsafe_allow_html=True,
    )
    kpi3.markdown(
        kpi_tile(
            "Avg investigation time",
            format_duration_seconds(summary["mean_duration_seconds"]),
            "end-to-end",
        ),
        unsafe_allow_html=True,
    )

    # --- Topology graph ---
    st.markdown(section_divider("Service Dependency Graph"), unsafe_allow_html=True)
    topology = _fetch_topology()
    dot = build_topology_dot(topology)
    st.graphviz_chart(dot, use_container_width=True)

    # --- Recent investigations card grid ---
    st.markdown(section_divider("Recent Investigations"), unsafe_allow_html=True)
    if not investigations:
        st.markdown(
            f"<div style='color:{PALETTE.text_muted}; font-size:13px;'>"
            "No investigations yet. Head to the <b>Investigate</b> page to run one."
            "</div>",
            unsafe_allow_html=True,
        )
    else:
        card_cols = st.columns(min(3, len(investigations[:6])))
        for i, inv in enumerate(investigations[:6]):
            col = card_cols[i % len(card_cols)]
            col.markdown(investigation_card(inv), unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Page: Investigate
# ---------------------------------------------------------------------------


def render_investigate() -> None:
    """Guided demo investigation page.

    Replaces the alert-schema form from Session 17 with a service picker.
    The user picks one of the six services with a canonical Session-13
    fault scenario; the API runs the full inject → wait → investigate →
    restore lifecycle asynchronously; the dashboard polls status every
    2 seconds to drive a real phase stepper.
    """
    _render_hero(
        "Guided Demo Investigation",
        "Pick a service. OpsAgent injects its canonical fault, waits for "
        "the anomaly to manifest, investigates, and restores — end-to-end.",
    )

    inv_id = st.session_state.get("demo_inv_id")
    if inv_id:
        _render_demo_in_progress(inv_id)
    else:
        _render_demo_picker()


def _render_demo_picker() -> None:
    """Service-picker UI shown when no demo is in flight."""
    picker_col, preview_col = st.columns([1, 1])

    with picker_col:
        st.markdown(
            f"<div style='font-family:JetBrains Mono,monospace; "
            f"font-size:11px; color:{PALETTE.text_muted}; "
            f"text-transform:uppercase; letter-spacing:0.1em; "
            f"margin-bottom:12px;'>Pick a service to demo</div>",
            unsafe_allow_html=True,
        )
        selected = st.radio(
            "Pick a service to demo",
            options=DEMO_SERVICE_CHOICES,
            format_func=lambda s: f"{s}  —  {DEMO_SERVICE_FAULT_LABELS[s]}",
            key="demo_selected_service",
            label_visibility="collapsed",
        )
        st.caption(
            "Typical run time ~3 min: 120 s for the anomaly to manifest, "
            "~25 s investigation, ~15 s restore."
        )
        start_clicked = st.button(
            "▶ Start demo investigation",
            type="primary",
            use_container_width=True,
        )

    with preview_col:
        st.markdown(
            f"<div style='font-family:JetBrains Mono,monospace; "
            f"font-size:11px; color:{PALETTE.text_muted}; "
            f"text-transform:uppercase; letter-spacing:0.1em; "
            f"margin-bottom:12px;'>Topology preview — selected service highlighted</div>",
            unsafe_allow_html=True,
        )
        topology = _fetch_topology()
        dot = build_topology_dot(topology, highlight=selected)
        st.graphviz_chart(dot, use_container_width=True)

    if start_clicked:
        _start_demo(selected)


def _start_demo(service: str) -> None:
    """POST /demo/investigate, handle 409, stash state, and rerun for polling."""
    body = build_demo_investigation_request(service)
    try:
        r = requests.post(
            f"{API_BASE}/demo/investigate",
            json=body,
            timeout=10,
        )
    except requests.RequestException as exc:
        st.error(f"Failed to reach API: {exc}")
        return

    if r.status_code == 409:
        st.warning(
            "A demo is already running on this server. Please wait for it "
            "to complete (~3 min) before starting another."
        )
        return
    if not r.ok:
        st.error(f"Demo request failed: HTTP {r.status_code} — {r.text[:200]}")
        return

    data = r.json()
    inv_id = data.get("investigation_id")
    if not inv_id:
        st.error("Demo response missing investigation_id.")
        return

    st.session_state["demo_inv_id"] = inv_id
    st.session_state["demo_service"] = data.get("service", service)
    st.session_state["demo_fault_type"] = data.get("fault_type", "")
    st.session_state["demo_ground_truth"] = data.get("ground_truth", "")
    st.session_state["demo_started_at"] = datetime.now(UTC).isoformat()
    st.rerun()


def _render_demo_in_progress(inv_id: str) -> None:
    """Polling UI shown while a demo is in flight, and the final result card."""
    status = api_get(f"/demo/investigations/{inv_id}/status", timeout=3.0)
    if status is None or not isinstance(status, dict):
        # Transient error — brief pause + rerun so we don't spin.
        st.info("Waiting for API status…")
        time.sleep(2)
        st.rerun()
        return

    phase = status.get("phase", "queued")
    service = status.get("service", st.session_state.get("demo_service", ""))
    fault_type = status.get("fault_type", st.session_state.get("demo_fault_type", ""))
    active_phase_int = DEMO_PHASE_TO_INT.get(phase, -1)

    st.markdown(
        phase_stepper(active_phase_int, labels=DEMO_PHASE_LABELS),
        unsafe_allow_html=True,
    )

    elapsed = 0.0
    started_at_str = st.session_state.get("demo_started_at")
    if started_at_str:
        try:
            elapsed = (datetime.now(UTC) - datetime.fromisoformat(started_at_str)).total_seconds()
        except ValueError:
            elapsed = 0.0

    col1, col2, col3 = st.columns(3)
    col1.markdown(kpi_tile("Service", service or "—"), unsafe_allow_html=True)
    col2.markdown(
        kpi_tile("Fault scenario", fault_type.replace("_", " ") if fault_type else "—"),
        unsafe_allow_html=True,
    )
    col3.markdown(
        kpi_tile("Elapsed", format_duration_seconds(elapsed) if elapsed > 0 else "—"),
        unsafe_allow_html=True,
    )

    # Terminal states: completed / failed
    if phase in ("completed", "failed"):
        if phase == "failed":
            err = status.get("error") or "unknown error"
            st.error(f"Demo investigation failed: {err}")
        result = status.get("result")
        if isinstance(result, dict):
            _render_investigation_result(result, elapsed)
        if st.button("▶ Start a new demo", type="primary"):
            for key in (
                "demo_inv_id",
                "demo_service",
                "demo_fault_type",
                "demo_ground_truth",
                "demo_started_at",
            ):
                st.session_state.pop(key, None)
            st.rerun()
        return

    # In-flight phase (queued / injecting / waiting / investigating / restoring).
    # Poll again in 2 s.
    time.sleep(2)
    st.rerun()


def _render_investigation_result(response: dict[str, Any], elapsed: float) -> None:
    """Shared renderer used by the Investigate page (new runs) and the
    History page (saved runs).
    """
    status = response.get("status", "unknown")
    if status == "failed":
        st.error(f"Investigation failed: {response.get('report', 'unknown error')}")
        return

    root_cause = response.get("root_cause") or {}
    st.markdown(section_divider("Root Cause"), unsafe_allow_html=True)
    if root_cause.get("service"):
        st.markdown(
            root_cause_card(
                service=str(root_cause.get("service", "unknown")),
                issue=extract_root_cause_issue(response.get("report")),
                confidence=float(root_cause.get("confidence", 0.0) or 0.0),
            ),
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f"<div style='color:{PALETTE.text_muted};'>No root cause identified.</div>",
            unsafe_allow_html=True,
        )

    meta_col1, meta_col2, meta_col3 = st.columns(3)
    meta_col1.markdown(
        kpi_tile("Investigation ID", response.get("investigation_id", "—")),
        unsafe_allow_html=True,
    )
    meta_col2.markdown(
        kpi_tile(
            "Duration",
            format_duration_seconds(response.get("duration_seconds", elapsed)),
        ),
        unsafe_allow_html=True,
    )
    meta_col3.markdown(
        kpi_tile(
            "Top 3 candidates",
            format_top3_ranked_html(response.get("top_3_predictions", []) or []),
        ),
        unsafe_allow_html=True,
    )

    # Evidence timeline
    evidence = parse_evidence_from_report(response.get("report"))
    if evidence:
        st.markdown(section_divider("Evidence Timeline"), unsafe_allow_html=True)
        st.markdown(evidence_timeline(evidence), unsafe_allow_html=True)

    # Recommended actions
    recs = response.get("recommendations", [])
    if recs:
        st.markdown(section_divider("Recommended Actions"), unsafe_allow_html=True)
        st.markdown(
            "<ol style='font-size:14px; line-height:1.7;'>"
            + "".join(f"<li>{r}</li>" for r in recs)
            + "</ol>",
            unsafe_allow_html=True,
        )

    # Full report
    report = response.get("report") or "(no report)"
    st.markdown(section_divider("Full RCA Report"), unsafe_allow_html=True)
    st.markdown(
        f'<div class="rca-report">{report}</div>',
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Page: History
# ---------------------------------------------------------------------------


def render_history() -> None:
    _render_hero(
        "Investigation History",
        "All investigations since the API last started (in-memory, capped at 100).",
    )

    investigations = _fetch_recent_investigations(limit=100)
    if not investigations:
        st.info("No investigations yet.")
        return

    # Render as a structured table using st.dataframe for sort/filter UX.
    import pandas as pd

    rows = []
    for inv in investigations:
        rc = inv.get("root_cause") or {}
        rows.append(
            {
                "ID": inv.get("investigation_id"),
                "Status": inv.get("status"),
                "Root cause": rc.get("service", "—"),
                "Confidence": float(rc.get("confidence", 0.0)),
                "Duration (s)": float(inv.get("duration_seconds", 0.0)),
                "Started": inv.get("started_at", ""),
            }
        )
    df = pd.DataFrame(rows)
    st.dataframe(
        df,
        hide_index=True,
        use_container_width=True,
        column_config={
            "Confidence": st.column_config.ProgressColumn(
                "Confidence",
                # `"percent"` preset multiplies the raw 0-1 value by 100 and
                # appends `%`. A printf format like `%.0f%%` would render
                # 0.75 as "1%" (no multiplier) — the bug we had before.
                format="percent",
                min_value=0.0,
                max_value=1.0,
            ),
            "Duration (s)": st.column_config.NumberColumn(format="%.1f"),
        },
    )

    # Detailed view
    ids = [i.get("investigation_id", "") for i in investigations]
    selected = st.selectbox("View full report", ids, index=0)
    if selected:
        selected_resp = next(
            (i for i in investigations if i.get("investigation_id") == selected),
            None,
        )
        if selected_resp:
            _render_investigation_result(selected_resp, elapsed=0.0)


# ---------------------------------------------------------------------------
# Page: Metrics
# ---------------------------------------------------------------------------


def render_metrics() -> None:
    _render_hero(
        "Real-Time Metrics",
        "Live Grafana dashboard — Service Overview (CPU, memory, network, probes).",
    )

    grafana_base = os.environ.get("GRAFANA_URL", "http://localhost:3000")
    grafana_url = f"{grafana_base}/d/service_overview?orgId=1&kiosk=tv&refresh=15s"

    st.markdown(
        f"<div style='color:{PALETTE.text_muted}; font-size:13px; margin-bottom:12px;'>"
        f"Embedded from <code>{grafana_url}</code>. "
        f"If the panel is blank, open Grafana directly and confirm the "
        f"<code>service_overview</code> dashboard exists."
        "</div>",
        unsafe_allow_html=True,
    )
    st.components.v1.iframe(grafana_url, height=820, scrolling=True)


# ---------------------------------------------------------------------------
# Page: Settings
# ---------------------------------------------------------------------------


def render_settings() -> None:
    _render_hero(
        "Settings",
        "Configuration snapshot. Edits require a restart of the API for now.",
    )

    st.markdown(section_divider("API Configuration"), unsafe_allow_html=True)
    st.markdown(
        f"<div style='font-family: JetBrains Mono, monospace; font-size: 13px; "
        f"line-height: 1.9; color: {PALETTE.text};'>"
        f"<div><span style='color:{PALETTE.text_muted};'>API base URL:</span> {API_BASE}</div>"
        f"<div><span style='color:{PALETTE.text_muted};'>Agent config:</span> "
        f"configs/agent_config.yaml</div>"
        f"<div><span style='color:{PALETTE.text_muted};'>LLM model:</span> "
        f"gemini-3-flash-preview (live)</div>"
        f"<div><span style='color:{PALETTE.text_muted};'>Max tool calls per investigation:</span> "
        f"10 (+36 deterministic sweep calls)</div>"
        f"<div><span style='color:{PALETTE.text_muted};'>Confidence threshold:</span> 0.70</div>"
        "</div>",
        unsafe_allow_html=True,
    )

    st.markdown(section_divider("Agent Phases"), unsafe_allow_html=True)
    st.markdown(
        f"<div style='color:{PALETTE.text_muted}; font-size:13px; line-height:1.7;'>"
        "On each investigation the LangGraph runs through five phases — "
        f"<code style='color:{PALETTE.primary};'>{' → '.join(PHASE_LABELS)}</code>. "
        "The dashboard stepper reflects this progression."
        "</div>",
        unsafe_allow_html=True,
    )

    st.markdown(section_divider("Notes"), unsafe_allow_html=True)
    st.markdown(
        f"<div style='color:{PALETTE.text_muted}; font-size:13px; line-height:1.7;'>"
        "Dynamic reload of agent configuration is a Week-12+ stretch goal. "
        "For now, edit <code>configs/agent_config.yaml</code> and restart the API "
        "(<code>make run</code>) for changes to take effect."
        "</div>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


PAGES = {
    "Overview": render_overview,
    "Investigate": render_investigate,
    "History": render_history,
    "Metrics": render_metrics,
    "Settings": render_settings,
}


PAGES[page]()
