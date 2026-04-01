# High Latency (Non-Cascading)

## Symptoms

- `latency_p99` elevated on one service while upstream dependencies appear healthy and responsive
- `error_rate` may remain low — the service is slow but not failing outright
- CPU or memory usage elevated on the affected service specifically
- The latency increase is isolated to one service and does not propagate to its dependents (distinguishing this from a cascading failure)
- In the OTel Demo stack, common targets include `productcatalogservice` (catalog queries) or `checkoutservice` (complex multi-step processing)

## Root Cause

The service is processing requests slowly due to internal resource constraints rather than dependency failures. Common causes include:

- **CPU saturation** — the service's CPU is fully utilized, causing request processing to queue
- **Inefficient queries** — database queries or data structure lookups that degrade under load (e.g., full table scans, N+1 query patterns)
- **GC pauses** — garbage collection stop-the-world pauses cause periodic latency spikes, visible as high p99 with normal p50
- **Lock contention** — threads blocking on shared resources (mutexes, database locks, file locks)
- **I/O wait** — disk or network I/O bottlenecks causing request processing to stall
- **Thread pool exhaustion** — all worker threads are busy, causing new requests to queue

## Investigation Steps

1. **Check CPU and memory:**
   - Query `cpu_usage` and `memory_working_set_bytes` for the affected service over the last 30 minutes
   - CPU near 100% suggests compute-bound; memory climbing suggests GC pressure
   - Compare against the service's normal baseline values

2. **Analyze latency distribution:**
   - Compare `latency_p50` vs `latency_p99` — a large gap (e.g., p50=50ms, p99=2000ms) suggests tail latency from GC pauses, lock contention, or occasional slow queries
   - A proportional increase in both p50 and p99 suggests systemic slowdown (CPU saturation, all queries slow)

3. **Search for latency indicators in logs:**
   - Search for "slow", "timeout", "GC pause", "blocked", "deadline exceeded", "lock wait"
   - Look for slow query logs or request processing time warnings
   - Check for thread dump or deadlock detection messages

4. **Verify upstream health:**
   - Use `get_topology` to identify upstream dependencies
   - Query metrics for upstream services to confirm they are healthy — this rules out cascading failure
   - If upstream services are also slow, this is a cascading failure, not isolated high latency

5. **Check for recent changes:**
   - Look for recent deployments, configuration changes, or traffic pattern shifts
   - Check if the latency increase correlates with a specific event or time

## Remediation

### Immediate

- If CPU-bound: horizontally scale by adding more replicas of the affected service
- If memory/GC-bound: restart the service to clear accumulated garbage and reset heap state
- If I/O-bound: check disk usage and clear any full filesystems or rotate large log files
- Temporarily reduce traffic to the affected service if possible (load balancer weight adjustment)

### Long-term

- **CPU optimization:** Profile with async-profiler or pprof to identify CPU hot paths; optimize critical code paths
- **GC tuning:** Adjust JVM GC settings (e.g., switch from G1 to ZGC for lower pause times, tune heap size)
- **Query optimization:** Add database indices, implement query result caching, fix N+1 patterns
- **Connection pooling:** Ensure database and service connections use properly sized pools
- **Add query timeout enforcement:** Set maximum execution time on database queries and upstream calls to prevent indefinite blocking
- **Implement request deadlines:** Use gRPC deadlines or HTTP request timeouts to fail fast on slow requests rather than accumulating queue depth

## Prevention

- Establish latency SLOs per service and alert when p99 exceeds the budget
- Include latency regression testing in CI/CD pipelines (benchmark critical endpoints)
- Monitor CPU utilization and set alerts at 70% sustained utilization
- Review query performance periodically, especially after schema changes or data growth
- Load test services individually to understand their breaking point and scale limits
