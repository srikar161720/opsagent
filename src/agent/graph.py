"""LangGraph workflow for the OpsAgent investigation agent.

Defines a five-node StateGraph that enforces the RCA investigation
protocol: topology analysis, hypothesis formation, evidence gathering,
causal analysis, and report generation.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import END, START, StateGraph

from src.agent.prompts.report_template import RCA_REPORT_TEMPLATE
from src.agent.state import AgentState
from src.agent.tools import TOOLS
from src.agent.tools.discover_causation import discover_causation
from src.agent.tools.get_topology import get_topology

logger = logging.getLogger(__name__)

load_dotenv()

# ── Tool lookup ──────────────────────────────────────────────────────────
_TOOLS_BY_NAME = {t.name: t for t in TOOLS}


def _get_llm() -> ChatGoogleGenerativeAI:
    """Create the LLM instance with tools bound."""
    api_key = os.environ.get("GEMINI_API_KEY", "")
    return ChatGoogleGenerativeAI(
        model="gemini-3-flash-preview",
        temperature=0.1,
        max_output_tokens=4096,
        google_api_key=api_key,
        max_retries=3,
    )


def _extract_text(response: Any) -> str:
    """Return the plain-text content of an LLM response.

    Gemini 2.x returns ``response.content`` as a plain string. Gemini 3.x
    returns a list of content parts (``[{"type": "text", "text": "..."}, ...]``).
    This helper normalises both shapes and ignores non-text parts (thought
    chunks, signatures, etc.).
    """
    content = getattr(response, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                # Gemini 3 "text" parts; skip "thought" / signature parts.
                if item.get("type") == "text":
                    parts.append(item.get("text", ""))
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts)
    return ""


# ── Node functions ───────────────────────────────────────────────────────


def sweep_probes_node(state: AgentState) -> dict[str, Any]:
    """Objective probe + metric + log sweep over every affected service.

    This is the SECOND graph node (after analyze_context). It runs BEFORE
    the LLM forms hypotheses so the LLM sees ground-truth signals for
    every service — not just the 2-3 it would self-select.

    Four-channel sweep per service:

    1. ``probe_up``      — catches service_crash / network_partition /
       connection_exhaustion (availability drops to 0).
    2. ``probe_latency`` — catches high_latency (reachable but slow).
    3. ``cpu_usage``     — catches CPU-saturated services.
    4. ``memory_usage``  — catches memory pressure (checkoutservice OOM).
    5. ``search_logs(crash-patterns)`` — catches crash/OOM/fatal log lines
       (memory_pressure's OOMKilled, config_error's fatal port-bind,
       currencyservice's std::logic_error in crash-loop, etc.).

    The sweep does NOT decrement ``tool_calls_remaining``. Rationale:
    mandatory infrastructure step, not LLM-directed reasoning.

    CRITICAL signals from ANY channel are surfaced two ways:

    1. Stored in each evidence entry's ``args['critical']`` as a bool (direct,
       untruncated flag — ``analyze_causation_node`` reads this as ground
       truth). The prior string-scan of ``finding`` is unreliable because
       ``finding`` is truncated to 500 chars and the CRITICAL note often
       sits after the (verbose) timestamps/values arrays.
    2. Summarised in the AIMessage emitted by this node so the LLM sees
       the same picture when forming hypotheses.
    """
    from src.agent.tools.query_metrics import query_metrics
    from src.agent.tools.search_logs import search_logs

    affected = state.get("affected_services", [])
    pinned_start = state.get("start_time")
    window_start = state.get("anomaly_window", ("", ""))[0]

    evidence = list(state.get("evidence", []))
    critical_signals: list[str] = []  # "svc/metric_or_logs"
    healthy_services: set[str] = set(affected)

    # Channels 1-4: per-service metric probes (probe_up, probe_latency,
    # cpu_usage, memory_usage). cpu_usage catches memory_pressure-adjacent
    # CPU spikes; memory_usage catches memory saturation directly.
    for svc in affected:
        for metric in ("probe_up", "probe_latency", "cpu_usage", "memory_usage"):
            qm_args: dict[str, Any] = {
                "service_name": svc,
                "metric_name": metric,
                "time_range_minutes": 10,
            }
            if pinned_start:
                qm_args["start_time"] = pinned_start
            try:
                result = query_metrics.invoke(qm_args)
            except Exception as exc:
                logger.warning("Sweep %s failed for %s: %s", metric, svc, exc)
                continue

            note_text = str(result.get("note", ""))
            is_critical = "CRITICAL" in note_text
            finding = json.dumps(result, default=str)[:500]
            evidence.append(
                {
                    "tool": "query_metrics",
                    "args": {
                        "service_name": svc,
                        "metric_name": metric,
                        "pre_gathered": True,
                        # Direct flag, untruncated — analyze_causation_node
                        # reads this instead of string-matching finding.
                        "critical": is_critical,
                    },
                    "finding": finding,
                    "timestamp": window_start,
                    "supports_hypothesis": "",
                }
            )
            if is_critical:
                critical_signals.append(f"{svc}/{metric}")
                healthy_services.discard(svc)

    # Channel 5: per-service crash-log sweep. search_logs escalates to
    # critical_service when ≥3 crash/OOM/fatal patterns match (see
    # _detect_crash_signal in search_logs.py).
    for svc in affected:
        log_args: dict[str, Any] = {
            "query": (
                "OOMKilled OR SIGSEGV OR panic OR std::logic_error OR "
                "terminate OR fatal OR segfault OR unhandled OR exit"
            ),
            "service_filter": svc,
            "time_range_minutes": 10,
            "limit": 50,
        }
        if pinned_start:
            log_args["start_time"] = pinned_start
        try:
            log_result = search_logs.invoke(log_args)
        except Exception as exc:
            logger.warning("Sweep crash-log failed for %s: %s", svc, exc)
            continue

        is_critical = bool(log_result.get("critical_service"))
        finding = json.dumps(log_result, default=str)[:500]
        evidence.append(
            {
                "tool": "search_logs",
                "args": {
                    "service_filter": svc,
                    # Legacy key — analyze_causation_node historically
                    # extracted "service_name" only. Setting it here lets
                    # existing string-scan logic also work if args.critical
                    # is ever missed.
                    "service_name": svc,
                    "query": "crash_patterns",
                    "pre_gathered": True,
                    "critical": is_critical,
                },
                "finding": finding,
                "timestamp": window_start,
                "supports_hypothesis": "",
            }
        )
        if is_critical:
            critical_signals.append(f"{svc}/logs")
            healthy_services.discard(svc)

    summary_lines = [
        "Probe+metric+log sweep complete (breadth-first across all affected "
        "services: probe_up, probe_latency, cpu_usage, memory_usage, and "
        "crash-pattern logs).",
    ]
    if critical_signals:
        summary_lines.append(f"CRITICAL signals: {', '.join(critical_signals)}")
    healthy_now = sorted(healthy_services)
    if healthy_now:
        summary_lines.append(
            f"No CRITICAL flag across any channel for: {', '.join(healthy_now)}"
        )
    summary_lines.append(
        "Use the above data as your starting point. Do NOT re-query probe_up, "
        "probe_latency, cpu_usage, memory_usage, or crash-pattern logs for "
        "services already covered — investigate different metrics (network "
        "rates, non-crash log patterns) or the same metrics on OTHER services "
        "to confirm or refute hypotheses. If a service is flagged CRITICAL "
        "above, prioritise it as a root-cause candidate."
    )

    sweep_message = AIMessage(content="\n".join(summary_lines))

    # Do NOT decrement tool_calls_remaining — sweep is mandatory infra.
    return {
        "messages": [sweep_message],
        "evidence": evidence,
    }


def analyze_context_node(state: AgentState) -> dict[str, Any]:
    """Parse the alert and retrieve the service topology.

    This is the first node — it calls ``get_topology`` directly
    (not via the LLM) to ground the investigation in the real
    dependency graph.
    """
    alert = state["alert"]
    affected = state.get("affected_services", [])

    # Call topology tool directly for each affected service
    topology_results: list[dict] = []
    for svc in affected:
        topo = get_topology.invoke({"service_name": svc})
        topology_results.append(topo)

    # Also get full topology
    full_topo = get_topology.invoke({"service_name": None})

    topology_summary = json.dumps(
        {
            "full_topology": {
                "nodes": [n["name"] for n in full_topo.get("nodes", [])],
                "edges": [f"{e['source']} -> {e['target']}" for e in full_topo.get("edges", [])],
            },
            "affected_service_details": topology_results,
        },
        indent=2,
    )

    context_message = AIMessage(
        content=(
            f"I've analyzed the alert and retrieved the service topology.\n\n"
            f"**Alert:** {alert.get('title', 'Unknown')}\n"
            f"**Severity:** {alert.get('severity', 'unknown')}\n"
            f"**Affected services:** {', '.join(affected)}\n"
            f"**Anomaly score:** {alert.get('anomaly_score', 'N/A')}\n\n"
            f"**Service Topology:**\n```json\n{topology_summary}\n```\n\n"
            f"I'll now form hypotheses about the root cause based on "
            f"upstream dependencies."
        )
    )

    return {
        "messages": [context_message],
        "tool_calls_remaining": state.get("tool_calls_remaining", 10),
    }


def form_hypothesis_node(state: AgentState) -> dict[str, Any]:
    """Use the LLM to reason about topology and evidence to form hypotheses."""
    llm = _get_llm()

    existing_evidence = state.get("evidence", [])
    causal = state.get("causal_graph")

    prompt = (
        "Based on the alert context, service topology, and any evidence "
        "gathered so far, form or refine your hypotheses about the root cause.\n\n"
    )
    if existing_evidence:
        prompt += f"Evidence collected so far:\n{json.dumps(existing_evidence, indent=2)}\n\n"
    if causal:
        prompt += f"Causal analysis results:\n{json.dumps(causal, indent=2)}\n\n"

    prompt += (
        "Provide your ranked hypotheses as a JSON list with format:\n"
        '[{"service": "name", "reason": "why", "confidence": 0.0-1.0, '
        '"status": "investigating"}]\n\n'
        "Rank them from most likely to least likely root cause."
    )

    response = llm.invoke(state["messages"] + [HumanMessage(content=prompt)])

    # Try to parse hypotheses from the response. Gemini 3 returns content as
    # a list of parts, not a plain string — use _extract_text to normalise.
    content = _extract_text(response)
    hypotheses = _parse_hypotheses(content, state.get("hypotheses", []))

    return {
        "messages": [response],
        "hypotheses": hypotheses,
    }


def gather_evidence_node(state: AgentState) -> dict[str, Any]:
    """Use the LLM with tools to gather evidence for/against hypotheses.

    The LLM decides which tools to call.  We execute tool calls manually
    to track the tool call budget.
    """
    llm = _get_llm()
    llm_with_tools = llm.bind_tools([t for t in TOOLS if t.name != "discover_causation"])
    remaining = state.get("tool_calls_remaining", 0)
    pinned_start = state.get("start_time")

    if remaining <= 1:
        # Reserve at least 1 call for causal analysis
        return {
            "messages": [
                AIMessage(content="Tool budget nearly exhausted. Moving to causal analysis.")
            ],
        }

    hypotheses = state.get("hypotheses", [])
    prompt = (
        "Gather evidence to validate or refute your hypotheses. "
        "Use query_metrics and search_logs on your top 2-3 suspect services. "
        "Use search_runbooks if you have a strong hypothesis about the issue type.\n\n"
        "IMPORTANT: Use time_range_minutes=10 for all query_metrics calls "
        "(not the default 30). A shorter window gives better signal-to-noise "
        "for recent faults.\n\n"
        f"Remaining tool calls: {remaining - 1} (reserving 1 for causal analysis)\n\n"
        f"Current hypotheses:\n{json.dumps(hypotheses, indent=2)}"
    )

    response = llm_with_tools.invoke(state["messages"] + [HumanMessage(content=prompt)])

    new_messages: list = [response]
    new_evidence = list(state.get("evidence", []))
    calls_used = 0

    # Execute any tool calls the LLM made
    if hasattr(response, "tool_calls") and response.tool_calls:
        for tc in response.tool_calls:
            if calls_used >= remaining - 1:
                break

            tool_name = tc["name"]
            tool_args = dict(tc["args"])  # copy so we can mutate
            # Thread the pinned start_time into metric/log tools so every
            # query during this investigation sees the same isolated window.
            if pinned_start and tool_name in {
                "query_metrics",
                "search_logs",
                "discover_causation",
            }:
                tool_args.setdefault("start_time", pinned_start)
            tool_fn = _TOOLS_BY_NAME.get(tool_name)

            if tool_fn is None:
                tool_result = f"Unknown tool: {tool_name}"
            else:
                try:
                    tool_result = tool_fn.invoke(tool_args)
                except Exception as exc:
                    tool_result = f"Tool error: {exc}"

            result_str = (
                json.dumps(tool_result, default=str)
                if not isinstance(tool_result, str)
                else tool_result
            )

            new_messages.append(
                ToolMessage(
                    content=result_str,
                    tool_call_id=tc["id"],
                )
            )

            new_evidence.append(
                {
                    "tool": tool_name,
                    "args": tool_args,
                    "finding": result_str[:500],
                    "timestamp": state.get("anomaly_window", ("", ""))[0],
                    "supports_hypothesis": "",
                }
            )
            calls_used += 1

    # Force a log search if the LLM didn't do one and budget allows.
    # Logs reveal error patterns (connection refused, timeout, panic) that
    # are invisible in container-level metrics.
    logs_searched = any(e.get("tool") == "search_logs" for e in new_evidence)
    budget_left = remaining - calls_used
    if not logs_searched and budget_left > 1 and hypotheses:
        top_svc = hypotheses[0].get("service", "")
        if top_svc:
            from src.agent.tools.search_logs import search_logs

            log_args: dict[str, Any] = {
                "query": "error OR crash OR timeout OR refused OR panic OR fatal",
                "service_filter": top_svc,
                "time_range_minutes": 10,
                "limit": 50,
            }
            if pinned_start:
                log_args["start_time"] = pinned_start
            try:
                log_result = search_logs.invoke(log_args)
                log_str = json.dumps(log_result, default=str)
                new_messages.append(
                    AIMessage(content=f"[Auto] Log search for {top_svc}:\n{log_str[:500]}")
                )
                new_evidence.append(
                    {
                        "tool": "search_logs",
                        "args": {"service_filter": top_svc, "query": "error"},
                        "finding": log_str[:500],
                        "timestamp": state.get("anomaly_window", ("", ""))[0],
                        "supports_hypothesis": "",
                    }
                )
                calls_used += 1
            except Exception:
                logger.warning("Forced log search for %s failed", top_svc)

    return {
        "messages": new_messages,
        "evidence": new_evidence,
        "tool_calls_remaining": remaining - calls_used,
    }


def analyze_causation_node(state: AgentState) -> dict[str, Any]:
    """Run causal discovery on the top suspect services.

    Calls ``discover_causation`` directly with the top hypothesis services.
    """
    remaining = state.get("tool_calls_remaining", 0)
    hypotheses = state.get("hypotheses", [])

    # Collect unique services from hypotheses + affected services
    services: list[str] = []
    for h in sorted(hypotheses, key=lambda x: x.get("confidence", 0), reverse=True):
        svc = h.get("service", "")
        if svc and svc not in services:
            services.append(svc)
        if len(services) >= 5:
            break

    # Add affected services not already in the list
    for svc in state.get("affected_services", []):
        if svc not in services:
            services.append(svc)
        if len(services) >= 5:
            break

    if len(services) < 2:
        # Not enough services for causal analysis
        return {
            "messages": [
                AIMessage(
                    content="Insufficient services for causal analysis. "
                    "Proceeding to report generation."
                )
            ],
            "tool_calls_remaining": max(0, remaining - 1),
        }

    # Build the CRITICAL service set from evidence BEFORE running PC, so we
    # can pass it as a prior into discover_causation. A service with a
    # CRITICAL finding has frozen/missing metrics and confuses PC; we
    # explicitly exclude it from the PC input and inject synthesized edges
    # from it to surviving services.
    #
    # Two extraction paths:
    # 1. Direct ``args['critical']`` flag set by the sweep — ground truth,
    #    untruncated. This is the primary source now because ``finding`` is
    #    truncated to 500 chars and the CRITICAL note often sits past the
    #    cutoff when timestamps/values arrays are large.
    # 2. Legacy string-scan of ``finding`` — kept for backward compatibility
    #    with LLM-directed tool calls that don't set the ``critical`` flag.
    evidence = state.get("evidence", [])
    critical_services: set[str] = set()
    for e in evidence:
        args = e.get("args", {})
        if not isinstance(args, dict):
            continue
        # Path 1: direct flag from sweep (reliable)
        if args.get("critical"):
            svc = args.get("service_name") or args.get("service_filter") or ""
            if svc:
                critical_services.add(svc)
                continue
        # Path 2: string-scan fallback
        if "CRITICAL" in str(e.get("finding", "")):
            svc = args.get("service_name") or args.get("service_filter") or ""
            if svc:
                critical_services.add(svc)

    # Run causal discovery
    pinned_start = state.get("start_time")
    causal_args: dict[str, Any] = {"services": services, "time_range_minutes": 10}
    if pinned_start:
        causal_args["start_time"] = pinned_start
    if critical_services:
        causal_args["critical_services"] = sorted(critical_services)
    try:
        # Use a 10-minute window to maximize the anomaly-to-baseline ratio.
        # With a 60s pre-investigation wait, ~4 of the 40 data points (15s step)
        # will contain the fault signal — much stronger than 4/120 with 30 min.
        causal_result = discover_causation.invoke(causal_args)
    except Exception as exc:
        logger.exception("Causal discovery failed")
        causal_result = {
            "causal_edges": [],
            "root_cause": "unknown",
            "root_cause_confidence": 0.0,
            "counterfactual": f"Causal discovery failed: {exc}",
            "graph_ascii": "",
        }

    causal_root = causal_result.get("root_cause", "unknown")
    causal_confidence = causal_result.get("root_cause_confidence", 0.0)

    # Determine final root cause: combine causal discovery with LLM hypotheses.
    # If causal confidence is low (< 50%) or result is "inconclusive",
    # prefer the LLM's top hypothesis — especially important when the true
    # root cause service is DOWN and invisible to the PC algorithm.
    root_cause = causal_root
    confidence = causal_confidence

    top_hypothesis = next(
        (
            h
            for h in sorted(hypotheses, key=lambda x: x.get("confidence", 0), reverse=True)
            if h.get("service")
        ),
        None,
    )

    # Priority 1: If ANY service has a CRITICAL signal (stale/sparse/no data),
    # it is almost certainly DOWN — override everything else.
    # A CRITICAL signal is stronger than any LLM reasoning or PC result
    # because it means the service literally stopped reporting metrics.
    if critical_services:
        # Pick the CRITICAL service that appears highest in the hypothesis list
        critical_in_hypotheses = next(
            (
                h["service"]
                for h in sorted(
                    hypotheses,
                    key=lambda x: x.get("confidence", 0),
                    reverse=True,
                )
                if h.get("service") in critical_services
            ),
            None,
        )
        if critical_in_hypotheses:
            root_cause = critical_in_hypotheses
            confidence = 0.75
            logger.info(
                "CRITICAL override: %s has stale/sparse metrics (service DOWN)",
                root_cause,
            )
        elif critical_services:
            # CRITICAL service not in hypotheses — use the first one
            root_cause = next(iter(critical_services))
            confidence = 0.70
            logger.info(
                "CRITICAL override (not in hypotheses): %s",
                root_cause,
            )
    elif causal_confidence < 0.5 or causal_root in ("inconclusive", "unknown"):
        # Priority 2: Low-confidence causal result — prefer LLM hypothesis
        if top_hypothesis:
            hyp_svc = top_hypothesis["service"]
            hyp_conf = top_hypothesis.get("confidence", 0.0)
            if hyp_conf > causal_confidence:
                root_cause = hyp_svc
                confidence = min(hyp_conf, 0.6)
                logger.info(
                    "Overriding low-confidence causal %s (%.0f%%) with hypothesis %s (%.0f%%)",
                    causal_root,
                    causal_confidence * 100,
                    root_cause,
                    confidence * 100,
                )
    elif top_hypothesis and top_hypothesis["service"] != causal_root:
        # Priority 3: PC and LLM disagree — use average confidence
        confidence = (causal_confidence + top_hypothesis.get("confidence", 0.0)) / 2

    causation_message = AIMessage(
        content=(
            f"Causal analysis complete.\n\n"
            f"**PC Algorithm root cause:** {causal_root} ({causal_confidence:.0%})\n"
            f"**Final root cause (combined):** {root_cause} ({confidence:.0%})\n\n"
            f"**Causal graph:**\n```\n{causal_result.get('graph_ascii', '')}\n```\n\n"
            f"**Counterfactual:** {causal_result.get('counterfactual', '')}"
        )
    )

    return {
        "messages": [causation_message],
        "causal_graph": causal_result,
        "root_cause": root_cause,
        "root_cause_confidence": confidence,
        "tool_calls_remaining": max(0, remaining - 1),
    }


def knockout_node(state: AgentState) -> dict[str, Any]:
    """Downstream-falsification sanity check for the chosen root cause.

    Runs AFTER analyze_causation_node. Given a ``root_cause`` candidate,
    asks: "does this service's OWN evidence support the claim more than
    an alternative top-3 candidate's evidence?"

    Specifically, for each candidate (root_cause + top-3 hypotheses), we
    count how many sweep-produced evidence entries are CRITICAL for that
    candidate. If an alternative candidate has strictly MORE CRITICAL
    signals than the current root_cause, we swap root_cause to that
    alternative.

    Skips the knockout check entirely when root_cause_confidence ≥ 0.75,
    because that's the high-confidence band indicating the CRITICAL-
    override already fired — no need to second-guess.

    Does not decrement the tool budget (uses only state data already
    gathered by earlier nodes).
    """
    root_cause = state.get("root_cause") or ""
    confidence = state.get("root_cause_confidence", 0.0) or 0.0
    hypotheses = state.get("hypotheses", []) or []
    evidence = state.get("evidence", []) or []

    # Trust the CRITICAL-override band — don't second-guess ≥0.75.
    if confidence >= 0.75 or root_cause in ("", "unknown", "inconclusive"):
        return {
            "messages": [
                AIMessage(
                    content=(
                        f"Knockout check skipped (confidence {confidence:.0%} "
                        f"is in the high-trust band, or root_cause is "
                        f"indeterminate)."
                    )
                )
            ],
        }

    # Build the candidate set: current root_cause + top-3 LLM hypothesis
    # services. Dedupe while preserving rank order (root_cause first so
    # ties resolve in its favour).
    candidates: list[str] = [root_cause]
    for h in sorted(hypotheses, key=lambda x: x.get("confidence", 0), reverse=True):
        svc = h.get("service") or ""
        if svc and svc not in candidates:
            candidates.append(svc)
        if len(candidates) >= 4:
            break

    def _critical_count(svc: str) -> int:
        """Count sweep-evidence entries flagged critical for a service."""
        count = 0
        for e in evidence:
            if not isinstance(e, dict):
                continue
            args = e.get("args", {})
            if not isinstance(args, dict):
                continue
            if not args.get("pre_gathered"):
                # Only sweep entries carry a reliable 'critical' flag.
                continue
            if not args.get("critical"):
                continue
            target = args.get("service_name") or args.get("service_filter") or ""
            if target == svc:
                count += 1
        return count

    scores = {svc: _critical_count(svc) for svc in candidates}
    current_score = scores.get(root_cause, 0)

    # Find the best alternative: candidate with the most CRITICAL signals,
    # strictly greater than the current root_cause's score. Tie goes to
    # the current root_cause (no flip on ties).
    best_alt: str | None = None
    best_score = current_score
    for svc, score in scores.items():
        if svc == root_cause:
            continue
        if score > best_score:
            best_alt = svc
            best_score = score

    if best_alt is None:
        # Current root_cause is the strongest candidate — pass through.
        msg = (
            f"Knockout check: root_cause={root_cause} has "
            f"{current_score} CRITICAL signal(s) across sweep evidence; "
            f"no alternative top-3 candidate has more. Keeping root_cause."
        )
        return {"messages": [AIMessage(content=msg)]}

    # Flip root_cause to the strictly-better alternative. Bump confidence
    # moderately (not to 0.75, since we're not in the CRITICAL-override
    # band) to reflect the additional evidence.
    new_confidence = min(0.65, max(confidence, 0.45) + 0.10 * (best_score - current_score))
    msg = (
        f"Knockout flip: {root_cause} had {current_score} CRITICAL "
        f"signal(s), but {best_alt} has {best_score}. Swapping root_cause "
        f"to {best_alt} with confidence {new_confidence:.0%}."
    )
    logger.info(msg)
    return {
        "messages": [AIMessage(content=msg)],
        "root_cause": best_alt,
        "root_cause_confidence": new_confidence,
    }


def generate_report_node(state: AgentState) -> dict[str, Any]:
    """Generate the final structured RCA report.

    Two-stage rendering: structural fields (incident title, timestamp,
    severity, root cause, confidence, causal graph ASCII, counterfactual)
    are substituted in Python via ``.format()``. Free-text fields (summary,
    evidence chain, immediate/long-term actions, runbook refs) are left as
    sentinel tokens and filled by the LLM — which returns a JSON object
    keyed by sentinel name. This eliminates the bug where the LLM would
    leave literal ``{causal_graph_ascii}`` in its output.
    """
    llm = _get_llm()

    alert = state.get("alert", {})
    causal = state.get("causal_graph", {}) or {}
    evidence = state.get("evidence", [])
    hypotheses = state.get("hypotheses", [])
    root_cause = state.get("root_cause", "unknown")
    confidence = state.get("root_cause_confidence", 0.0)
    runbooks = state.get("relevant_runbooks", [])

    # Stage 1: Python-side substitution of structural fields.
    # Sentinel tokens preserve the free-text slots for the LLM to fill in
    # stage 2. Sentinels are chosen so they are extremely unlikely to appear
    # in real report content.
    sentinels = {
        "summary": "__OPS_SUMMARY__",
        "evidence_chain": "__OPS_EVIDENCE_CHAIN__",
        "root_cause_component": "__OPS_ROOT_COMPONENT__",
        "root_cause_issue": "__OPS_ROOT_ISSUE__",
        "immediate_actions": "__OPS_IMMEDIATE_ACTIONS__",
        "longterm_actions": "__OPS_LONGTERM_ACTIONS__",
        "relevant_docs": "__OPS_RELEVANT_DOCS__",
    }

    try:
        scaffold = RCA_REPORT_TEMPLATE.format(
            incident_title=alert.get("title", "Unknown Incident"),
            timestamp=alert.get("timestamp", "unknown"),
            severity=alert.get("severity", "unknown"),
            confidence=f"{confidence * 100:.0f}",
            root_cause_service=root_cause,
            causal_graph_ascii=causal.get("graph_ascii", "N/A") or "N/A",
            counterfactual_explanation=causal.get("counterfactual", "N/A") or "N/A",
            **sentinels,
        )
    except (KeyError, IndexError, ValueError) as exc:
        logger.exception("Report template structural format failed: %s", exc)
        scaffold = RCA_REPORT_TEMPLATE  # best-effort fall-back

    # Stage 2: Ask the LLM to emit ONLY a JSON object with the free-text
    # fields. This keeps the LLM output small and structured, and side-steps
    # any attempt by it to (re-)rewrite the structural scaffolding.
    prompt = (
        "Produce the free-text sections of the RCA report below as a JSON "
        "object. DO NOT reproduce the scaffolding — only emit the JSON.\n\n"
        "Required fields (all string values):\n"
        "  - summary:              2-4 sentence executive summary of the incident.\n"
        "  - evidence_chain:       bullet list (markdown) of chronological evidence.\n"
        "  - root_cause_component: the affected component within the root cause "
        "service (e.g. 'gRPC server', 'Redis connection pool').\n"
        "  - root_cause_issue:     one-line technical description of the issue.\n"
        "  - immediate_actions:    numbered list (markdown) of immediate actions.\n"
        "  - longterm_actions:     numbered list (markdown) of long-term actions.\n"
        "  - relevant_docs:        bullet list of relevant runbook / doc references.\n\n"
        f"Context:\n"
        f"- Incident title: {alert.get('title', 'Unknown Incident')}\n"
        f"- Severity: {alert.get('severity', 'unknown')}\n"
        f"- Root cause service: {root_cause}\n"
        f"- Root cause confidence: {confidence * 100:.0f}%\n"
        f"- Causal graph:\n{causal.get('graph_ascii', 'N/A')}\n"
        f"- Counterfactual: {causal.get('counterfactual', 'N/A')}\n"
        f"- Evidence collected: {json.dumps(evidence, default=str)}\n"
        f"- Hypotheses: {json.dumps(hypotheses, default=str)}\n"
        f"- Runbooks: {json.dumps(runbooks, default=str)}\n\n"
        "Return ONLY the JSON object. No prose before or after."
    )

    response = llm.invoke(state["messages"] + [HumanMessage(content=prompt)])
    # Gemini 3 returns list-of-parts; _extract_text normalises.
    raw = _extract_text(response)

    # Stage 3: Parse the JSON and substitute each sentinel. If JSON parsing
    # fails, use the raw content as the summary so we don't lose all the
    # LLM's work when it outputs prose instead of structured JSON.
    free_text = _parse_report_fields(raw) if raw else {}
    if not free_text and raw:
        # Best-effort fallback: put the raw LLM output into the summary
        # slot (trimmed to a reasonable length) so the report isn't empty.
        free_text = {"summary": raw.strip()[:1500]}
    rca_report = scaffold
    for field, token in sentinels.items():
        rca_report = rca_report.replace(token, free_text.get(field) or "N/A")

    # Final sanitization: any leftover {placeholder} is replaced with "N/A".
    rca_report = re.sub(r"\{[a-z_]+\}", "N/A", rca_report)

    # Extract recommended actions from the report
    actions = _extract_actions(rca_report)

    return {
        "messages": [response],
        "rca_report": rca_report,
        "recommended_actions": actions,
    }


def _parse_report_fields(content: str) -> dict[str, str]:
    """Best-effort parse a JSON object of free-text report fields.

    Mirrors the three-tier strategy used in ``_parse_hypotheses``: strip
    markdown code fences, extract the first JSON object, coerce every value
    to a string.
    """
    cleaned = re.sub(r"```(?:json)?\s*(.*?)\s*```", r"\1", content, flags=re.DOTALL)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end < start:
        return {}
    try:
        parsed = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {k: str(v) for k, v in parsed.items()}


# ── Routing ──────────────────────────────────────────────────────────────


def should_continue(state: AgentState) -> str:
    """Routing function: return 'continue' to loop, 'end' to generate report."""
    if state.get("tool_calls_remaining", 0) <= 0:
        return "end"
    if state.get("root_cause_confidence", 0.0) >= 0.7:
        return "end"
    return "continue"


# ── Graph assembly ───────────────────────────────────────────────────────


def build_graph() -> Any:
    """Build and compile the OpsAgent investigation StateGraph."""
    graph = StateGraph(AgentState)

    graph.add_node("analyze_context", analyze_context_node)
    graph.add_node("sweep_probes", sweep_probes_node)
    graph.add_node("form_hypothesis", form_hypothesis_node)
    graph.add_node("gather_evidence", gather_evidence_node)
    graph.add_node("analyze_causation", analyze_causation_node)
    graph.add_node("knockout", knockout_node)
    graph.add_node("generate_report", generate_report_node)

    graph.add_edge(START, "analyze_context")
    graph.add_edge("analyze_context", "sweep_probes")
    graph.add_edge("sweep_probes", "form_hypothesis")
    graph.add_edge("form_hypothesis", "gather_evidence")
    graph.add_edge("gather_evidence", "analyze_causation")
    # Knockout sits on the "end" branch — it only runs once, right before
    # report generation, not on every investigation loop iteration.
    graph.add_conditional_edges(
        "analyze_causation",
        should_continue,
        {
            "continue": "form_hypothesis",
            "end": "knockout",
        },
    )
    graph.add_edge("knockout", "generate_report")
    graph.add_edge("generate_report", END)

    return graph.compile()


# ── Helpers ──────────────────────────────────────────────────────────────


def _parse_hypotheses(content: str, existing: list[dict]) -> list[dict]:
    """Best-effort parse hypotheses JSON from LLM response text.

    Three-tier extraction strategy:
    1. Strip markdown code fences, then extract JSON array
    2. Raw bracket extraction (original approach)
    3. Regex fallback: extract service names from text patterns
    """
    # Tier 1: Strip markdown code fences (```json ... ```)
    cleaned = re.sub(r"```(?:json)?\s*(.*?)\s*```", r"\1", content, flags=re.DOTALL)

    # Tier 2: Extract JSON array from cleaned text
    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start != -1 and end != -1 and end > start:
        try:
            parsed = json.loads(cleaned[start : end + 1])
            if isinstance(parsed, list) and parsed:
                return parsed
        except json.JSONDecodeError:
            pass

    # Tier 3: Regex fallback — extract service names from patterns like
    # "service": "cartservice" or **cartservice** is the root cause
    known_services = {
        "cartservice",
        "checkoutservice",
        "currencyservice",
        "frontend",
        "paymentservice",
        "productcatalogservice",
        "redis",
    }
    found: list[str] = []
    for svc in known_services:
        if svc in content.lower() and svc not in found:
            found.append(svc)

    if found:
        return [
            {
                "service": svc,
                "confidence": 0.5,
                "reason": "extracted from text",
                "status": "investigating",
            }
            for svc in found[:5]
        ]

    return existing


def _extract_actions(report: str) -> list[str]:
    """Extract recommended actions from the RCA report text."""
    actions: list[str] = []
    in_actions = False
    for line in report.split("\n"):
        stripped = line.strip()
        if "RECOMMENDED ACTIONS" in stripped:
            in_actions = True
            continue
        if in_actions and stripped.startswith(("─", "═")):
            if actions:
                break
            continue
        if in_actions and stripped and stripped[0].isdigit():
            # Remove leading number and dot
            action = stripped.lstrip("0123456789.").strip()
            if action:
                actions.append(action)
    return actions
