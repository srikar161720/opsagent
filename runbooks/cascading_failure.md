# Cascading Failure

## Symptoms

- Multiple services degrading in sequence, with timestamps staggered 30-120 seconds apart
- Error rates climbing downstream from the original failure point
- Causal graph shows a single root service with high fan-out to multiple affected services
- Frontend error rate increases last, after backend services have already degraded
- In the OTel Demo stack, a typical cascade: `redis` failure → `cartservice` errors → `checkoutservice` errors → `frontend` errors

## Root Cause

An upstream service failure propagates to all dependents via synchronous call chains. When a dependency fails or becomes slow, callers retry and accumulate pending requests, eventually exhausting their own resources (threads, connections, memory) and failing themselves. This creates a domino effect that spreads through the service dependency graph.

Common triggers include:

- **Service crash** — a core dependency goes down entirely, causing all callers to timeout
- **Resource exhaustion** — CPU, memory, or connection pool saturation in an upstream service causes slow responses that back up downstream
- **Retry amplification** — aggressive retry policies multiply load on an already-degraded service, accelerating its failure
- **Missing circuit breakers** — callers continue sending requests to a failed service instead of failing fast
- **Shared dependency failure** — multiple services depend on the same database or cache (e.g., Redis), and its failure affects all of them simultaneously

## Investigation Steps

1. **Map the dependency chain:**
   - Use `get_topology` to identify the full upstream dependency chain for the affected services
   - Note which services are upstream (potential root causes) and downstream (showing symptoms)

2. **Find the earliest degrading service:**
   - Query error rate and latency metrics for all services in the chain over the last 30 minutes
   - Sort services by the timestamp of their first anomalous metric — the EARLIEST degrading service is the root cause candidate
   - Pay special attention to services with no upstream dependencies (e.g., `redis`) — if they fail, everything downstream fails

3. **Confirm causal direction:**
   - Run `discover_causation` on the chain of affected services to confirm that causality flows from the identified root cause outward
   - Verify that the root cause service showed anomalous behavior BEFORE its dependents

4. **Check for retry amplification:**
   - Search logs for retry patterns: "retry", "attempt 2", "backoff"
   - Look for exponential increase in request count on the root cause service
   - Check if the root cause service's CPU or memory spiked due to retry load

5. **Assess blast radius:**
   - Count how many services are affected and which user-facing functionality is impaired
   - Check if the cascade has reached the frontend or is still contained to backend services

## Remediation

### Immediate

- **Restart the root cause service first**, then restart dependent services in dependency order (upstream before downstream)
- If the root cause service cannot be restarted, consider disabling the feature that depends on it to stop the cascade
- Temporarily disable retries on callers of the failed service to reduce amplification load

### Long-term

- Implement exponential backoff with jitter in all service-to-service callers to prevent retry storms
- Add circuit breakers (e.g., Hystrix pattern) to prevent callers from continuously hitting a failed service
- Introduce the bulkhead pattern to isolate failure domains — separate thread pools or connection pools for different dependencies
- Add health check endpoints and configure load balancers to remove unhealthy instances automatically
- Implement graceful degradation — services should return cached or default responses when a dependency is unavailable
- Set up cascade detection alerts: if more than 2 services show elevated error rates within a 5-minute window, trigger an investigation

## Prevention

- Regularly test failure scenarios with chaos engineering (kill individual services and observe propagation)
- Review service dependency graphs quarterly to identify single points of failure
- Ensure all inter-service calls have appropriate timeouts configured
- Maintain runbook links in service documentation so on-call engineers can quickly identify cascade patterns
