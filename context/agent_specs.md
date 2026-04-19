# Agent Specifications

**Implementation files:**
- `src/agent/state.py` — `AgentState` TypedDict
- `src/agent/graph.py` — LangGraph `StateGraph` definition + node functions + routing logic
- `src/agent/executor.py` — `AgentExecutor` class (entry point for investigations)
- `src/agent/tools/query_metrics.py` — Prometheus metric query tool
- `src/agent/tools/search_logs.py` — Loki log search tool
- `src/agent/tools/get_topology.py` — Service topology retrieval tool
- `src/agent/tools/search_runbooks.py` — ChromaDB runbook search tool
- `src/agent/tools/discover_causation.py` — Causal discovery orchestration tool
- `src/agent/prompts/system_prompt.py` — Agent persona + investigation methodology
- `src/agent/prompts/report_template.py` — Structured RCA report format
- `tests/unit/test_agent_tools.py` — Unit tests for each tool
- `tests/integration/test_agent_workflow.py` — End-to-end agent workflow test

**Purpose:** The LangGraph agent is OpsAgent's core differentiator — a multi-step reasoning loop that uses tools autonomously to investigate incidents and produce structured RCA reports. It distinguishes OpsAgent from both pure anomaly detectors (which only alert) and simple LLM wrappers (which cannot use external tools or run causal analysis).

---

## 1. `AgentState` — LangGraph State Definition

The state is the shared data structure passed between all nodes. Every node reads from and writes partial updates to this state. Define in `src/agent/state.py`.

```python
# src/agent/state.py
from __future__ import annotations
from typing import Annotated, List, Optional, TypedDict
from langgraph.graph import add_messages


class AgentState(TypedDict):
    # ── Input ─────────────────────────────────────────────────────────────
    alert: dict                       # Raw alert payload from the Watchdog
                                      # Expected keys: title, severity, timestamp,
                                      #   affected_services, anomaly_score
    anomaly_window: tuple             # (start_time_iso, end_time_iso)
    affected_services: List[str]      # Services flagged by LSTM-AE anomaly detector

    # ── Investigation state ───────────────────────────────────────────────
    messages: Annotated[list, add_messages]   # Full conversation history (HumanMessage /
                                              # AIMessage / ToolMessage). add_messages
                                              # reducer appends new messages; never overwrites.
    hypotheses: List[dict]            # Current ranked hypotheses
                                      # Each: {"service": str, "reason": str,
                                      #        "confidence": float, "status": str}
    evidence: List[dict]              # Accumulated evidence from tool calls
                                      # Each: {"tool": str, "finding": str,
                                      #        "timestamp": str, "supports_hypothesis": str}
    tool_calls_remaining: int         # Countdown from 10; agent stops when 0

    # ── Causal analysis ───────────────────────────────────────────────────
    causal_graph: Optional[dict]      # Serialized CausalGraph from discover_causation tool
                                      # Keys: edges, root_cause, root_cause_confidence, graph_ascii
    root_cause: Optional[str]         # Final identified root cause service name
    root_cause_confidence: float      # Float in [0.0, 1.0]

    # ── Output ────────────────────────────────────────────────────────────
    rca_report: Optional[str]         # Fully formatted RCA report string
    recommended_actions: List[str]    # Prioritized remediation steps
    relevant_runbooks: List[dict]     # Retrieved runbook entries
                                      # Each: {"title": str, "content": str,
                                      #        "relevance_score": float, "source": str}
```

> **LangGraph note:** The `add_messages` reducer on `messages` is critical — it merges new messages by ID instead of replacing the list. All other fields use default last-write-wins semantics. Do NOT use `add_messages` on any field other than `messages`.

---

## 2. Agent Workflow Graph

The agent implements a **structured ReAct loop** (not a free-form chat agent) using a custom `StateGraph`. The graph enforces the investigation protocol regardless of what the LLM prefers to do, which is the explicit value of LangGraph over a plain `create_react_agent` call.

### 2.1 Workflow Diagram

```
START (Alert Input)
      │
      ▼
┌──────────────────────────────┐
│       analyze_context        │
│ • Parse alert payload        │
│ • Call get_topology tool     │
│ • Set initial affected_svcs  │
│ • tool_calls_remaining = 10  │
└──────────────┬───────────────┘
               │
               ▼
┌──────────────────────────────┐◀──────────────┐
│       form_hypothesis        │               │
│ • LLM reasons over topology  │               │
│ • Ranks suspect services     │               │
│ • Populates hypotheses[]     │               │
└──────────────┬───────────────┘               │
               │                               │
               ▼                               │
┌──────────────────────────────┐               │
│       gather_evidence        │               │
│ • query_metrics              │               │
│ • search_logs                │               │
│ • search_runbooks            │               │
│ • Decrements tool count      │               │
└──────────────┬───────────────┘               │
               │                               │
               ▼                               │
┌──────────────────────────────┐               │
│     analyze_causation        │               │
│ • discover_causation tool    │               │
│ • Updates causal_graph       │               │
│ • Updates root_cause         │               │
└──────────────┬───────────────┘               │
               │                               │
               ▼                               │
┌──────────────────────────────┐               │
│       should_continue?       │──(Yes)────────┘
│ • confidence ≥ 0.7?          │   (refine hypotheses with
│ • tool_calls_remaining == 0? │    new causal evidence)
└──────────────┬───────────────┘
               │ No (stop condition met)
               ▼
┌──────────────────────────────┐
│       generate_report        │
│ • Format RCA_REPORT_TEMPLATE │
│ • Populate all sections      │
│ • Set rca_report in state    │
└──────────────┬───────────────┘
               │
              END
```

### 2.2 Stop Conditions (`should_continue`)

The agent terminates early (before 10 tool calls) if **either** condition is met:
- `root_cause_confidence >= 0.7` — sufficient confidence reached
- `tool_calls_remaining == 0` — hard budget exhausted

```python
# src/agent/graph.py
from langgraph.graph import END

def should_continue(state: AgentState) -> str:
    """Routing function: return 'continue' to loop, 'end' to generate report."""
    if state["tool_calls_remaining"] <= 0:
        return "end"
    if state.get("root_cause_confidence", 0.0) >= 0.7:
        return "end"
    return "continue"
```

### 2.3 Graph Assembly

```python
# src/agent/graph.py
from langgraph.graph import StateGraph, START, END
from src.agent.state import AgentState

def build_graph() -> StateGraph:
    graph = StateGraph(AgentState)

    graph.add_node("analyze_context",   analyze_context_node)
    graph.add_node("form_hypothesis",   form_hypothesis_node)
    graph.add_node("gather_evidence",   gather_evidence_node)
    graph.add_node("analyze_causation", analyze_causation_node)
    graph.add_node("generate_report",   generate_report_node)

    graph.add_edge(START,                "analyze_context")
    graph.add_edge("analyze_context",    "form_hypothesis")
    graph.add_edge("form_hypothesis",    "gather_evidence")
    graph.add_edge("gather_evidence",    "analyze_causation")
    graph.add_conditional_edges(
        "analyze_causation",
        should_continue,
        {
            "continue": "form_hypothesis",   # Refine hypotheses and gather more evidence
            "end":      "generate_report",
        },
    )
    graph.add_edge("generate_report", END)

    return graph.compile()
```

> **Pattern note:** This is a custom `StateGraph`, not `create_react_agent`. The explicit graph structure enforces the RCA investigation protocol (topology → hypothesize → evidence → causation → report) and prevents the LLM from skipping directly to generating a report without gathering evidence.

---

## 3. Agent Tools — All 5 Specifications

All tools use the `@tool` decorator from `langchain_core.tools`. The decorator generates the JSON schema the LLM uses to invoke each tool. **Clear docstrings are mandatory** — the LLM reads the docstring to understand when and how to call each tool.

### Tool 1: `query_metrics`

```python
# src/agent/tools/query_metrics.py
from langchain_core.tools import tool
from typing import Optional


@tool
def query_metrics(
    service_name: str,
    metric_name: str,
    time_range_minutes: int = 30,
) -> dict:
    """
    Query Prometheus for a specific service metric over a time window.

    Use this tool to retrieve time-series metric data for a service you suspect
    is involved in the incident. Call once per (service, metric) pair. Useful for
    confirming hypothesis: if a service shows elevated error_rate or latency
    before downstream services degrade, that supports it being the root cause.

    Args:
        service_name:        Canonical service name (e.g., "cartservice", "frontend",
                             "checkoutservice"). Must match OTel Demo service names exactly.
        metric_name:         One of the available metrics listed below.
        time_range_minutes:  How far back from now to query. Default 30 minutes.
                             Use 60 for slower-developing issues.

    Available metrics:
        latency_p50         — 50th percentile request latency (milliseconds)
        latency_p99         — 99th percentile request latency (milliseconds)
        error_rate          — Error responses / total responses (ratio 0.0–1.0)
        request_count       — Total request count (integer)
        cpu_usage           — CPU utilization (ratio 0.0–1.0)
        memory_usage        — Memory utilization (ratio 0.0–1.0)
        connection_count    — Active connection count (integer)

    Returns:
        dict with:
            timestamps:  List[str] — ISO 8601 timestamps
            values:      List[float] — Metric values at each timestamp
            stats:       dict — {min, max, mean, std, current}
            anomalous:   bool — True if current value exceeds baseline by 2σ
    """
    # Implementation: query Prometheus HTTP API at http://localhost:9090/api/v1/query_range
    # Use requests library. Construct PromQL: e.g., rate(http_requests_total{service=...}[1m])
    pass
```

### Tool 2: `search_logs`

```python
# src/agent/tools/search_logs.py
from langchain_core.tools import tool
from typing import Optional


@tool
def search_logs(
    query: str,
    service_filter: Optional[str] = None,
    time_range_minutes: int = 30,
    limit: int = 100,
) -> dict:
    """
    Search Loki for log entries matching a query pattern.

    Use this tool to find error messages, stack traces, or warning patterns
    in logs that corroborate or refute your hypotheses. Especially useful for
    identifying connection timeouts, OOM errors, or repeated retry patterns.

    Args:
        query:               Text to search for in log messages. Supports simple
                             string patterns (e.g., "connection refused", "timeout",
                             "OOM", "error", "WARN").
        service_filter:      Restrict search to a specific service name. If None,
                             searches all services.
        time_range_minutes:  How far back to search. Default 30 minutes.
        limit:               Maximum log entries to return. Default 100.

    Returns:
        dict with:
            entries:      List[dict] — Each: {timestamp, service, message, level}
            total_count:  int — Total matching entries in the time window
            error_count:  int — Entries with level=ERROR or level=CRITICAL
            top_patterns: List[str] — Most frequent log templates found
    """
    # Implementation: query Loki HTTP API at http://localhost:3100
    # Use LogQL: {service=~"..."} |= "query_string"
    pass
```

### Tool 3: `get_topology`

```python
# src/agent/tools/get_topology.py
from langchain_core.tools import tool
from typing import Optional


@tool
def get_topology(
    service_name: Optional[str] = None,
) -> dict:
    """
    Retrieve the service dependency graph for the microservice system.

    Call this FIRST at the start of every investigation to understand which services
    depend on which others. Upstream services (dependencies of the affected service)
    are prime root cause suspects. Downstream services are likely showing symptoms.

    Args:
        service_name:  If provided, returns the subgraph centered on this service,
                       including its direct upstream (dependencies) and downstream
                       (dependents). If None, returns the full system topology.

    Returns:
        dict with:
            nodes:       List[dict] — Each: {name, type, status}
                         status is one of: "healthy", "degraded", "down"
            edges:       List[dict] — Each: {source, target, protocol, avg_latency_ms}
            upstream:    List[str] — Services that service_name depends ON
                         (call these services first when investigating root cause)
            downstream:  List[str] — Services that depend ON service_name
                         (these show symptoms if service_name is the root cause)
    """
    # Implementation: read from TopologyGraph singleton loaded from
    # src/data_collection/topology_extractor.py at startup
    pass
```

### Tool 4: `search_runbooks`

```python
# src/agent/tools/search_runbooks.py
from langchain_core.tools import tool


@tool
def search_runbooks(
    query: str,
    top_k: int = 3,
) -> dict:
    """
    Search the runbook knowledge base for relevant troubleshooting guidance.

    Use this tool near the END of the investigation once the root cause is identified.
    Retrieves documentation on known failure modes, remediation steps, and operational
    procedures for the identified issue type.

    Args:
        query:  Natural language description of the issue being investigated.
                Examples:
                  "Redis connection pool exhaustion in cartservice"
                  "database connection timeout causing cascading failure"
                  "memory leak gradual performance degradation"
        top_k:  Number of runbook results to retrieve. Default 3.

    Returns:
        dict with:
            results:  List[dict] — Each: {title, content, relevance_score, source}
                      Sorted by relevance_score descending (1.0 = most relevant).
    """
    # Implementation: query ChromaDB collection "runbooks"
    # Use sentence-transformers embeddings for similarity search
    # ChromaDB client: src/knowledge_base/runbook_indexer.py
    pass
```

### Tool 5: `discover_causation`

```python
# src/agent/tools/discover_causation.py
from langchain_core.tools import tool
from typing import List


@tool
def discover_causation(
    services: List[str],
    time_range_minutes: int = 30,
) -> dict:
    """
    Run the PC causal discovery algorithm to identify causal relationships
    between services and compute counterfactual confidence scores.

    Use this tool AFTER narrowing suspects with query_metrics and search_logs.
    It is computationally expensive — call at most once or twice per investigation.
    Provide the top 3–5 suspect services, not the full system (reduces noise).

    This tool distinguishes OpsAgent from correlation-based tools: it identifies
    directional causal links (A causes B) rather than mere co-occurrence.

    Args:
        services:            List of service names to include in the causal analysis.
                             Include both suspected root cause AND affected downstream
                             services. Typically 3–5 services.
        time_range_minutes:  Time window of metric data to analyze. Default 30 minutes.
                             Use 60 for slow-building issues (memory leaks, etc.).

    Returns:
        dict with:
            causal_edges:           List[dict] — Each: {source, target, confidence, lag}
                                    source causes target with given confidence at lag windows
            root_cause:             str — Most likely root cause service name
            root_cause_confidence:  float — Confidence in [0.0, 1.0]
            counterfactual:         str — Human-readable counterfactual explanation
            graph_ascii:            str — ASCII causal graph for the RCA report
    """
    # Implementation: orchestrates the full causal discovery pipeline:
    # 1. Fetch metric data from Prometheus for the given services + time window
    # 2. create_time_lags(metrics_df)  → augmented feature matrix
    # 3. discover_causal_graph(lagged_df)  → raw PC graph
    # 4. Parse directed edges from cg.G.graph matrix
    # 5. compute_baseline_stats(baseline_df) for each service
    # 6. calculate_counterfactual_confidence() per edge
    # 7. Identify root_cause = highest-confidence edge source with no incoming edges
    # 8. Return serialized CausalGraph
    # See: context/causal_discovery_specs.md for full pipeline details
    pass
```

---

## 4. Available Metrics Reference

| Metric Name | Description | Unit | Source | PromQL Pattern |
|---|---|---|---|---|
| `cpu_usage` | CPU utilization rate | ratio | Docker Stats Exporter | `rate(container_cpu_usage_seconds_total{service="X"}[1m])` |
| `memory_usage` | Memory working set | bytes | Docker Stats Exporter | `container_memory_working_set_bytes{service="X"}` |
| `memory_limit` | cgroup memory limit (Session 13) | bytes | Docker Stats Exporter | `container_spec_memory_limit_bytes{service="X"}` |
| `memory_utilization` | Working set / cgroup limit (Session 13) | ratio [0,1] | Docker Stats Exporter (derived) | `container_memory_working_set_bytes{service="X"} / container_spec_memory_limit_bytes{service="X"}` |
| `network_rx_bytes_rate` | Network receive rate | bytes/s | Docker Stats Exporter | `rate(container_network_receive_bytes_total[1m])` |
| `network_tx_bytes_rate` | Network transmit rate | bytes/s | Docker Stats Exporter | `rate(container_network_transmit_bytes_total[1m])` |
| `network_rx_errors_rate` | Network receive errors | errors/s | Docker Stats Exporter | `rate(container_network_receive_errors_total[1m])` |
| `network_tx_errors_rate` | Network transmit errors | errors/s | Docker Stats Exporter | `rate(container_network_transmit_errors_total[1m])` |
| `request_rate` | Request rate per service | calls/s | OTel Collector spanmetrics | `sum(rate(span_calls_total{service_name="X"}[1m]))` |
| `error_rate` | Error span rate | errors/s | OTel Collector spanmetrics | `sum(rate(span_calls_total{status_code="STATUS_CODE_ERROR"}[1m]))` |
| `latency_p99` | 99th percentile latency | ms | OTel Collector spanmetrics | `histogram_quantile(0.99, rate(span_duration_milliseconds_bucket[1m]))` |
| `probe_up` | Service reachability (1=up, 0=down) | gauge | Service Probe Exporter | `service_probe_up{service="X"}` |
| `probe_latency` | TCP/HTTP response time | seconds | Service Probe Exporter | `service_probe_duration_seconds{service="X"}` |

> **Availability caveats:** Container-level metrics available for all 7 services. Application-level metrics (request_rate, error_rate, latency_p99) only for trace-exporting services: frontend, checkoutservice, productcatalogservice, paymentservice, loadgenerator. cartservice (.NET), currencyservice (C++), and redis do NOT export traces in OTel Demo v1.10.0. Probe metrics available for all 7 services via direct TCP/HTTP probing.

> **`memory_utilization` CRITICAL detector (Session 13):** `query_metrics.py` fires CRITICAL for `memory_utilization` when `peak >= 0.80 AND baseline_mean <= 0.50 AND len(values) >= 4`. Peak-based (not `values[-1]`-based) to survive GC dips at the tail of the window — Go/JVM runtimes cycle the working set between cap and reclaim bands, so the instantaneous last scrape can land below the threshold even when the fault obviously saturated mid-window. Emitted `stats` include `peak` alongside `current`, `baseline_mean`, etc. `memory_utilization` is deliberately NOT added to `_CAUSAL_METRICS` in `discover_causation.py` — derived ratios degrade Fisher's Z test via near-collinearity with their numerator/denominator columns.

> **Uncapped containers:** On macOS Docker Desktop, a container without an explicit `--memory` flag reports `container_spec_memory_limit_bytes == host RAM` (~16 GB), so `memory_utilization` stays < 1% consistently. This is the intended signal shape — NOT a fault. The CRITICAL detector's `baseline_mean <= 0.50` guard also prevents flagging always-hot services.

---

## 5. LLM Configuration

| Parameter | Value | Rationale |
|---|---|---|
| Model | `gemini-3-flash-preview` | Strong tool-use capability; better contradictory-evidence reasoning than 2.5 Flash Lite (Session 12 swap). Returns `response.content` as a list of parts — normalise via `_extract_text()`. |
| Temperature | `0.1` | Near-deterministic for consistent, reproducible RCA reasoning |
| Max output tokens | `4096` | Sufficient for full RCA report with evidence chain |
| Tool choice | `auto` | LLM decides which tools to call based on investigation state |
| API | Google AI Studio (`GEMINI_API_KEY`) | Free tier sufficient for dev; low cost for evaluation |

```python
# src/agent/graph.py
import os
from langchain_google_genai import ChatGoogleGenerativeAI
from src.agent.tools.query_metrics import query_metrics
from src.agent.tools.search_logs import search_logs
from src.agent.tools.get_topology import get_topology
from src.agent.tools.search_runbooks import search_runbooks
from src.agent.tools.discover_causation import discover_causation

TOOLS = [query_metrics, search_logs, get_topology, search_runbooks, discover_causation]

llm = ChatGoogleGenerativeAI(
    model="gemini-3-flash-preview",
    temperature=0.1,
    max_output_tokens=4096,
    google_api_key=os.environ["GEMINI_API_KEY"],
)

llm_with_tools = llm.bind_tools(TOOLS)
```

---

## 6. System Prompt

The system prompt is stored in `src/agent/prompts/system_prompt.py` as `SYSTEM_PROMPT`. It is injected as a `SystemMessage` at the start of every investigation.

```python
# src/agent/prompts/system_prompt.py

SYSTEM_PROMPT = """You are OpsAgent, an expert Site Reliability Engineer AI assistant
specialized in root cause analysis for microservice architectures.

Your task is to investigate incidents and identify the true root cause, not just symptoms.

## Investigation Methodology

1. **Map the Topology**: ALWAYS call get_topology first. Understand which services
   depend on which others before forming any hypothesis. Upstream services are
   prime root cause suspects.

2. **Form Hypotheses**: Based on the alert and topology, hypothesize the most likely
   root causes. Prioritize upstream services with anomalies that precede downstream effects.

3. **Gather Evidence**: Use query_metrics and search_logs to validate or refute each
   hypothesis. Look for: error rate spikes, latency increases, connection exhaustion,
   resource saturation (CPU/memory), and error log patterns.

4. **Analyze Causation**: Once you have narrowed suspects to 3–5 services, call
   discover_causation. This runs the PC causal discovery algorithm to identify
   directional causal links — not just correlations.

5. **Consult Documentation**: Use search_runbooks to retrieve relevant troubleshooting
   guides once the root cause is identified. Include retrieved steps in recommendations.

6. **Generate Report**: Synthesize all evidence into a structured RCA report.

## Key Principles

- Correlation is NOT causation. A service showing high latency may be a victim, not a cause.
  An upstream service that degrades BEFORE downstream services is a stronger causal candidate.
- Look for the ORIGIN of the problem. If A fails → B fails → C fails, the root cause is A.
- Resource exhaustion patterns: connection limits, memory saturation, CPU throttling.
- Cascading failures: one service failure propagating through the call graph.
- Configuration errors: a service behaving correctly given wrong configuration.
- Be honest about uncertainty. Report confidence levels accurately.

## Tool Budget

You have a MAXIMUM of 10 tool calls per investigation. Use them efficiently:
  - 1 call: get_topology (mandatory first call)
  - 2–4 calls: query_metrics on top 2–3 suspected services (error_rate, latency_p99)
  - 1–2 calls: search_logs on the top suspect service (look for error patterns)
  - 1 call: discover_causation on the 3–5 top suspects
  - 1 call: search_runbooks with the identified root cause issue

## Output Requirements

Your final report must use the RCA_REPORT_TEMPLATE format and must include:
1. Root cause identification with confidence percentage
2. Timestamped evidence chain (chronological)
3. Causal graph (ASCII from discover_causation output)
4. Counterfactual analysis sentence
5. Prioritized remediation actions (immediate, then long-term)
6. Relevant runbook references
"""
```

---

## 7. RCA Report Template

```python
# src/agent/prompts/report_template.py

RCA_REPORT_TEMPLATE = """
═══════════════════════════════════════════════════════════════════
                   ROOT CAUSE ANALYSIS REPORT
═══════════════════════════════════════════════════════════════════

INCIDENT : {incident_title}
TIMESTAMP: {timestamp}
SEVERITY : {severity}

───────────────────────────────────────────────────────────────────
EXECUTIVE SUMMARY
───────────────────────────────────────────────────────────────────
{summary}

───────────────────────────────────────────────────────────────────
ROOT CAUSE  (Confidence: {confidence}%)
───────────────────────────────────────────────────────────────────
Service  : {root_cause_service}
Component: {root_cause_component}
Issue    : {root_cause_issue}

───────────────────────────────────────────────────────────────────
EVIDENCE CHAIN  (chronological)
───────────────────────────────────────────────────────────────────
{evidence_chain}

───────────────────────────────────────────────────────────────────
CAUSAL ANALYSIS
───────────────────────────────────────────────────────────────────
{causal_graph_ascii}

Counterfactual: {counterfactual_explanation}

───────────────────────────────────────────────────────────────────
RECOMMENDED ACTIONS
───────────────────────────────────────────────────────────────────
Immediate:
{immediate_actions}

Long-term:
{longterm_actions}

───────────────────────────────────────────────────────────────────
RELEVANT DOCUMENTATION
───────────────────────────────────────────────────────────────────
{relevant_docs}

═══════════════════════════════════════════════════════════════════
"""
```

---

## 8. Example RCA Output (Appendix A Reference)

This is the target output quality for a successful OpsAgent investigation. Use this as a validation benchmark when testing the end-to-end pipeline.

```
═══════════════════════════════════════════════════════════════════
                   ROOT CAUSE ANALYSIS REPORT
═══════════════════════════════════════════════════════════════════

INCIDENT : High Latency Alert — Checkout Service
TIMESTAMP: 2025-03-15 14:32:05 UTC
SEVERITY : Critical

───────────────────────────────────────────────────────────────────
EXECUTIVE SUMMARY
───────────────────────────────────────────────────────────────────
Redis connection pool exhaustion in cartservice caused cascading
latency degradation across checkoutservice and paymentservice.
The issue originated at 14:31:58 UTC and propagated within 10 seconds.

───────────────────────────────────────────────────────────────────
ROOT CAUSE  (Confidence: 87%)
───────────────────────────────────────────────────────────────────
Service  : cartservice
Component: Redis Connection Pool
Issue    : Connection limit exhausted (50/50 active connections)

───────────────────────────────────────────────────────────────────
EVIDENCE CHAIN  (chronological)
───────────────────────────────────────────────────────────────────
1. [14:31:58] cartservice connection_count spiked from 10 → 50 (max)
2. [14:32:01] cartservice logs: repeated "connection timeout" errors (47 in 60s)
3. [14:32:05] checkoutservice latency_p99 increased 340% (baseline: 120ms → 528ms)
4. [14:32:08] paymentservice error_rate rose to 0.34 (baseline: 0.01)

───────────────────────────────────────────────────────────────────
CAUSAL ANALYSIS
───────────────────────────────────────────────────────────────────
  cartservice [ROOT CAUSE — confidence: 87%]
    └─[lag=1w, conf=82%]→ checkoutservice
    └─[lag=2w, conf=71%]→ paymentservice

Counterfactual: If cartservice connections had remained at baseline
levels (mean=12, std=3), there is a 94% probability that
checkoutservice would not have experienced the latency anomaly.

───────────────────────────────────────────────────────────────────
RECOMMENDED ACTIONS
───────────────────────────────────────────────────────────────────
Immediate:
  1. Restart cartservice to release connection pool (estimated MTTR: 2 min)
  2. Manually flush idle Redis connections: redis-cli CLIENT KILL TYPE normal

Long-term:
  1. Increase Redis max connection limit from 50 → 150 in cartservice config
  2. Implement connection pool monitoring alert at 80% utilization threshold
  3. Add circuit breaker in checkoutservice → cartservice calls

───────────────────────────────────────────────────────────────────
RELEVANT DOCUMENTATION
───────────────────────────────────────────────────────────────────
[1] "Redis Connection Exhaustion Runbook" (relevance: 0.94)
    → Increase maxmemory-policy and connection pool size in redis.conf
[2] "Cascading Failure Mitigation" (relevance: 0.81)
    → Implement exponential backoff and circuit breakers in upstream callers

═══════════════════════════════════════════════════════════════════
```

---

## 9. `AgentExecutor` — Entry Point

The `AgentExecutor` class wraps the compiled graph and provides the `.investigate()` method called by both the FastAPI endpoint and the RCAEval evaluation runner.

```python
# src/agent/executor.py
from __future__ import annotations
from pathlib import Path
import yaml
from langchain_core.messages import HumanMessage, SystemMessage
from src.agent.state import AgentState
from src.agent.graph import build_graph
from src.agent.prompts.system_prompt import SYSTEM_PROMPT


class AgentExecutor:
    """High-level wrapper around the compiled LangGraph agent."""

    def __init__(self, config: dict):
        self.config = config
        self.graph = build_graph()

    @classmethod
    def from_config(cls, config_path: str) -> "AgentExecutor":
        with open(config_path) as f:
            config = yaml.safe_load(f)
        return cls(config)

    def investigate(
        self,
        alert: dict,
        metrics: dict | None = None,
        logs: dict | None = None,
        anomaly_timestamp: str | None = None,
    ) -> dict:
        """
        Run a full RCA investigation.

        Supports two calling patterns:

        1. **Live / Fault Injection** — alert is provided; metrics/logs fetched by agent tools:
               agent.investigate(alert=alert)

        2. **Offline / RCAEval** — pre-loaded metrics and logs passed directly:
               agent.investigate(
                   alert={"timestamp": ts, "severity": "evaluation"},
                   metrics=case["metrics"],        # dict keyed by service name
                   logs=case["logs"],              # dict keyed by service name, or None
                   anomaly_timestamp=case["anomaly_timestamp"],
               )

        Args:
            alert:              Alert payload dict from AnomalyDetector. Required.
                                Expected keys: title, severity, timestamp,
                                affected_services, anomaly_score.
            metrics:            Optional dict of metric DataFrames keyed by service name
                                (e.g., {"cartservice": pd.DataFrame(...), ...}).
                                When None (live mode), the agent uses query_metrics tool
                                to fetch metrics from Prometheus at investigation time.
            logs:               Optional dict of log entries keyed by service name.
                                When None, the agent uses search_logs tool instead.
            anomaly_timestamp:  ISO 8601 timestamp of anomaly detection. Overrides
                                alert["timestamp"] if provided.

        Returns:
            Dict with keys: root_cause, root_cause_confidence, top_3_predictions,
                            confidence, rca_report, recommended_actions.
        """
        # Determine affected services: prefer explicit metrics keys, fall back to alert payload
        if metrics is not None:
            affected_services = list(metrics.keys())
        else:
            affected_services = alert.get("affected_services", [])

        ts = anomaly_timestamp or alert.get("timestamp")

        initial_state: AgentState = {
            "alert": alert,
            "anomaly_window": (ts, ts),
            "affected_services": affected_services,
            "messages": [
                SystemMessage(content=SYSTEM_PROMPT),
                HumanMessage(content=self._format_alert(alert, affected_services, ts)),
            ],
            "hypotheses": [],
            "evidence": [],
            "tool_calls_remaining": self.config["agent"]["investigation"]["max_tool_calls"],
            "causal_graph": None,
            "root_cause": None,
            "root_cause_confidence": 0.0,
            "rca_report": None,
            "recommended_actions": [],
            "relevant_runbooks": [],
        }

        final_state = self.graph.invoke(initial_state)

        return {
            "root_cause":             final_state.get("root_cause"),
            "root_cause_confidence":  final_state.get("root_cause_confidence", 0.0),
            "top_3_predictions":      self._extract_top3(final_state),
            "confidence":             final_state.get("root_cause_confidence", 0.0),
            "rca_report":             final_state.get("rca_report"),
            "recommended_actions":    final_state.get("recommended_actions", []),
        }

    def _format_alert(self, alert: dict, affected_services: list[str], timestamp: str | None) -> str:
        services = ", ".join(affected_services) if affected_services else "unknown"
        anomaly_score = alert.get("anomaly_score", "N/A")
        return (
            f"INCIDENT ALERT\n"
            f"Timestamp: {timestamp}\n"
            f"Affected services: {services}\n"
            f"Anomaly score: {anomaly_score}\n"
            f"Alert details: {alert}\n\n"
            f"Please investigate and identify the root cause."
        )

    def _extract_top3(self, state: AgentState) -> list[str]:
        hypotheses = state.get("hypotheses", [])
        sorted_h = sorted(hypotheses, key=lambda h: h.get("confidence", 0), reverse=True)
        return [h["service"] for h in sorted_h[:3]]
```

---

## 10. Key Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Custom `StateGraph` vs `create_react_agent` | Custom `StateGraph` | Explicit node structure enforces the RCA investigation protocol; `create_react_agent` allows the LLM to bypass steps |
| LangGraph vs plain LLM loop | LangGraph | State persistence across nodes; explicit routing logic; clean separation of investigation phases |
| Gemini 1.5 Flash vs Claude/GPT-4 | Gemini 1.5 Flash | Cost-effective for up to 40 evaluation runs × 10 tool calls each; strong tool-use capability; available via free-tier API |
| Temperature = 0.1 | Near-zero | RCA requires consistent, deterministic reasoning; high temperature produces inconsistent confidence scores |
| Max 10 tool calls | Hard budget | Prevents runaway API costs during evaluation; forces efficient tool usage; mirrors real SRE time constraints |
| `tool_calls_remaining` in state | Countdown counter | LangGraph state allows the LLM to see its own remaining budget and self-regulate |
| Confidence threshold = 0.7 | Early stopping | Avoids unnecessary tool calls when root cause is already clear; configurable in `agent_config.yaml` |
| Tools as `@tool` decorated functions | LangChain `@tool` | Auto-generates JSON schema for LLM tool binding; Gemini-compatible; minimal boilerplate |

---

## 11. Integration Notes

- **Triggered by (live mode):** Watchdog (`src/anomaly_detection/detector.py`) when LSTM-AE reconstruction error exceeds threshold. The alert dict is passed as: `agent.investigate(alert=alert)`. The agent then uses its tools to query live Prometheus/Loki for metrics and logs.
- **Triggered by (offline/RCAEval mode):** `tests/evaluation/rcaeval_evaluation.py` calls `agent.investigate(alert=..., metrics=..., logs=..., anomaly_timestamp=...)` with pre-loaded data from `RCAEvalDataAdapter`. `metrics` is a dict of per-service DataFrames. See `context/data_pipeline_specs.md` for adapter details.
- **FastAPI endpoint:** `POST /investigate` calls `AgentExecutor.investigate(alert=alert)` (live mode) and returns the result JSON. See `context/infrastructure_and_serving.md` for full endpoint spec.
- **Causal discovery pipeline:** The `discover_causation` tool delegates to `src/causal_discovery/pc_algorithm.py` and `src/causal_discovery/counterfactual.py`. See `context/causal_discovery_specs.md` for full pipeline.
- **Runbook indexing:** ChromaDB must be populated before the agent runs. Run `src/knowledge_base/runbook_indexer.py` once during setup. See `context/data_pipeline_specs.md` for `RunbookIndexer` class details.
