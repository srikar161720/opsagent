"""Docker Stats Exporter for Prometheus.

Queries the Docker API for per-container resource metrics and exposes them
in Prometheus text format on port 9101. Replaces cAdvisor for macOS Docker
Desktop environments where cAdvisor cannot discover containers.

Uses a background thread to collect stats asynchronously so that Prometheus
scrapes never time out (container.stats() blocks ~1-2s per container).
"""

from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import docker

# How often the background thread refreshes stats (seconds).
COLLECT_INTERVAL = 10.0

# Shared state protected by a lock.
_lock = threading.Lock()
_metrics_text: str = "# Waiting for first collection...\n"


def _extract_cpu_seconds(stats: dict[str, Any]) -> float | None:
    """Extract cumulative CPU usage in seconds from Docker stats."""
    cpu = stats.get("cpu_stats", {})
    usage = cpu.get("cpu_usage", {})
    total_ns = usage.get("total_usage")
    if total_ns is None:
        return None
    return total_ns / 1e9


def _extract_memory(
    stats: dict[str, Any],
) -> tuple[float | None, float | None, float | None]:
    """Extract memory usage, working set, and cgroup limit bytes.

    Returns (usage_bytes, working_set_bytes, limit_bytes).

    - ``working_set = usage - inactive_file`` matches the Kubernetes
      definition (RSS excluding reclaimable page cache).
    - ``limit`` is the cgroup memory limit applied to the container. On
      macOS Docker Desktop a container with no ``--memory`` flag reports
      ``limit == host RAM`` (~16 GB); the agent-side ratio detector
      ignores those. When the Docker API omits ``limit`` entirely, the
      third tuple element is ``None`` and the exporter skips emitting
      the ``container_spec_memory_limit_bytes`` gauge for that container.
    - NOTE: this is the ``--memory`` cgroup limit, not ``--memory-swap``.
      Fault scripts that want to trigger memory pressure must set both
      flags to the same value so swap cannot mask the cap.
    """
    mem = stats.get("memory_stats", {})
    usage = mem.get("usage")
    if usage is None:
        return None, None, None

    # working_set = usage - inactive_file (matches Kubernetes definition)
    cache = mem.get("stats", {}).get("inactive_file", 0)
    working_set = max(0, usage - cache)

    limit_raw = mem.get("limit")
    limit = float(limit_raw) if limit_raw is not None else None

    return float(usage), float(working_set), limit


def _extract_network(stats: dict[str, Any]) -> dict[str, float]:
    """Extract cumulative network counters across all interfaces."""
    networks = stats.get("networks", {})
    rx_bytes = 0.0
    tx_bytes = 0.0
    rx_errors = 0.0
    tx_errors = 0.0
    for _iface, counters in networks.items():
        rx_bytes += counters.get("rx_bytes", 0)
        tx_bytes += counters.get("tx_bytes", 0)
        rx_errors += counters.get("rx_errors", 0)
        tx_errors += counters.get("tx_errors", 0)
    return {
        "rx_bytes": rx_bytes,
        "tx_bytes": tx_bytes,
        "rx_errors": rx_errors,
        "tx_errors": tx_errors,
    }


def _extract_fs_usage(stats: dict[str, Any]) -> float | None:
    """Extract filesystem usage from blkio stats.

    Docker stats don't directly expose filesystem usage the way cAdvisor does.
    We report total blkio bytes (read + write) as a proxy, or None if unavailable.
    """
    blkio = stats.get("blkio_stats", {})
    entries = blkio.get("io_service_bytes_recursive")
    if not entries:
        return None
    total = 0.0
    for entry in entries:
        total += entry.get("value", 0)
    return total


def _build_metrics(client: docker.DockerClient) -> str:
    """Query Docker API and build Prometheus text exposition."""
    lines: list[str] = []

    # Header comments (TYPE/HELP)
    headers = [
        (
            "container_cpu_usage_seconds_total",
            "counter",
            "Cumulative CPU time consumed in seconds.",
        ),
        ("container_memory_usage_bytes", "gauge", "Current memory usage in bytes."),
        ("container_memory_working_set_bytes", "gauge", "Current working set in bytes."),
        (
            "container_spec_memory_limit_bytes",
            "gauge",
            "Memory cgroup limit in bytes (host RAM when no --memory flag is set).",
        ),
        ("container_network_receive_bytes_total", "counter", "Cumulative network bytes received."),
        (
            "container_network_transmit_bytes_total",
            "counter",
            "Cumulative network bytes transmitted.",
        ),
        ("container_network_receive_errors_total", "counter", "Cumulative receive errors."),
        ("container_network_transmit_errors_total", "counter", "Cumulative transmit errors."),
        ("container_fs_usage_bytes", "counter", "Total filesystem I/O bytes."),
    ]
    for name, mtype, desc in headers:
        lines.append(f"# HELP {name} {desc}")
        lines.append(f"# TYPE {name} {mtype}")
        lines.append("")

    try:
        containers = client.containers.list()
    except Exception as e:
        lines.append(f"# ERROR listing containers: {e}")
        return "\n".join(lines) + "\n"

    for container in containers:
        labels = container.labels or {}
        service = labels.get("com.docker.compose.service", "")
        if not service:
            continue

        container_name = container.name or "unknown"

        try:
            stats = container.stats(stream=False)
        except Exception:
            continue

        label_str = f'service="{service}",name="{container_name}"'

        # CPU
        cpu_sec = _extract_cpu_seconds(stats)
        if cpu_sec is not None:
            lines.append(f"container_cpu_usage_seconds_total{{{label_str}}} {cpu_sec:.6f}")

        # Memory
        mem_usage, mem_working, mem_limit = _extract_memory(stats)
        if mem_usage is not None:
            lines.append(f"container_memory_usage_bytes{{{label_str}}} {mem_usage:.0f}")
        if mem_working is not None:
            lines.append(f"container_memory_working_set_bytes{{{label_str}}} {mem_working:.0f}")
        # container_spec_memory_limit_bytes — the cgroup memory limit. Labelled
        # identically to container_memory_working_set_bytes so the agent-side
        # memory_utilization ratio query joins natively on {service,name}.
        # Containers that don't report a limit (unlikely in practice) are
        # simply absent from this gauge.
        if mem_limit is not None:
            lines.append(f"container_spec_memory_limit_bytes{{{label_str}}} {mem_limit:.0f}")

        # Network
        net = _extract_network(stats)
        lines.append(f"container_network_receive_bytes_total{{{label_str}}} {net['rx_bytes']:.0f}")
        lines.append(f"container_network_transmit_bytes_total{{{label_str}}} {net['tx_bytes']:.0f}")
        lines.append(
            f"container_network_receive_errors_total{{{label_str}}} {net['rx_errors']:.0f}"
        )
        lines.append(
            f"container_network_transmit_errors_total{{{label_str}}} {net['tx_errors']:.0f}"
        )

        # Filesystem
        fs_usage = _extract_fs_usage(stats)
        if fs_usage is not None:
            lines.append(f"container_fs_usage_bytes{{{label_str}}} {fs_usage:.0f}")

    return "\n".join(lines) + "\n"


def _collector_loop() -> None:
    """Background thread that periodically collects Docker stats."""
    global _metrics_text
    client = docker.DockerClient(base_url="unix:///var/run/docker.sock")
    print("Background collector started.")

    while True:
        try:
            start = time.time()
            result = _build_metrics(client)
            elapsed = time.time() - start
            with _lock:
                _metrics_text = result
            print(f"Collected stats in {elapsed:.1f}s")
        except Exception as e:
            print(f"Collection error: {e}")

        time.sleep(COLLECT_INTERVAL)


class MetricsHandler(BaseHTTPRequestHandler):
    """HTTP handler that serves cached Prometheus metrics instantly."""

    def do_GET(self) -> None:
        if self.path == "/metrics":
            with _lock:
                body = _metrics_text.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ok\n")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        """Suppress default request logging to reduce noise."""
        pass


def main() -> None:
    port = 9101

    # Start background collector thread
    collector = threading.Thread(target=_collector_loop, daemon=True)
    collector.start()

    server = HTTPServer(("0.0.0.0", port), MetricsHandler)
    print(f"Docker Stats Exporter listening on :{port}")
    print("  /metrics  — Prometheus metrics endpoint")
    print("  /health   — Health check")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.server_close()


if __name__ == "__main__":
    main()
