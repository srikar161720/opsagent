# Memory Pressure / OOM

## Symptoms

- `memory_usage` or `memory_working_set_bytes` metric climbing steadily over 30+ minutes without leveling off
- Service restarts or `OOMKilled` events visible in container logs or Docker events
- Gradual latency increase preceding crash — as memory fills, garbage collection (GC) pauses become longer and more frequent
- Container memory usage approaching the configured limit (e.g., 85%+ of Docker memory constraint)
- In the OTel Demo stack, `frontend` is the highest memory consumer (~200MB baseline); any service showing sustained growth above its baseline warrants investigation

## Root Cause

Memory is being consumed faster than it is released, eventually exceeding the container's memory limit and triggering an OOM kill. Common causes include:

- **Memory leak** — objects are allocated but never freed (e.g., growing lists, unclosed streams, event listener accumulation)
- **Large object accumulation** — caching without TTL or bounded size causes unbounded memory growth
- **Insufficient memory allocation** — container memory limits are set too low for the service's workload
- **GC pressure** — high allocation rates cause the garbage collector to run frequently but ineffectively, leading to "GC overhead limit exceeded" errors
- **Request payload accumulation** — large request bodies held in memory during processing without streaming

## Investigation Steps

1. **Check memory trend:**
   - Query `memory_working_set_bytes` over a 60-minute window for the affected service
   - Look for monotonic increase (leak) vs. sawtooth pattern (normal GC) vs. sudden spike (burst allocation)
   - Compare against the service's normal baseline memory usage

2. **Search for OOM events:**
   - Search logs for "OOMKilled", "out of memory", "GC overhead", "heap space"
   - Check Docker events: `docker events --filter event=oom`
   - Note the timestamp of the OOM event and correlate with the memory growth start time

3. **Correlate with other metrics:**
   - Check CPU usage — high GC activity often shows as CPU spikes preceding the OOM
   - Check request rate — memory growth may correlate with traffic increase
   - Check latency — gradual latency increase often precedes OOM as GC pauses grow

4. **Identify the memory consumer:**
   - If the service has profiling enabled, check heap dumps or memory snapshots
   - Look for patterns in logs around the time memory started growing (new feature deployed, configuration change, traffic pattern change)
   - Check for caching without TTL: search logs or config for cache-related entries

5. **Check container limits:**
   - Verify the container's memory limit in Docker Compose configuration
   - Compare the limit against actual peak usage from historical data
   - Check if the limit was recently changed

## Remediation

### Immediate

- Restart the affected service to reclaim memory: `docker restart <service-name>`
- If the service keeps OOM-killing on restart, temporarily increase the container memory limit in Docker Compose
- Reduce traffic temporarily using load balancer weights or feature flags to lower memory pressure

### Long-term

- Profile memory usage to identify the specific leak source (use tools like async-profiler for JVM, tracemalloc for Python, pprof for Go)
- Add memory usage alerts at 85% of container limit — this provides a warning window before OOM
- Enforce bounded caches with TTL and maximum size limits
- Set JVM heap limits explicitly (`-Xmx`) to a value below the container limit, leaving room for non-heap memory
- Implement memory-aware request handling: stream large payloads instead of buffering, use pagination for large result sets
- Review and right-size container memory limits based on observed usage patterns with appropriate headroom (20-30%)

## Prevention

- Include memory profiling in performance testing before deployment
- Monitor memory usage trends over time — gradual increases across deployments may indicate slow leaks
- Set up automated memory regression testing: compare memory usage of new builds against baseline
- Use container resource monitoring dashboards to track memory utilization across all services
- Review code changes that introduce caching or in-memory data structures for bounded size guarantees
