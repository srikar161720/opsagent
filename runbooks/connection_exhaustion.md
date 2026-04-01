# Connection Pool Exhaustion

## Symptoms

- Service logs contain: "connection timeout", "pool exhausted", "too many connections", "cannot acquire connection"
- `connection_count` metric at or near the maximum configured value for the affected service
- Downstream service latency spikes within 1-2 minutes of pool saturation
- New requests fail with timeout errors while existing connections remain held
- In the OTel Demo stack, this most commonly affects `cartservice` connecting to `redis`, or `checkoutservice` connecting to upstream services

## Root Cause

The connection pool is fully utilized and no connections are available for new requests. Common triggers include:

- **Slow queries or upstream responses** — connections are held longer than expected, reducing pool throughput
- **Connection leaks** — application code acquires connections but fails to release them (e.g., missing `finally` block or context manager)
- **Traffic spikes** — sudden increase in request volume exceeds pool capacity
- **Deadlocked connections** — connections waiting on locks or resources that will never be released
- **Redis maxclient limit** — Redis itself rejects new connections when `maxclients` is reached

## Investigation Steps

1. **Check connection metrics:**
   - Query `connection_count` for the affected service over the last 30 minutes
   - Compare current value against the configured pool maximum
   - Look for a sudden step-up or gradual climb pattern

2. **Search logs for connection errors:**
   - Search for "timeout", "exhausted", "too many connections", "refused" in the affected service logs
   - Check the timestamp of the first connection error — this marks the onset

3. **Identify the root cause of held connections:**
   - Look for long-running queries or slow upstream calls that hold connections
   - Check for error patterns that might indicate a connection leak (connections acquired but never returned)
   - For Redis: check `redis-cli INFO clients` for connected_clients count

4. **Verify connection pool configuration:**
   - Check service configuration for pool size, timeout, and idle connection settings
   - Compare pool size against the expected concurrent request rate

5. **Check upstream dependency health:**
   - Use `get_topology` to identify which services the affected service depends on
   - Query metrics for those upstream services to see if they are responding slowly

## Remediation

### Immediate

- Restart the affected service to flush hung connections:
  `docker restart <service-name>`
- For Redis connection exhaustion, manually clear idle connections:
  `redis-cli CLIENT KILL TYPE normal`
- If the issue is caused by a specific slow upstream service, consider temporarily removing it from the load balancer rotation

### Long-term

- Increase connection pool max size in service configuration (but not indefinitely — set based on expected load + 50% headroom)
- Add circuit breaker on all callers of the affected service to fail fast instead of queuing
- Implement connection pool monitoring with alerts at 80% utilization (before exhaustion)
- Add connection acquisition timeouts to prevent indefinite blocking
- Ensure all connection usage follows try/finally or context manager patterns to prevent leaks
- For Redis: configure `maxclients` appropriately and add `timeout` for idle client connections

## Prevention

- Load test connection pool behavior under peak traffic conditions
- Monitor connection pool utilization as a standard SRE metric
- Set up automated alerts when pool utilization exceeds 80% for more than 5 minutes
- Review code for proper connection lifecycle management during code reviews
