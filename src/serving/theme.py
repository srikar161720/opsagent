"""Design system for the OpsAgent Streamlit dashboard.

Palette + typography synthesized from a ``ui-ux-pro-max`` design consultation:
base style = ``Dark Mode (OLED)`` crossed with ``Real-Time Monitoring`` semantic
colors, typography pair = ``Developer Mono`` (JetBrains Mono + IBM Plex Sans).
The goal is a calm, engineer-trusted observability console that doesn't read
as generic "AI dashboard" chrome.

This module provides three things:

1. Palette + spacing constants used by both the custom CSS and Plotly charts.
2. ``CSS_OVERRIDES`` — a single CSS string injected once at dashboard
   startup via ``st.markdown(..., unsafe_allow_html=True)``.
3. Small HTML builders (``status_pill``, ``kpi_tile``, ``root_cause_card``,
   ``phase_stepper``, ``evidence_timeline``) that return snippets of HTML
   for embedding in Streamlit pages. Kept pure so unit tests can exercise
   them without a running Streamlit scriptrunner.

Usage::

    import streamlit as st
    from src.serving.theme import CSS_OVERRIDES, inject_theme, kpi_tile

    inject_theme()  # once, at the top of dashboard.py
    st.markdown(kpi_tile("Investigations run", "35", "↗ +5 past hour"),
                unsafe_allow_html=True)
"""

from __future__ import annotations

from dataclasses import dataclass

import streamlit as st  # noqa: I001

# ---------------------------------------------------------------------------
# Palette (hex values — keep in sync with .streamlit/config.toml)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Palette:
    primary: str = "#60A5FA"  # sky-blue accent
    bg: str = "#0B0E14"  # deep near-black, cool tint
    bg_elevated: str = "#151A23"  # card / elevated surface
    bg_elevated_2: str = "#1C2230"  # nested surface, sliders, hover
    text: str = "#E4E7EB"  # body text (off-white)
    text_muted: str = "#94A3B8"  # muted labels, captions
    text_dim: str = "#64748B"  # timestamps, metadata
    border: str = "rgba(148, 163, 184, 0.12)"
    border_strong: str = "rgba(148, 163, 184, 0.22)"

    # Semantic (Real-Time Monitoring convention)
    success: str = "#22C55E"
    warn: str = "#F59E0B"
    error: str = "#EF4444"
    critical: str = "#DC2626"  # deeper red for CRITICAL signals


PALETTE = Palette()


# Plotly colorway for the topology graph and any future charts.
PLOTLY_COLORWAY = [
    "#60A5FA",  # primary
    "#22C55E",  # success
    "#F59E0B",  # warn
    "#EF4444",  # error
    "#A78BFA",  # violet-400
    "#2DD4BF",  # teal-400
    "#FB923C",  # orange-400
    "#F472B6",  # pink-400
]


# ---------------------------------------------------------------------------
# CSS overrides
# ---------------------------------------------------------------------------

# Streamlit traps this overrides:
# - Default ``st.metric`` widget is too generic; we build custom KPI tiles.
# - ``st.code()`` renders with a light code-block style that clashes with dark
#   theme; we use ``st.markdown()`` for RCA reports and style ``.rca-report``.
# - ``st.success`` / ``st.error`` / ``st.info`` / ``st.warning`` use hardcoded
#   pastel backgrounds; we build status pills via CSS instead.
CSS_OVERRIDES = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700&family=IBM+Plex+Sans:wght@300;400;500;600;700&display=swap');

html, body {{
    font-family: 'IBM Plex Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
}}

/* Do not force a global descendant font on Streamlit content.
   Streamlit sidebar controls render icon ligatures in internal spans;
   broad inheritance can override the icon font and show raw token text. */

h1, h2, h3, h4, h5, h6,
[data-testid="stHeading"] * {{
    font-family: 'JetBrains Mono', 'SF Mono', Menlo, monospace !important;
    font-weight: 600;
    letter-spacing: -0.01em;
}}

code, pre, .stCode, .mono {{
    font-family: 'JetBrains Mono', 'SF Mono', Menlo, monospace !important;
}}

/* Explicitly preserve icon ligatures for Streamlit controls and known
    Material Symbols classes across Streamlit versions. */
.material-symbols-rounded,
.material-symbols-outlined,
.material-symbols-sharp,
[class*="material-symbols"],
[class*="material-icons"],
[class*="material-icon"],
[data-testid="collapsedControl"] span,
[data-testid="stSidebarCollapseButton"] span,
[data-testid="stSidebarCollapsedControl"] span,
button[aria-label*="sidebar"] span {{
    font-family: 'Material Symbols Rounded', 'Material Symbols Outlined',
                 'Material Symbols Sharp' !important;
     font-weight: normal !important;
     font-style: normal !important;
     letter-spacing: normal !important;
     text-transform: none !important;
     white-space: nowrap !important;
     font-feature-settings: 'liga' 1 !important;
     -webkit-font-feature-settings: 'liga' 1 !important;
}}

/* --- Sidebar polish ------------------------------------------------------- */
[data-testid="stSidebar"] {{
    background-color: {PALETTE.bg_elevated};
    border-right: 1px solid {PALETTE.border};
}}
[data-testid="stSidebar"] h1 {{
    font-size: 20px !important;
    color: {PALETTE.primary};
    margin-bottom: 4px !important;
}}
[data-testid="stSidebar"] p,
[data-testid="stSidebar"] label {{
    color: {PALETTE.text_muted} !important;
    font-size: 13px !important;
}}

/* --- Top-of-page hero bar ------------------------------------------------- */
/* ``100vw`` + centring trick stretches the bar to the full viewport width
   regardless of how much horizontal padding Streamlit's ``block-container``
   currently imposes (varies by breakpoint). */
.ops-hero {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 20px 40px;
    position: relative;
    width: 100vw;
    left: 50%;
    right: 50%;
    margin-left: -50vw;
    margin-right: -50vw;
    margin-top: -24px;
    margin-bottom: 24px;
    background: linear-gradient(
        90deg,
        {PALETTE.bg_elevated} 0%,
        {PALETTE.bg_elevated_2} 100%
    );
    border-bottom: 1px solid {PALETTE.border};
    box-sizing: border-box;
}}
.ops-hero__title {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 22px;
    font-weight: 600;
    color: {PALETTE.text};
    margin: 0;
    letter-spacing: -0.01em;
}}
.ops-hero__subtitle {{
    font-size: 13px;
    color: {PALETTE.text_muted};
    margin: 4px 0 0 0;
    font-weight: 400;
}}

/* --- Status pills --------------------------------------------------------- */
.status-pill {{
    display: inline-flex;
    align-items: center;
    gap: 8px;
    padding: 4px 12px;
    border-radius: 999px;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    font-family: 'JetBrains Mono', monospace;
    white-space: nowrap;
}}
.status-pill::before {{
    content: '';
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: currentColor;
}}
.status-pill--success {{ color: {PALETTE.success}; background: rgba(34, 197, 94, 0.12); }}
.status-pill--warn    {{ color: {PALETTE.warn};    background: rgba(245, 158, 11, 0.12); }}
.status-pill--error   {{ color: {PALETTE.error};   background: rgba(239, 68, 68, 0.12); }}
.status-pill--critical{{ color: {PALETTE.critical};background: rgba(220, 38, 38, 0.15); }}
.status-pill--info    {{ color: {PALETTE.primary}; background: rgba(96, 165, 250, 0.12); }}
.status-pill--muted   {{ color: {PALETTE.text_dim};background: rgba(100, 116, 139, 0.12); }}

/* --- KPI tile ------------------------------------------------------------- */
.kpi-tile {{
    padding: 20px;
    background: {PALETTE.bg_elevated};
    border: 1px solid {PALETTE.border};
    border-radius: 12px;
    display: flex;
    flex-direction: column;
    gap: 10px;
    min-height: 112px;
    transition: border-color 180ms ease;
}}
.kpi-tile:hover {{
    border-color: {PALETTE.border_strong};
}}
.kpi-tile__label {{
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: {PALETTE.text_muted};
    font-family: 'JetBrains Mono', monospace;
}}
.kpi-tile__value {{
    font-size: 32px;
    font-weight: 500;
    font-family: 'JetBrains Mono', monospace;
    color: {PALETTE.text};
    line-height: 1.1;
}}
.kpi-tile__trend {{
    font-size: 12px;
    font-weight: 500;
    color: {PALETTE.text_muted};
}}
.kpi-tile__trend--up   {{ color: {PALETTE.success}; }}
.kpi-tile__trend--down {{ color: {PALETTE.error}; }}

/* Ranked list — embedded inside a .kpi-tile__value for "top N" breakdowns.
   Smaller than the default 32px KPI value so three entries don't balloon
   the tile height past its single-line neighbours.
   Using the tag-qualified selector ``ol.rank-list`` to outspecific
   Streamlit's global ``ol`` markdown rule (otherwise padding-inline-start
   defaults to ~40px and the list looks weirdly indented). */
ol.rank-list {{
    list-style: none;
    counter-reset: rank;
    padding: 0;
    padding-inline-start: 0;
    margin: 0;
    font-size: 18px;
    font-weight: 500;
    font-family: 'JetBrains Mono', monospace;
    color: {PALETTE.text};
    line-height: 1.45;
}}
ol.rank-list li {{
    counter-increment: rank;
    display: flex;
    gap: 10px;
    align-items: baseline;
    margin: 0;
    padding: 0;
}}
ol.rank-list li::before {{
    content: counter(rank) ".";
    color: {PALETTE.text_muted};
    font-size: 14px;
    font-weight: 500;
    min-width: 18px;
}}

/* --- Root-cause hero card ------------------------------------------------- */
/* Flex with wrap so the confidence ring sits right-justified inside the card
   on wide screens, but wraps below the service name on narrow screens instead
   of overflowing the card (and the viewport). */
.rc-card {{
    padding: 28px 32px;
    background: {PALETTE.bg_elevated};
    border: 1px solid rgba(96, 165, 250, 0.2);
    border-radius: 16px;
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 24px;
    /* breathing room between the root-cause hero and the KPI-tile row below. */
    margin-bottom: 16px;
    box-shadow: inset 0 0 0 1px rgba(96, 165, 250, 0.06);
}}
.rc-card__body {{
    flex: 1 1 220px;
    min-width: 0;
    word-break: break-word;
}}
.rc-card__label {{
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    color: {PALETTE.text_muted};
    font-family: 'JetBrains Mono', monospace;
    margin-bottom: 8px;
}}
.rc-card__service {{
    font-size: 28px;
    font-weight: 600;
    font-family: 'JetBrains Mono', monospace;
    color: {PALETTE.text};
    margin: 0;
    line-height: 1.15;
}}
.rc-card__issue {{
    font-size: 14px;
    color: {PALETTE.text_muted};
    margin: 6px 0 0 0;
    line-height: 1.5;
}}

.rc-ring {{
    position: relative;
    width: 96px;
    height: 96px;
    flex: 0 0 auto;
}}
.rc-ring__bg, .rc-ring__fg {{
    fill: none;
    stroke-width: 8;
}}
.rc-ring__bg {{ stroke: {PALETTE.border_strong}; }}
.rc-ring__fg {{
    stroke: {PALETTE.primary};
    stroke-linecap: round;
    transform: rotate(-90deg);
    transform-origin: center;
    transition: stroke-dashoffset 600ms ease;
}}
.rc-ring__pct {{
    position: absolute;
    inset: 0;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    font-family: 'JetBrains Mono', monospace;
}}
.rc-ring__pct-value {{ font-size: 20px; font-weight: 600; color: {PALETTE.text}; }}
.rc-ring__pct-label {{
    font-size: 9px;
    letter-spacing: 0.12em;
    color: {PALETTE.text_muted};
    text-transform: uppercase;
    margin-top: 2px;
}}

/* --- Phase-progression stepper -------------------------------------------- */
.phase-stepper {{
    display: grid;
    grid-template-columns: repeat(5, 1fr);
    gap: 0;
    padding: 16px 0;
    position: relative;
}}
.phase-stepper::before {{
    content: '';
    position: absolute;
    top: 28px;
    left: 10%;
    right: 10%;
    height: 1px;
    background: {PALETTE.border_strong};
    z-index: 0;
}}
.phase-step {{
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 8px;
    position: relative;
    z-index: 1;
}}
.phase-step__node {{
    width: 24px;
    height: 24px;
    border-radius: 50%;
    border: 1.5px solid {PALETTE.border_strong};
    background: {PALETTE.bg};
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 11px;
    font-weight: 700;
    color: {PALETTE.text_dim};
    font-family: 'JetBrains Mono', monospace;
}}
.phase-step--done .phase-step__node {{
    border-color: {PALETTE.success};
    background: rgba(34, 197, 94, 0.15);
    color: {PALETTE.success};
}}
.phase-step--active .phase-step__node {{
    border-color: {PALETTE.primary};
    background: rgba(96, 165, 250, 0.2);
    color: {PALETTE.primary};
    animation: phase-pulse 1.8s ease-in-out infinite;
}}
@keyframes phase-pulse {{
    0%, 100% {{ box-shadow: 0 0 0 0 rgba(96, 165, 250, 0.35); }}
    50%      {{ box-shadow: 0 0 0 6px rgba(96, 165, 250, 0.0); }}
}}
.phase-step__label {{
    font-size: 10px;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: {PALETTE.text_muted};
    text-align: center;
    font-family: 'JetBrains Mono', monospace;
}}
.phase-step--done .phase-step__label   {{ color: {PALETTE.text}; }}
.phase-step--active .phase-step__label {{ color: {PALETTE.primary}; }}

/* --- Severity-coded evidence timeline ------------------------------------- */
.timeline {{
    border-left: 2px solid {PALETTE.border_strong};
    margin-left: 10px;
    padding-left: 24px;
    display: flex;
    flex-direction: column;
    gap: 14px;
}}
.timeline-entry {{
    position: relative;
    font-size: 13px;
    line-height: 1.55;
    color: {PALETTE.text};
}}
.timeline-entry::before {{
    content: '';
    position: absolute;
    left: -32px;
    top: 6px;
    width: 10px;
    height: 10px;
    border-radius: 50%;
    background: {PALETTE.text_dim};
    border: 2px solid {PALETTE.bg};
}}
.timeline-entry--success::before  {{ background: {PALETTE.success}; }}
.timeline-entry--warn::before     {{ background: {PALETTE.warn}; }}
.timeline-entry--error::before    {{ background: {PALETTE.error}; }}
.timeline-entry--critical::before {{ background: {PALETTE.critical}; }}
.timeline-entry--info::before     {{ background: {PALETTE.primary}; }}
.timeline-entry__time {{
    font-family: 'JetBrains Mono', monospace;
    color: {PALETTE.text_dim};
    font-size: 12px;
    margin-right: 10px;
}}
.timeline-entry__badge {{
    margin-left: 10px;
    display: inline-block;
    vertical-align: middle;
}}

/* --- Recent-investigation card (used on Overview) ------------------------- */
.inv-card {{
    padding: 16px 18px;
    background: {PALETTE.bg_elevated};
    border: 1px solid {PALETTE.border};
    border-radius: 10px;
    display: flex;
    flex-direction: column;
    gap: 10px;
}}
.inv-card__row {{ display: flex; align-items: center; justify-content: space-between; gap: 12px; }}
.inv-card__id {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 12px;
    color: {PALETTE.text_muted};
}}
.inv-card__service {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 15px;
    font-weight: 600;
    color: {PALETTE.text};
}}
.inv-card__meta {{
    font-size: 12px;
    color: {PALETTE.text_muted};
}}

/* --- RCA report container ------------------------------------------------- */
.rca-report {{
    padding: 24px 28px;
    background: {PALETTE.bg_elevated};
    border: 1px solid {PALETTE.border};
    border-radius: 12px;
    font-family: 'JetBrains Mono', 'SF Mono', Menlo, monospace;
    font-size: 12.5px;
    line-height: 1.65;
    color: {PALETTE.text};
    white-space: pre-wrap;
    word-wrap: break-word;
    overflow-x: auto;
    max-height: 720px;
    overflow-y: auto;
}}

/* --- Generic section divider --------------------------------------------- */
.section-divider {{
    height: 1px;
    background: {PALETTE.border};
    margin: 32px 0 24px 0;
    position: relative;
}}
.section-divider__label {{
    position: absolute;
    top: -10px;
    left: 0;
    padding-right: 12px;
    background: {PALETTE.bg};
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: {PALETTE.text_muted};
    font-family: 'JetBrains Mono', monospace;
}}

/* --- Streamlit built-in tweaks -------------------------------------------- */
/* Make the primary form button feel less "demo" and more "console-grade". */
.stButton > button[kind="primary"] {{
    background: {PALETTE.primary};
    color: {PALETTE.bg};
    border: none;
    font-family: 'JetBrains Mono', monospace;
    font-weight: 600;
    letter-spacing: 0.04em;
    border-radius: 8px;
    padding: 10px 20px;
    transition: background 180ms ease, transform 120ms ease;
}}
.stButton > button[kind="primary"]:hover {{
    background: #93C5FD;  /* primary lightened */
    transform: translateY(-1px);
}}

/* Tables: slightly denser, monospace. */
[data-testid="stTable"] table,
[data-testid="stDataFrame"] * {{
    font-family: 'IBM Plex Sans', sans-serif;
    font-size: 13px;
}}
[data-testid="stDataFrame"] th {{
    font-family: 'JetBrains Mono', monospace !important;
    font-size: 11px !important;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: {PALETTE.text_muted} !important;
}}
</style>
"""


# ---------------------------------------------------------------------------
# Helpers for dashboard.py
# ---------------------------------------------------------------------------


def inject_theme() -> None:
    """Inject the CSS overrides into a running Streamlit app.

    Call once at the top of ``dashboard.py`` (immediately after
    ``st.set_page_config``). Safe to call multiple times but idempotent —
    Streamlit replaces the same DOM node.
    """
    st.markdown(CSS_OVERRIDES, unsafe_allow_html=True)


_PILL_CLASSES = {
    "success": "status-pill--success",
    "warn": "status-pill--warn",
    "error": "status-pill--error",
    "critical": "status-pill--critical",
    "info": "status-pill--info",
    "muted": "status-pill--muted",
}


def status_pill(label: str, severity: str = "info") -> str:
    """Return the HTML for a status pill.

    ``severity`` must be one of: success, warn, error, critical, info, muted.
    Unknown severities fall back to ``muted``.
    """
    css_class = _PILL_CLASSES.get(severity, _PILL_CLASSES["muted"])
    return f'<span class="status-pill {css_class}">{label}</span>'


def kpi_tile(
    label: str,
    value: str,
    trend: str | None = None,
    trend_direction: str = "flat",
) -> str:
    """Return the HTML for a KPI tile.

    ``trend_direction`` is ``up``, ``down``, or ``flat`` — drives the trend
    color. ``trend`` is the raw display string (caller includes an arrow
    glyph if desired).
    """
    trend_html = ""
    if trend:
        direction_class = {
            "up": "kpi-tile__trend--up",
            "down": "kpi-tile__trend--down",
        }.get(trend_direction, "")
        trend_html = f'<div class="kpi-tile__trend {direction_class}">{trend}</div>'
    return (
        '<div class="kpi-tile">'
        f'<div class="kpi-tile__label">{label}</div>'
        f'<div class="kpi-tile__value">{value}</div>'
        f"{trend_html}"
        "</div>"
    )


def root_cause_card(service: str, issue: str, confidence: float) -> str:
    """Return the HTML for the root-cause hero card.

    ``confidence`` is a float in [0, 1]. The SVG ring is an 88-px-wide
    circle with circumference 276.5; we offset the fg stroke by
    (1 - confidence) * circumference to produce the fill.

    The ``issue`` slot is rendered only when non-empty. Callers are
    expected to pass a meaningful short summary (not the raw first line
    of an RCA report, which typically starts with box-drawing
    divider characters).
    """
    pct = max(0, min(100, int(round(confidence * 100))))
    circumference = 276.5
    offset = circumference * (1 - confidence)
    issue_html = (
        f'<div class="rc-card__issue">{issue}</div>' if issue and issue.strip() else ""
    )
    return (
        '<div class="rc-card">'
        '<div class="rc-card__body">'
        '<div class="rc-card__label">Root Cause</div>'
        f'<div class="rc-card__service">{service}</div>'
        f"{issue_html}"
        "</div>"
        '<div class="rc-ring">'
        '<svg viewBox="0 0 96 96" width="96" height="96">'
        '<circle class="rc-ring__bg" cx="48" cy="48" r="44"/>'
        '<circle class="rc-ring__fg" cx="48" cy="48" r="44" '
        f'stroke-dasharray="{circumference}" stroke-dashoffset="{offset:.2f}"/>'
        "</svg>"
        '<div class="rc-ring__pct">'
        f'<span class="rc-ring__pct-value">{pct}%</span>'
        '<span class="rc-ring__pct-label">conf</span>'
        "</div>"
        "</div>"
        "</div>"
    )


PHASE_LABELS = ["Sweep", "Hypotheses", "Evidence", "Causation", "Report"]


def phase_stepper(active_phase: int | None, labels: list[str] | None = None) -> str:
    """Return the HTML for an N-phase investigation stepper.

    ``active_phase`` is the 0-indexed current phase, or None to indicate
    the investigation hasn't started. Phases before ``active_phase``
    render as ``done``, the active phase pulses, later phases render as
    pending. Pass ``active_phase >= len(labels)`` to render every step
    as done (useful for a terminal ``completed`` state).

    ``labels`` overrides the default 5-phase list (``PHASE_LABELS``).
    The guided-demo UI passes 5 demo-specific labels (Injecting,
    Waiting, Investigating, Restoring, Completed) while the legacy
    investigation UI keeps the original Sweep/Hypotheses/... set.
    """
    step_labels = labels if labels is not None else PHASE_LABELS
    if active_phase is None:
        active_phase = -1  # nothing active or done
    steps = []
    for i, label in enumerate(step_labels):
        if active_phase >= len(step_labels):
            # All done (terminal state); mark every step as done.
            state = "done"
            glyph = "✓"
        elif i < active_phase:
            state = "done"
            glyph = "✓"
        elif i == active_phase:
            state = "active"
            glyph = f"{i + 1}"
        else:
            state = "pending"
            glyph = f"{i + 1}"
        steps.append(
            f'<div class="phase-step phase-step--{state}">'
            f'<div class="phase-step__node">{glyph}</div>'
            f'<div class="phase-step__label">{label}</div>'
            "</div>"
        )
    return f'<div class="phase-stepper">{"".join(steps)}</div>'


_TIMELINE_SEVERITY_CLASSES = {
    "success": "timeline-entry--success",
    "warn": "timeline-entry--warn",
    "error": "timeline-entry--error",
    "critical": "timeline-entry--critical",
    "info": "timeline-entry--info",
}


def evidence_timeline(entries: list[dict[str, str]]) -> str:
    """Return the HTML for the evidence-chain timeline.

    ``entries`` is a list of dicts with keys: ``time`` (timestamp string),
    ``text`` (human-readable description), and ``severity`` (one of
    success, warn, error, critical, info). Unknown severities render as
    muted grey dots.
    """
    rows: list[str] = []
    for entry in entries:
        time = entry.get("time", "")
        text = entry.get("text", "")
        severity = entry.get("severity", "info")
        css_class = _TIMELINE_SEVERITY_CLASSES.get(severity, "")
        badge = ""
        if severity in ("warn", "error", "critical"):
            badge = status_pill(severity.upper(), severity)
            badge = f'<span class="timeline-entry__badge">{badge}</span>'
        rows.append(
            f'<div class="timeline-entry {css_class}">'
            f'<span class="timeline-entry__time">{time}</span>'
            f"{text}{badge}"
            "</div>"
        )
    return f'<div class="timeline">{"".join(rows)}</div>'


def hero_bar(title: str, subtitle: str, status_label: str, status_severity: str = "info") -> str:
    """Return the HTML for the top-of-page hero bar.

    Used on every non-trivial page. The right-hand pill communicates the
    API connection state at a glance.
    """
    pill = status_pill(status_label, status_severity)
    return (
        '<div class="ops-hero">'
        "<div>"
        f'<h1 class="ops-hero__title">{title}</h1>'
        f'<p class="ops-hero__subtitle">{subtitle}</p>'
        "</div>"
        f"<div>{pill}</div>"
        "</div>"
    )


def section_divider(label: str) -> str:
    """Return the HTML for a labelled section divider."""
    return f'<div class="section-divider"><span class="section-divider__label">{label}</span></div>'


def investigation_card(inv: dict) -> str:
    """Return the HTML for a single recent-investigation card.

    ``inv`` is expected to have the shape of ``InvestigationResponse``
    serialized to dict (so dashboard callers can pass API responses
    directly without adaptation).
    """
    rc = inv.get("root_cause") or {}
    service = rc.get("service", "—")
    confidence = rc.get("confidence", 0.0)
    status = inv.get("status", "unknown")
    severity = "success" if status == "completed" else "error"
    inv_id = inv.get("investigation_id", "—")
    duration = inv.get("duration_seconds", 0.0)
    return (
        '<div class="inv-card">'
        '<div class="inv-card__row">'
        f'<span class="inv-card__id">{inv_id}</span>'
        f"{status_pill(status.upper(), severity)}"
        "</div>"
        '<div class="inv-card__row">'
        f'<span class="inv-card__service">{service}</span>'
        f'<span class="inv-card__meta">{confidence:.0%} · {duration:.1f}s</span>'
        "</div>"
        "</div>"
    )


__all__ = [
    "CSS_OVERRIDES",
    "PALETTE",
    "PHASE_LABELS",
    "PLOTLY_COLORWAY",
    "Palette",
    "evidence_timeline",
    "hero_bar",
    "inject_theme",
    "investigation_card",
    "kpi_tile",
    "phase_stepper",
    "root_cause_card",
    "section_divider",
    "status_pill",
]
