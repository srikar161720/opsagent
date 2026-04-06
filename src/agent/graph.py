"""LangGraph workflow for the OpsAgent investigation agent.

Defines a five-node StateGraph that enforces the RCA investigation
protocol: topology analysis, hypothesis formation, evidence gathering,
causal analysis, and report generation.
"""

from __future__ import annotations

import json
import logging
import os
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
        model="gemini-2.5-flash-lite",
        temperature=0.1,
        max_output_tokens=4096,
        google_api_key=api_key,
    )


# ── Node functions ───────────────────────────────────────────────────────


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

    # Try to parse hypotheses from the response
    content = response.content if isinstance(response.content, str) else ""
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
            tool_args = tc["args"]
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

    # Run causal discovery
    try:
        causal_result = discover_causation.invoke({"services": services, "time_range_minutes": 30})
    except Exception as exc:
        logger.exception("Causal discovery failed")
        causal_result = {
            "causal_edges": [],
            "root_cause": "unknown",
            "root_cause_confidence": 0.0,
            "counterfactual": f"Causal discovery failed: {exc}",
            "graph_ascii": "",
        }

    root_cause = causal_result.get("root_cause", "unknown")
    confidence = causal_result.get("root_cause_confidence", 0.0)

    causation_message = AIMessage(
        content=(
            f"Causal analysis complete.\n\n"
            f"**Root cause:** {root_cause}\n"
            f"**Confidence:** {confidence:.0%}\n\n"
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


def generate_report_node(state: AgentState) -> dict[str, Any]:
    """Generate the final structured RCA report using the LLM."""
    llm = _get_llm()

    alert = state.get("alert", {})
    causal = state.get("causal_graph", {}) or {}
    evidence = state.get("evidence", [])
    hypotheses = state.get("hypotheses", [])
    root_cause = state.get("root_cause", "unknown")
    confidence = state.get("root_cause_confidence", 0.0)
    runbooks = state.get("relevant_runbooks", [])

    prompt = (
        f"Generate the final RCA report using this template:\n\n"
        f"{RCA_REPORT_TEMPLATE}\n\n"
        f"Fill in all placeholders using the following information:\n"
        f"- Incident title: {alert.get('title', 'Unknown Incident')}\n"
        f"- Timestamp: {alert.get('timestamp', 'unknown')}\n"
        f"- Severity: {alert.get('severity', 'unknown')}\n"
        f"- Root cause service: {root_cause}\n"
        f"- Root cause confidence: {confidence * 100:.0f}%\n"
        f"- Causal graph ASCII: {causal.get('graph_ascii', 'N/A')}\n"
        f"- Counterfactual: {causal.get('counterfactual', 'N/A')}\n"
        f"- Evidence collected: {json.dumps(evidence, default=str)}\n"
        f"- Hypotheses: {json.dumps(hypotheses, default=str)}\n"
        f"- Runbooks: {json.dumps(runbooks, default=str)}\n\n"
        f"Provide the complete filled-in report. Include specific, actionable "
        f"remediation steps based on the root cause."
    )

    response = llm.invoke(state["messages"] + [HumanMessage(content=prompt)])

    rca_report = (
        response.content if isinstance(response.content, str) else "Report generation failed."
    )

    # Extract recommended actions from the report
    actions = _extract_actions(rca_report)

    return {
        "messages": [response],
        "rca_report": rca_report,
        "recommended_actions": actions,
    }


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
    graph.add_node("form_hypothesis", form_hypothesis_node)
    graph.add_node("gather_evidence", gather_evidence_node)
    graph.add_node("analyze_causation", analyze_causation_node)
    graph.add_node("generate_report", generate_report_node)

    graph.add_edge(START, "analyze_context")
    graph.add_edge("analyze_context", "form_hypothesis")
    graph.add_edge("form_hypothesis", "gather_evidence")
    graph.add_edge("gather_evidence", "analyze_causation")
    graph.add_conditional_edges(
        "analyze_causation",
        should_continue,
        {
            "continue": "form_hypothesis",
            "end": "generate_report",
        },
    )
    graph.add_edge("generate_report", END)

    return graph.compile()


# ── Helpers ──────────────────────────────────────────────────────────────


def _parse_hypotheses(content: str, existing: list[dict]) -> list[dict]:
    """Best-effort parse hypotheses JSON from LLM response text."""
    # Try to find JSON array in the response
    start = content.find("[")
    end = content.rfind("]")
    if start != -1 and end != -1 and end > start:
        try:
            parsed = json.loads(content[start : end + 1])
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            pass
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
