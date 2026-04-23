"""End-to-end serving-layer test.

This is the Week-11 demo-verification test: trigger the guided demo via
the new ``POST /demo/investigate`` endpoint, poll its status, and assert
the agent identifies the correct service. The endpoint internally runs
the full inject → wait → investigate → restore lifecycle that produced
Session 13's 100% Recall@1.

Gated behind the ``OPSAGENT_RUN_E2E=1`` environment variable so it doesn't
run in the default unit/integration gate — the full round-trip takes
~3 minutes per demo and burns LLM tokens.

Run manually::

    OPSAGENT_RUN_E2E=1 poetry run pytest tests/integration/test_serving_e2e.py -v

Prerequisites (user must ensure before running):
- Docker Engine running.
- ``make infra-up`` + ``make demo-up`` executed (stack healthy).
- ``GEMINI_API_KEY`` set in the environment (or ``.env``).

The test spins up its own uvicorn on an ephemeral port (same pattern as
``test_api_live.py``), so no ports collide with a running ``make run``.
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

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytestmark = pytest.mark.skipif(
    os.environ.get("OPSAGENT_RUN_E2E") != "1",
    reason="E2E test gated on OPSAGENT_RUN_E2E=1 (runs a real fault injection).",
)

# Total polling budget for the demo lifecycle: 120 s wait + ~30 s
# investigation + ~15 s restore + overhead. 5 minutes is generous.
_POLL_BUDGET_SECONDS = 300
_POLL_INTERVAL_SECONDS = 5.0

# Terminal phases that end the polling loop.
_TERMINAL_PHASES = frozenset({"completed", "failed"})


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_for_http(url: str, timeout_s: float = 45.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            r = requests.get(url, timeout=1.0)
            if 200 <= r.status_code < 300:
                return True
        except requests.RequestException:
            pass
        time.sleep(0.5)
    return False


@pytest.fixture(scope="module")
def live_api() -> Iterator[str]:
    """Spin up uvicorn + wait for health."""
    port = _find_free_port()
    base = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
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
    # stderr=DEVNULL because confluent_kafka's reconnect-retry spam fills
    # a PIPE buffer and blocks uvicorn's event loop — same trap as
    # ``test_api_live.py``.
    proc = subprocess.Popen(
        cmd,
        cwd=_PROJECT_ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        assert _wait_for_http(f"{base}/health", timeout_s=60.0), "api never became healthy"
        yield base
    finally:
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()


def _poll_until_terminal(live_api: str, investigation_id: str) -> dict:
    """Poll the demo status endpoint until phase is completed/failed, or timeout."""
    deadline = time.time() + _POLL_BUDGET_SECONDS
    last_phase = ""
    while time.time() < deadline:
        r = requests.get(
            f"{live_api}/demo/investigations/{investigation_id}/status",
            timeout=5,
        )
        assert r.status_code == 200, f"status poll failed: {r.status_code} {r.text[:200]}"
        data = r.json()
        phase = data.get("phase", "")
        if phase != last_phase:
            last_phase = phase
            # Print so a real E2E run shows progress; pytest -v surfaces this.
            print(f"[demo {investigation_id}] phase → {phase}")
        if phase in _TERMINAL_PHASES:
            return data
        time.sleep(_POLL_INTERVAL_SECONDS)
    raise AssertionError(
        f"Demo {investigation_id} didn't terminate within "
        f"{_POLL_BUDGET_SECONDS}s (last phase={last_phase!r})"
    )


@pytest.mark.integration
class TestServingE2E:
    """One end-to-end guided demo using the service-picker endpoint."""

    def test_demo_cartservice_service_crash_roundtrip(self, live_api: str) -> None:
        """Pick cartservice, run the full demo, assert root_cause=cartservice.

        The ``/demo/investigate`` endpoint maps ``cartservice`` to the
        ``service_crash`` fault scenario, runs inject → wait 120 s →
        investigate → restore, and appends the result to
        ``/investigations`` history. Session 13 produced 5/5 Recall@1 at
        0.75 confidence on this exact scenario.
        """
        # 1) POST /demo/investigate
        r = requests.post(
            f"{live_api}/demo/investigate",
            json={"service": "cartservice"},
            timeout=10,
        )
        assert r.status_code == 200, f"demo start failed: {r.status_code} {r.text[:200]}"
        start = r.json()
        investigation_id = start["investigation_id"]
        assert investigation_id.startswith("demo_")
        assert start["fault_type"] == "service_crash"
        assert start["ground_truth"] == "cartservice"

        # 2) Poll until terminal phase.
        final = _poll_until_terminal(live_api, investigation_id)

        # 3) Happy-path asserts.
        assert final["phase"] == "completed", f"demo failed: {final.get('error')!r}"
        result = final.get("result")
        assert result is not None, "no result attached to completed status"
        assert result["status"] == "completed"
        assert result["root_cause"] is not None
        assert result["root_cause"]["service"] == "cartservice"
        assert result["root_cause"]["confidence"] >= 0.7

        # 4) Demo result also appears in the regular /investigations history.
        r2 = requests.get(f"{live_api}/investigations", timeout=5)
        assert r2.status_code == 200
        history = r2.json()
        assert any(h["investigation_id"] == investigation_id for h in history)

    def test_concurrent_demo_rejected_with_409(self, live_api: str) -> None:
        """Two simultaneous demos are rejected by the single-user lock.

        Run this first; spin a second request immediately while the first
        is still in flight. The second must return HTTP 409 and the first
        must still succeed. Kept in a separate test so it can be skipped
        when the user only wants the happy path.

        Note: this test pays the full ~3 min for the first demo. Order it
        AFTER the round-trip test (pytest runs in file order by default).
        """
        # Kick off demo 1.
        r1 = requests.post(
            f"{live_api}/demo/investigate",
            json={"service": "cartservice"},
            timeout=10,
        )
        assert r1.status_code == 200
        inv_id = r1.json()["investigation_id"]
        try:
            # Demo 2 immediately → expect 409.
            r2 = requests.post(
                f"{live_api}/demo/investigate",
                json={"service": "frontend"},
                timeout=5,
            )
            assert r2.status_code == 409
            assert "already running" in r2.json()["detail"].lower()
        finally:
            # Drain demo 1 so the restore script runs and the lock releases.
            _poll_until_terminal(live_api, inv_id)
