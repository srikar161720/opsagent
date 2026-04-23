"""Integration tests for the OpsAgent FastAPI server.

These tests spin up ``uvicorn src.serving.api:app`` in a subprocess, poll
``/health`` until it responds, exercise the read-only endpoints
(``/health``, ``/topology``, ``/investigations``), then shut the server
down cleanly. No LLM calls, no Docker stack required — they verify the
lifespan, routing, CORS, and Pydantic response models work end-to-end
in-process.

Mark with ``@pytest.mark.integration`` so the default unit-test run
skips them.
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Iterator

import pytest
import requests


def _find_free_port() -> int:
    """Return a free ephemeral port on the host."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_for_http(url: str, timeout_s: float = 45.0) -> bool:
    """Poll ``url`` for up to ``timeout_s`` seconds. Returns True on 2xx.

    Uses a 5-second client timeout per request because ``/health`` fans out
    to Prometheus / Loki / Kafka probes and can legitimately take >2 s when
    Kafka is not reachable (``confluent_kafka.AdminClient.list_topics``
    honours its own timeout separately).
    """
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            r = requests.get(url, timeout=5.0)
            if 200 <= r.status_code < 300:
                return True
        except requests.RequestException:
            pass
        time.sleep(1.0)
    return False


@pytest.fixture(scope="module")
def api_server() -> Iterator[str]:
    """Spin up uvicorn in a subprocess bound to a free port.

    We run the server with ``--workers 1`` so the lifespan only fires
    once. The fixture yields the base URL (e.g. ``http://127.0.0.1:58213``)
    so individual tests can point requests at it.
    """
    port = _find_free_port()
    base = f"http://127.0.0.1:{port}"
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    env = os.environ.copy()
    # Keep the subprocess minimal: no Docker, no ChromaDB, no real LLM.
    # The lifespan will still happily construct AgentExecutor —
    # ``AgentExecutor.__init__`` does NOT require live dependencies.
    env.setdefault("GEMINI_API_KEY", "test-key-for-lifespan-startup")

    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "src.serving.api:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--workers",
        "1",
        "--log-level",
        "warning",
    ]
    # IMPORTANT: route stderr to DEVNULL. ``confluent_kafka`` emits
    # several hundred KB/s of retry-failure lines to stderr when the
    # broker isn't reachable — capturing to a PIPE fills the OS buffer
    # and blocks the server's event loop on writes. DEVNULL avoids that
    # at the cost of losing logs in the failure case.
    proc = subprocess.Popen(
        cmd,
        cwd=project_root,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        if not _wait_for_http(f"{base}/health", timeout_s=60.0):
            proc.kill()
            raise RuntimeError(
                "uvicorn failed to become healthy within 60s. "
                "Re-run with uvicorn manually (`make run`) to inspect logs."
            )
        yield base
    finally:
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.mark.integration
class TestServingApiLive:
    """In-process smoke tests of the live FastAPI server."""

    def test_health_returns_structured_status(self, api_server: str) -> None:
        r = requests.get(f"{api_server}/health", timeout=10)
        assert r.status_code == 200
        body = r.json()
        # Status is either healthy or degraded depending on whether this
        # environment has Prometheus/Loki/Kafka/ChromaDB reachable.
        assert body["status"] in ("healthy", "degraded")
        assert set(body["components"]) == {
            "prometheus",
            "loki",
            "kafka",
            "chromadb",
            "llm",
        }

    def test_topology_returns_expected_shape(self, api_server: str) -> None:
        r = requests.get(f"{api_server}/topology", timeout=10)
        assert r.status_code == 200
        body = r.json()
        assert len(body["nodes"]) == 11
        assert len(body["edges"]) == 14
        assert body["subgraph_of"] is None
        # Expected well-known services.
        names = {n["name"] for n in body["nodes"]}
        assert {"cartservice", "frontend", "redis", "checkoutservice"} <= names

    def test_topology_subgraph_returns_focused_view(self, api_server: str) -> None:
        r = requests.get(f"{api_server}/topology", params={"service": "cartservice"}, timeout=5)
        assert r.status_code == 200
        body = r.json()
        assert body["subgraph_of"] == "cartservice"

    def test_investigations_empty_on_fresh_start(self, api_server: str) -> None:
        r = requests.get(f"{api_server}/investigations", timeout=10)
        assert r.status_code == 200
        assert r.json() == []

    def test_unknown_investigation_returns_404(self, api_server: str) -> None:
        r = requests.get(f"{api_server}/investigations/inv_nonexistent", timeout=10)
        assert r.status_code == 404

    def test_openapi_available(self, api_server: str) -> None:
        r = requests.get(f"{api_server}/openapi.json", timeout=10)
        assert r.status_code == 200
        spec = r.json()
        assert spec["info"]["title"] == "OpsAgent API"
        # Must expose each of the four documented endpoints.
        paths = set(spec["paths"].keys())
        assert {"/health", "/topology", "/investigate", "/investigations"} <= paths

    def test_cors_allows_dashboard_origin(self, api_server: str) -> None:
        """CORS preflight from the Streamlit origin should be accepted."""
        r = requests.options(
            f"{api_server}/health",
            headers={
                "Origin": "http://localhost:8501",
                "Access-Control-Request-Method": "GET",
            },
            timeout=10,
        )
        # Starlette's CORS middleware returns 200 with the allow headers.
        assert r.status_code == 200
        assert "http://localhost:8501" in r.headers.get("access-control-allow-origin", "")
