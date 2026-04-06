"""RCA report template for structured investigation output.

Used by the generate_report node to format the final root cause analysis.
"""

RCA_REPORT_TEMPLATE = """\
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
