# General Troubleshooting Guide

## Overview

This runbook provides a structured methodology for investigating incidents in the OTel Demo microservice stack when the failure pattern does not clearly match a specific runbook (connection exhaustion, cascading failure, memory pressure, or high latency). Use this as a starting framework and pivot to a specialized runbook once the failure type is identified.

## Investigation Methodology

### Step 1: Understand the Alert Context

Before investigating, gather the initial context from the anomaly detection alert:

- **Which service(s) triggered the alert?** — Start with the primary alerting service
- **What type of anomaly was detected?** — Log pattern anomaly (LSTM-AE), metric anomaly, or both
- **When did the anomaly start?** — Note the exact timestamp for metric correlation
- **What is the anomaly confidence score?** — Higher scores indicate stronger signal

### Step 2: Map the Blast Radius

Use the service topology to understand which services could be affected:

- Use `get_topology` to retrieve the dependency graph for the alerting service
- Identify **upstream services** (dependencies — potential root causes)
- Identify **downstream services** (dependents — potential impact zones)
- Query health metrics for all services in the subgraph to determine which are affected and which are healthy

### Step 3: Query Service Health Metrics

For each service in the affected subgraph, check these key metrics:

- **CPU usage** — Is the service compute-bound? Compare against baseline
- **Memory usage** — Is memory growing steadily (leak) or at its limit (OOM risk)?
- **Error rate** — Are requests failing? What percentage?
- **Latency (p50 and p99)** — Are requests slow? Is it tail latency or systemic?
- **Network I/O** — Unusual traffic patterns (spike, drop to zero)?

### Step 4: Search Logs for Error Patterns

Search the affected service's logs for common error indicators:

- **Connection errors:** "timeout", "refused", "exhausted", "connection reset"
- **Resource errors:** "OOMKilled", "out of memory", "disk full", "no space"
- **Application errors:** "exception", "error", "fatal", "panic", "stack trace"
- **Performance warnings:** "slow", "deadline exceeded", "GC pause", "blocked"

Pay attention to the chronological order of errors — the first error often points to the root cause.

### Step 5: Identify the Root Cause Direction

Use causal discovery to determine the direction of failure propagation:

- Run `discover_causation` on the set of affected services
- The causal graph will show which service's anomaly preceded and likely caused others
- The root cause is typically the service with the earliest anomaly that has causal edges pointing to other affected services

### Step 6: Classify the Failure Pattern

Based on Steps 1-5, classify the incident into one of these categories and pivot to the appropriate specialized runbook:

| Pattern | Key Indicators | Runbook |
|---------|---------------|---------|
| **Connection exhaustion** | Connection count at max, timeout errors, single service affected | `connection_exhaustion.md` |
| **Cascading failure** | Multiple services failing sequentially, staggered timestamps | `cascading_failure.md` |
| **Memory pressure** | Steady memory climb, OOMKilled events, GC pauses | `memory_pressure.md` |
| **High latency** | Elevated p99 on one service, healthy upstream, CPU/GC issues | `high_latency.md` |
| **Unknown** | Continue with general investigation below | This runbook |

## Common Patterns and Quick Checks

### Service Crash / Restart Loop

- **Indicator:** Service metrics disappear and reappear periodically
- **Check:** `docker ps -a` for restart counts; search logs for crash stack traces
- **Common causes:** Unhandled exceptions, segfaults, misconfiguration, resource limits
- **Action:** Check logs immediately before the crash for the triggering error

### Configuration Error

- **Indicator:** Service fails immediately after deployment or configuration change
- **Check:** Search logs for "config", "invalid", "parse error", "missing required"
- **Common causes:** Invalid YAML/JSON, missing environment variables, wrong endpoint URLs
- **Action:** Compare current configuration against the last known working configuration

### Network Partition

- **Indicator:** Some services cannot reach others; partial connectivity
- **Check:** Network I/O drops to zero between specific service pairs
- **Common causes:** Docker network issues, DNS resolution failures, firewall rules
- **Action:** Check Docker network connectivity; verify DNS resolution within containers

### Resource Contention

- **Indicator:** Multiple services degrading simultaneously without clear upstream failure
- **Check:** Host-level CPU, memory, and disk I/O metrics
- **Common causes:** Noisy neighbor on shared host, insufficient Docker resource allocation
- **Action:** Check host-level metrics; consider increasing Docker memory or CPU limits

## Escalation Criteria

Escalate the incident if any of the following are true:

- The root cause cannot be identified within 30 minutes of investigation
- More than 3 services are simultaneously affected
- The `frontend` service is returning errors to users
- The failure pattern does not match any known runbook
- A data loss scenario is suspected (e.g., Redis data corruption, dropped Kafka messages)
- The incident recurs within 24 hours of a previous resolution

## Post-Incident Actions

After resolving the incident:

1. Document the timeline, root cause, and resolution steps
2. Update the relevant runbook if the investigation revealed new patterns or steps
3. Create follow-up tickets for any identified systemic improvements
4. Review monitoring and alerting — would earlier detection have been possible?
5. Consider adding the failure scenario to the fault injection test suite for regression testing
