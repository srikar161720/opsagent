"""Service Probe Exporter for Prometheus.

Periodically probes each OTel Demo service via TCP connection and exposes
availability (up/down) and response latency as Prometheus gauge metrics.

Runs on port 9102. Probe results are collected by a background thread
every 15 seconds and cached for instant scrape responses.
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Probe interval in seconds (matches Prometheus scrape interval).
PROBE_INTERVAL = 15.0

# TCP connection timeout per service (seconds).
CONNECT_TIMEOUT = 5.0

# Services to probe: {name: (host, port)}
# Hosts are Docker Compose service names, resolvable on the shared network.
SERVICES: dict[str, tuple[str, int]] = {
    "cartservice": ("cartservice", 8080),  # .NET binds to 8080 in v1.10.0
    "checkoutservice": ("checkoutservice", 5050),
    "currencyservice": ("currencyservice", 7001),
    "frontend": ("frontend", 8080),
    "paymentservice": ("paymentservice", 50051),
    "productcatalogservice": ("productcatalogservice", 3550),
    "redis": ("redis", 6379),
}

# Shared state protected by a lock.
_lock = threading.Lock()
_metrics_text: str = "# Waiting for first probe cycle...\n"


def _probe_service(host: str, port: int) -> tuple[bool, float]:
    """Probe a service and return (is_up, duration_seconds).

    Goes beyond TCP connect: sends a small payload and waits for a response.
    This detects paused containers (which accept TCP SYN at the kernel level
    but can't respond to application-level requests).

    - Redis (port 6379): sends PING, expects +PONG
    - HTTP (port 8080): sends minimal GET, expects any response
    - gRPC/other: sends empty bytes, expects any response within timeout
    """
    start = time.monotonic()
    try:
        sock = socket.create_connection((host, port), timeout=CONNECT_TIMEOUT)
        sock.settimeout(3.0)  # read timeout for response

        try:
            if port == 6379:
                # Redis PING protocol
                sock.sendall(b"PING\r\n")
                resp = sock.recv(64)
                is_up = b"PONG" in resp
            elif port == 8080:
                # Minimal HTTP request
                sock.sendall(b"GET / HTTP/1.0\r\nHost: probe\r\n\r\n")
                resp = sock.recv(64)
                is_up = len(resp) > 0
            else:
                # For gRPC ports: attempt to read — a healthy gRPC server
                # will either send a response or reset. A paused server
                # will timeout on recv.
                sock.sendall(b"\x00")
                try:
                    resp = sock.recv(64)
                    is_up = True  # got any response (even RST/error)
                except socket.timeout:
                    is_up = False  # no response = paused/frozen
        except socket.timeout:
            is_up = False  # read timed out = paused/frozen
        except OSError:
            is_up = True  # connection reset = service is alive but rejected our probe

        sock.close()
        duration = time.monotonic() - start
        return is_up, duration
    except (OSError, socket.timeout):
        duration = time.monotonic() - start
        return False, duration


def _collect_probes() -> str:
    """Probe all services and return Prometheus-format metric text."""
    lines: list[str] = []
    lines.append("# HELP service_probe_up Whether the service is reachable (1=up, 0=down)")
    lines.append("# TYPE service_probe_up gauge")
    lines.append("# HELP service_probe_duration_seconds TCP connect time to the service")
    lines.append("# TYPE service_probe_duration_seconds gauge")

    for name, (host, port) in SERVICES.items():
        is_up, duration = _probe_service(host, port)
        lines.append(f'service_probe_up{{service="{name}"}} {1 if is_up else 0}')
        lines.append(f'service_probe_duration_seconds{{service="{name}"}} {duration:.6f}')

    return "\n".join(lines) + "\n"


def _background_collector() -> None:
    """Background thread that probes services on a fixed interval."""
    global _metrics_text  # noqa: PLW0603
    logger.info("Starting probe collector (interval=%.0fs)", PROBE_INTERVAL)
    while True:
        try:
            text = _collect_probes()
            with _lock:
                _metrics_text = text
        except Exception:
            logger.exception("Probe collection failed")
        time.sleep(PROBE_INTERVAL)


class _MetricsHandler(BaseHTTPRequestHandler):
    """HTTP handler that serves cached probe metrics."""

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/metrics":
            with _lock:
                body = _metrics_text.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        """Suppress per-request access logs."""


def main() -> None:
    """Start the background collector and HTTP server."""
    collector = threading.Thread(target=_background_collector, daemon=True)
    collector.start()

    server = HTTPServer(("0.0.0.0", 9102), _MetricsHandler)
    logger.info("Service Probe Exporter listening on :9102")
    server.serve_forever()


if __name__ == "__main__":
    main()
