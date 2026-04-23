"""Pure helpers used by the OpsAgent Streamlit dashboard.

Kept separate from ``dashboard.py`` so they can be unit-tested without
spinning up a Streamlit scriptrunner. None of these functions touch
``streamlit``; they take plain data and return plain data (or Plotly
figures). ``dashboard.py`` is the thin UI layer that wires them up.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

from src.serving.theme import PALETTE

DEFAULT_API_BASE = os.environ.get("OPSAGENT_API_BASE", "http://localhost:8000")

# Ordered list shown in the Investigate form's service picker. The order
# mirrors the Session 13 ground-truth distribution (the services users are
# most likely to investigate appear first).
SERVICE_CHOICES: list[str] = [
    "cartservice",
    "checkoutservice",
    "frontend",
    "paymentservice",
    "productcatalogservice",
    "redis",
    "currencyservice",
    "adservice",
    "emailservice",
    "recommendationservice",
    "shippingservice",
]

METRIC_CHOICES: list[str] = [
    "latency_p99",
    "error_rate",
    "cpu_usage",
    "memory_utilization",
    "memory_usage",
    "probe_up",
    "probe_latency",
]


# ---------------------------------------------------------------------------
# Guided-demo picker constants (used by the Investigate-page redesign)
# ---------------------------------------------------------------------------


# Ordered list shown in the guided-demo service picker. Each service has a
# canonical fault mapping in the API; the dashboard only picks the service.
DEMO_SERVICE_CHOICES: list[str] = [
    "cartservice",
    "checkoutservice",
    "frontend",
    "paymentservice",
    "productcatalogservice",
    "redis",
]

# Human-readable labels shown on each picker card.
DEMO_SERVICE_FAULT_LABELS: dict[str, str] = {
    "cartservice": "Service Crash",
    "checkoutservice": "Memory Pressure",
    "frontend": "High Latency",
    "paymentservice": "Network Partition",
    "productcatalogservice": "Config Error",
    "redis": "Connection Exhaustion",
}

# Labels shown in the demo-phase stepper (passed to ``theme.phase_stepper``).
DEMO_PHASE_LABELS: list[str] = [
    "Injecting",
    "Waiting",
    "Investigating",
    "Restoring",
    "Completed",
]

# Map the API's ``DemoPhase`` literal to the 0-indexed stepper position.
# ``queued`` and ``failed`` are terminal or pre-active states; we render
# ``queued`` as "nothing active yet" (-1) and ``failed`` with no phase
# highlighted. ``completed`` uses the length of the label list so every
# step renders as done.
DEMO_PHASE_TO_INT: dict[str, int] = {
    "queued": -1,
    "injecting": 0,
    "waiting": 1,
    "investigating": 2,
    "restoring": 3,
    "completed": len(DEMO_PHASE_LABELS),
    "failed": -1,
}


# ---------------------------------------------------------------------------
# Health-response helpers
# ---------------------------------------------------------------------------


def health_pill_for_overall(status: str) -> tuple[str, str]:
    """Return ``(label, severity)`` for the main hero-bar status pill."""
    if status == "healthy":
        return ("API HEALTHY", "success")
    if status == "degraded":
        return ("API DEGRADED", "warn")
    return ("API UNREACHABLE", "error")


def component_pill_severity(component_status: str) -> str:
    """Map a per-dependency status to a pill severity name."""
    if component_status in ("connected", "available"):
        return "success"
    if component_status == "unreachable":
        return "error"
    if component_status == "unconfigured":
        return "warn"
    return "muted"


# ---------------------------------------------------------------------------
# Investigation-response helpers
# ---------------------------------------------------------------------------


def summarize_investigations(investigations: list[dict[str, Any]]) -> dict[str, Any]:
    """Produce the 3-KPI summary shown on the Overview page.

    Returns ``{total, completed, mean_confidence, mean_duration_seconds}``.
    Resilient to empty lists and partial/failed investigations.
    """
    total = len(investigations)
    completed = [i for i in investigations if i.get("status") == "completed"]
    completed_count = len(completed)

    confidences = [
        (i.get("root_cause") or {}).get("confidence", 0.0) for i in completed if i.get("root_cause")
    ]
    durations = [i.get("duration_seconds", 0.0) for i in completed if i.get("duration_seconds")]

    return {
        "total": total,
        "completed": completed_count,
        "mean_confidence": (sum(confidences) / len(confidences)) if confidences else 0.0,
        "mean_duration_seconds": (sum(durations) / len(durations)) if durations else 0.0,
    }


def format_duration_seconds(seconds: float) -> str:
    """Human-friendly formatter used by the Overview KPI tile."""
    if seconds <= 0:
        return "—"
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    remainder = seconds - (minutes * 60)
    return f"{minutes}m {remainder:.0f}s"


def format_confidence(confidence: float) -> str:
    """Human-friendly percent formatter."""
    return f"{confidence * 100:.0f}%"


def format_top3_ranked_html(predictions: list[str]) -> str:
    """Render the top-3 prediction list as a compact numbered HTML list.

    Returns an empty-state em-dash when ``predictions`` is empty so the
    caller can pass the result straight into ``kpi_tile(..., value=...)``
    without worrying about the empty case.
    """
    if not predictions:
        return "—"
    items = "".join(f"<li>{p}</li>" for p in predictions[:3])
    return f'<ol class="rank-list">{items}</ol>'


def extract_root_cause_issue(report: str | None) -> str:
    """Return a short, human-readable issue summary from an RCA report.

    The Session-13 RCA report template opens with a box-drawing header
    (``═══...``) so naively taking the first line renders as a literal
    double underline in the root-cause card. This helper prefers the
    structured ``Issue    :`` field emitted by the agent; falls back to
    the first line of the ``EXECUTIVE SUMMARY`` section; falls back to
    an empty string when neither is present (the card then hides the
    issue slot entirely).
    """
    if not report:
        return ""

    # Preferred: structured "Issue    : <text>" field.
    for line in report.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("issue") and ":" in stripped:
            after_colon = stripped.split(":", 1)[1].strip()
            if after_colon:
                return after_colon[:200]

    # Fallback: first non-empty, non-divider line inside EXECUTIVE SUMMARY.
    lines = report.splitlines()
    for i, raw in enumerate(lines):
        if "EXECUTIVE SUMMARY" in raw:
            for candidate in lines[i + 1 :]:
                cleaned = candidate.strip()
                if not cleaned or _is_divider_line(cleaned):
                    continue
                return cleaned[:200]
            break

    return ""


_DIVIDER_CHARS = frozenset("═─=-─━│┃")


def _is_divider_line(line: str) -> bool:
    """True when the line is composed entirely of box-drawing / divider chars."""
    stripped = line.strip()
    if not stripped:
        return False
    return all(ch in _DIVIDER_CHARS for ch in stripped)


def parse_evidence_from_report(report: str | None) -> list[dict[str, str]]:
    """Extract the EVIDENCE CHAIN section of an RCA report into
    ``{time, text, severity}`` entries for the timeline renderer.

    Falls back to an empty list when the section is absent or
    unparseable — the dashboard still renders the raw report below the
    timeline, so losing the timeline degrades gracefully.
    """
    if not report:
        return []

    lines = report.splitlines()
    in_section = False
    entries: list[dict[str, str]] = []
    for raw in lines:
        line = raw.strip()
        if "EVIDENCE CHAIN" in line:
            in_section = True
            continue
        if not in_section:
            continue
        if line.startswith(("─", "═", "---", "===")) or line.startswith("##"):
            # Leaving the section (next heading / divider)
            if line.startswith(("─", "═")) and entries:
                # Divider terminator -- stop collecting.
                break
            continue
        if not line or line.startswith(("CAUSAL ANALYSIS", "RECOMMENDED ACTIONS")):
            break

        # Entries in Session-13 reports look like:
        #   "- **03:57:42 UTC**: cartservice memory utilization ..."
        # or:
        #   "* **2026-04-19T03:34:39**: ..."
        stripped = line.lstrip("-*").strip()
        time_part = ""
        text_part = stripped
        if "**" in stripped:
            # Extract the first **...** block as the timestamp.
            first_open = stripped.find("**")
            first_close = stripped.find("**", first_open + 2)
            if first_close > first_open:
                time_part = stripped[first_open + 2 : first_close].strip()
                rest = stripped[first_close + 2 :].lstrip(":").strip()
                text_part = rest or stripped
        severity = _infer_severity(text_part)
        entries.append({"time": time_part, "text": text_part, "severity": severity})

    return entries


_SEVERITY_PATTERNS: tuple[tuple[str, str], ...] = (
    ("CRITICAL", "critical"),
    ("probe_up CRITICAL", "critical"),
    ("probe_up drops to 0", "critical"),
    ("probe_up=0", "critical"),
    ("sparse", "warn"),
    ("stale", "warn"),
    ("OOMKill", "critical"),
    ("SIGSEGV", "critical"),
    ("panic", "critical"),
    ("500 error", "warn"),
    ("ECONNREFUSED", "warn"),
    ("unhandled promise", "warn"),
)


def _infer_severity(text: str) -> str:
    """Infer a severity tag from free-text evidence. Defaults to ``info``."""
    lowered = text.lower()
    for marker, severity in _SEVERITY_PATTERNS:
        if marker.lower() in lowered:
            return severity
    return "info"


# ---------------------------------------------------------------------------
# Topology DOT graph (Graphviz)
# ---------------------------------------------------------------------------


def _topology_node_color(name: str) -> str:
    """Role-based node color for the topology graph."""
    if name == "redis":
        return PALETTE.warn
    if name == "frontend":
        return PALETTE.primary
    if name in ("cartservice", "checkoutservice", "paymentservice"):
        return "#A78BFA"  # violet
    if name == "productcatalogservice":
        return "#2DD4BF"  # teal
    if name in ("adservice", "recommendationservice", "emailservice", "shippingservice"):
        return "#F472B6"  # pink (Online Boutique extensions)
    return PALETTE.text_muted  # fallback


# Human-readable labels for the dependency-graph nodes. Graphviz's ``\n``
# inside a quoted label string renders each chunk on its own line and
# centre-aligns them in the circle. Services not listed here fall back
# to their raw tag (e.g. future additions render literally until mapped).
_TOPOLOGY_LABELS: dict[str, str] = {
    "cartservice": "Cart\\nService",
    "checkoutservice": "Checkout\\nService",
    "currencyservice": "Currency\\nService",
    "paymentservice": "Payment\\nService",
    "productcatalogservice": "Product Catalog\\nService",
    "recommendationservice": "Recommendation\\nService",
    "emailservice": "Email\\nService",
    "shippingservice": "Shipping\\nService",
    "adservice": "Ad\\nService",
    "frontend": "Frontend",
    "redis": "Redis",
}


def _topology_label(name: str) -> str:
    """Return the display-friendly, circle-aligned label for a service."""
    return _TOPOLOGY_LABELS.get(name, name)


def build_topology_dot(topology: dict[str, Any], highlight: str | None = None) -> str:
    """Render the service-dependency graph as a Graphviz DOT string.

    Consumed by ``st.graphviz_chart(dot_string, use_container_width=True)``.
    Uses a circular layout (``layout=circo``) so the render is deterministic
    across refreshes and doesn't re-layout when data hasn't changed.

    ``highlight`` optionally names a service whose node should be rendered
    with a brighter border (used to call out the currently-selected service
    on the Investigate page).
    """
    nodes = topology.get("nodes", [])
    edges = topology.get("edges", [])

    # ``width=1.35`` gives 14-char lines ("Recommendation", "Product Catalog")
    # enough room at fontsize 10 without spilling past the circle rim. ``nojustify``
    # keeps multi-line labels centred inside the circle rather than left-aligned.
    lines: list[str] = [
        "digraph topology {",
        "  layout=circo;",
        '  bgcolor="transparent";',
        "  overlap=false;",
        "  splines=true;",
        '  fontname="JetBrains Mono";',
        '  node [shape=circle, style="filled,bold", fontname="JetBrains Mono", '
        'fontsize=10, fontcolor="#0B0E14", width=1.35, fixedsize=true, labelloc=c, '
        "nojustify=false];",
        f'  edge [color="{PALETTE.text_dim}", arrowsize=0.7, penwidth=1.0];',
    ]

    if not nodes:
        empty_color = PALETTE.text_muted
        lines.append(f'  empty [label="no data", shape=plaintext, fontcolor="{empty_color}"];')
        lines.append("}")
        return "\n".join(lines)

    for node in nodes:
        name = node["name"]
        label = _topology_label(name)
        color = _topology_node_color(name)
        border_color = PALETTE.primary if name == highlight else PALETTE.bg
        border_width = 3 if name == highlight else 1
        lines.append(
            f'  "{name}" [label="{label}", fillcolor="{color}", '
            f'color="{border_color}", penwidth={border_width}];'
        )

    for edge in edges:
        src = edge["source"]
        dst = edge["target"]
        lines.append(f'  "{src}" -> "{dst}";')

    lines.append("}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# API-response normalization
# ---------------------------------------------------------------------------


def build_alert_payload(
    service: str,
    metric: str,
    value: float,
    threshold: float,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build the JSON body for ``POST /investigate``.

    Mirrors ``schemas.AlertPayload``. ``now`` is injectable for tests.
    """
    ts = (now or datetime.now(UTC)).isoformat()
    return {
        "service": service,
        "metric": metric,
        "value": float(value),
        "threshold": float(threshold),
        "timestamp": ts,
    }


def build_investigation_request(
    service: str,
    metric: str,
    value: float,
    threshold: float,
    time_range_minutes: int = 30,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Wrap the alert in the outer ``InvestigationRequest`` body."""
    return {
        "alert": build_alert_payload(service, metric, value, threshold, now=now),
        "time_range_minutes": int(time_range_minutes),
    }


def build_demo_investigation_request(service: str) -> dict[str, str]:
    """Build the JSON body for ``POST /demo/investigate``.

    The endpoint maps the service to its canonical Session-13 fault
    scenario server-side, so the dashboard only needs to pass the
    service name.
    """
    return {"service": service}


__all__ = [
    "DEFAULT_API_BASE",
    "DEMO_PHASE_LABELS",
    "DEMO_PHASE_TO_INT",
    "DEMO_SERVICE_CHOICES",
    "DEMO_SERVICE_FAULT_LABELS",
    "METRIC_CHOICES",
    "SERVICE_CHOICES",
    "build_alert_payload",
    "build_demo_investigation_request",
    "build_investigation_request",
    "build_topology_dot",
    "component_pill_severity",
    "extract_root_cause_issue",
    "format_confidence",
    "format_duration_seconds",
    "format_top3_ranked_html",
    "health_pill_for_overall",
    "parse_evidence_from_report",
    "summarize_investigations",
]
