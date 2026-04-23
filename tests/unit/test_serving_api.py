"""Tests for src.serving.api.

Uses ``fastapi.testclient.TestClient`` which drives the app through its
real lifespan, so ``AgentExecutor.from_config`` is patched at the module
level to hand back a lightweight fake that never touches the LLM or the
LangGraph. Individual tests also patch the probe helpers for the
``/health`` endpoint.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.serving import api as api_module
from src.serving.api import (
    _alert_payload_to_executor_alert,
    _build_demo_alert,
    _record_demo_status,
    _remember,
    _run_demo_lifecycle,
    _update_demo_phase,
    app,
)
from src.serving.schemas import (
    AlertPayload,
    DemoInvestigationStatus,
    InvestigationResponse,
)

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeAgent:
    """Minimal stand-in for ``AgentExecutor``.

    The ``investigate`` return shape mirrors the real executor's 6-field
    dict so ``_build_response_from_agent_result`` has the same code-path
    as production.
    """

    def __init__(
        self,
        *,
        root_cause: str = "cartservice",
        confidence: float = 0.75,
        raise_exc: bool = False,
    ) -> None:
        self.root_cause = root_cause
        self.confidence = confidence
        self.raise_exc = raise_exc
        self.calls: list[dict[str, Any]] = []

    def investigate(self, alert: dict[str, Any], **_: Any) -> dict[str, Any]:
        self.calls.append(alert)
        if self.raise_exc:
            raise RuntimeError("simulated investigation failure")
        return {
            "root_cause": self.root_cause,
            "root_cause_confidence": self.confidence,
            "top_3_predictions": [self.root_cause, "redis", "checkoutservice"],
            "confidence": self.confidence,
            "rca_report": f"# Root cause\n{self.root_cause}",
            "recommended_actions": ["Restart " + self.root_cause],
        }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_agent() -> _FakeAgent:
    return _FakeAgent()


@pytest.fixture
def client(fake_agent: _FakeAgent) -> Iterator[TestClient]:
    """TestClient with AgentExecutor.from_config patched to return the fake."""
    with (
        patch.object(
            api_module,
            "AgentExecutor",
            MagicMock(from_config=MagicMock(return_value=fake_agent)),
        ),
        TestClient(app) as c,
    ):
        yield c


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------


class TestHealth:
    def test_healthy_when_all_probes_connected(self, client: TestClient) -> None:
        with (
            patch.object(api_module, "_probe_prometheus", return_value="connected"),
            patch.object(api_module, "_probe_loki", return_value="connected"),
            patch.object(api_module, "_probe_kafka", return_value="connected"),
            patch.object(api_module, "_probe_chromadb", return_value="connected"),
            patch.object(api_module, "_probe_llm", return_value="available"),
        ):
            r = client.get("/health")
            assert r.status_code == 200
            data = r.json()
            assert data["status"] == "healthy"
            assert set(data["components"]) == {
                "prometheus",
                "loki",
                "kafka",
                "chromadb",
                "llm",
            }

    def test_degraded_when_any_probe_unreachable(self, client: TestClient) -> None:
        with (
            patch.object(api_module, "_probe_prometheus", return_value="unreachable"),
            patch.object(api_module, "_probe_loki", return_value="connected"),
            patch.object(api_module, "_probe_kafka", return_value="connected"),
            patch.object(api_module, "_probe_chromadb", return_value="connected"),
            patch.object(api_module, "_probe_llm", return_value="available"),
        ):
            r = client.get("/health")
            assert r.json()["status"] == "degraded"

    def test_degraded_when_llm_unconfigured(self, client: TestClient) -> None:
        with (
            patch.object(api_module, "_probe_prometheus", return_value="connected"),
            patch.object(api_module, "_probe_loki", return_value="connected"),
            patch.object(api_module, "_probe_kafka", return_value="connected"),
            patch.object(api_module, "_probe_chromadb", return_value="connected"),
            patch.object(api_module, "_probe_llm", return_value="unconfigured"),
        ):
            r = client.get("/health")
            assert r.json()["status"] == "degraded"


# ---------------------------------------------------------------------------
# /topology
# ---------------------------------------------------------------------------


class TestTopology:
    def test_full_graph(self, client: TestClient) -> None:
        r = client.get("/topology")
        assert r.status_code == 200
        data = r.json()
        # Full OB topology: 11 services + 14 directed edges.
        assert len(data["nodes"]) == 11
        assert len(data["edges"]) == 14
        assert data["subgraph_of"] is None
        # Spot-check a few well-known services.
        names = {n["name"] for n in data["nodes"]}
        assert {"cartservice", "frontend", "redis"} <= names

    def test_subgraph(self, client: TestClient) -> None:
        r = client.get("/topology", params={"service": "cartservice"})
        assert r.status_code == 200
        data = r.json()
        assert data["subgraph_of"] == "cartservice"


# ---------------------------------------------------------------------------
# /investigate
# ---------------------------------------------------------------------------


class TestInvestigate:
    def _payload(self) -> dict[str, Any]:
        return {
            "alert": {
                "service": "cartservice",
                "metric": "latency_p99",
                "value": 500.0,
                "threshold": 200.0,
                "timestamp": "2026-04-21T00:00:00Z",
            },
            "time_range_minutes": 30,
        }

    def test_success(self, client: TestClient, fake_agent: _FakeAgent) -> None:
        r = client.post("/investigate", json=self._payload())
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "completed"
        assert data["investigation_id"].startswith("inv_")
        assert data["root_cause"]["service"] == "cartservice"
        assert data["root_cause"]["confidence"] == 0.75
        assert data["top_3_predictions"][0] == "cartservice"
        assert "Restart cartservice" in data["recommendations"]
        # duration is real wall-clock but small; allow generous ceiling.
        assert 0.0 <= data["duration_seconds"] < 10.0
        # Agent saw the translated alert.
        assert len(fake_agent.calls) == 1
        assert fake_agent.calls[0]["affected_services"] == ["cartservice"]

    def test_failure_returns_200_with_failed_status(
        self, client: TestClient, fake_agent: _FakeAgent
    ) -> None:
        fake_agent.raise_exc = True
        r = client.post("/investigate", json=self._payload())
        assert r.status_code == 200  # API never surfaces 5xx for agent errors
        data = r.json()
        assert data["status"] == "failed"
        assert data["root_cause"] is None
        assert "simulated investigation failure" in data["report"]

    def test_unknown_root_cause_leaves_root_cause_null(
        self, client: TestClient, fake_agent: _FakeAgent
    ) -> None:
        fake_agent.root_cause = "unknown"
        r = client.post("/investigate", json=self._payload())
        assert r.json()["root_cause"] is None

    def test_investigation_appears_in_history(
        self, client: TestClient, fake_agent: _FakeAgent
    ) -> None:
        r1 = client.post("/investigate", json=self._payload())
        inv_id = r1.json()["investigation_id"]
        # Retrieve individually.
        r2 = client.get(f"/investigations/{inv_id}")
        assert r2.status_code == 200
        assert r2.json()["investigation_id"] == inv_id
        # Appears in list.
        r3 = client.get("/investigations")
        assert r3.status_code == 200
        lst = r3.json()
        assert any(i["investigation_id"] == inv_id for i in lst)


# ---------------------------------------------------------------------------
# /investigations edge cases
# ---------------------------------------------------------------------------


class TestInvestigations:
    def test_get_unknown_id_404(self, client: TestClient) -> None:
        r = client.get("/investigations/inv_nonexistent")
        assert r.status_code == 404

    def test_list_empty_initially(self, client: TestClient) -> None:
        r = client.get("/investigations")
        assert r.status_code == 200
        assert r.json() == []


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestAlertPayloadToExecutorAlert:
    def test_fields(self) -> None:
        alert = AlertPayload(
            service="cartservice",
            metric="latency_p99",
            value=500.0,
            threshold=200.0,
            timestamp="2026-04-21T00:00:00Z",
        )
        exe = _alert_payload_to_executor_alert(alert)
        assert exe["affected_services"] == ["cartservice"]
        assert exe["severity"] == "high"
        assert exe["anomaly_score"] == 500.0
        assert exe["threshold"] == 200.0
        assert exe["timestamp"] == "2026-04-21T00:00:00Z"
        assert exe["alert_metric"] == "latency_p99"
        assert "cartservice" in exe["title"]
        assert "latency_p99" in exe["title"]


class TestRememberFifo:
    def test_evicts_oldest_beyond_cap(self) -> None:
        from collections import OrderedDict

        # Monkeypatch MAX_HISTORY to a small value for a compact test.
        cap = 3
        with patch.object(api_module, "MAX_HISTORY", cap):
            history: OrderedDict[str, InvestigationResponse] = OrderedDict()
            for i in range(cap + 2):
                _remember(
                    history,
                    InvestigationResponse(investigation_id=f"inv_{i:02d}", status="completed"),
                )
            assert len(history) == cap
            # Oldest two should be evicted (inv_00, inv_01); newest should remain.
            assert "inv_00" not in history
            assert "inv_01" not in history
            assert "inv_04" in history


# ---------------------------------------------------------------------------
# /demo/investigate + /demo/investigations/{id}/status
# ---------------------------------------------------------------------------


class TestDemoInvestigate:
    async def _noop_lifecycle(self, *args: Any, **kwargs: Any) -> None:  # noqa: D401
        """Replacement for `_run_demo_lifecycle` that does nothing."""
        return None

    def test_post_starts_lifecycle_and_returns_id(self, client: TestClient) -> None:
        with patch.object(api_module, "_run_demo_lifecycle", self._noop_lifecycle):
            r = client.post("/demo/investigate", json={"service": "cartservice"})
        assert r.status_code == 200
        data = r.json()
        assert data["investigation_id"].startswith("demo_")
        assert data["service"] == "cartservice"
        assert data["fault_type"] == "service_crash"
        assert data["ground_truth"] == "cartservice"
        # A status entry was seeded.
        status = client.get(f"/demo/investigations/{data['investigation_id']}/status")
        assert status.status_code == 200
        s = status.json()
        assert s["service"] == "cartservice"
        assert s["fault_type"] == "service_crash"
        assert s["phase"] in ("queued", "injecting", "waiting", "investigating")

    def test_rejects_invalid_service_with_422(self, client: TestClient) -> None:
        r = client.post("/demo/investigate", json={"service": "currencyservice"})
        assert r.status_code == 422
        r = client.post("/demo/investigate", json={"service": "unknown"})
        assert r.status_code == 422

    def test_all_six_services_produce_valid_response(self, client: TestClient) -> None:
        """Every supported service maps to a registered fault + ground truth."""
        with patch.object(api_module, "_run_demo_lifecycle", self._noop_lifecycle):
            for service, expected_fault in (
                ("cartservice", "service_crash"),
                ("checkoutservice", "memory_pressure"),
                ("frontend", "high_latency"),
                ("paymentservice", "network_partition"),
                ("productcatalogservice", "config_error"),
                ("redis", "connection_exhaustion"),
            ):
                r = client.post("/demo/investigate", json={"service": service})
                assert r.status_code == 200
                data = r.json()
                assert data["service"] == service
                assert data["fault_type"] == expected_fault

    def test_concurrent_demo_returns_409(self, client: TestClient) -> None:
        """When the demo lock is held, a second POST is rejected with 409."""
        with patch.object(app.state.demo_lock, "locked", return_value=True):
            r = client.post("/demo/investigate", json={"service": "cartservice"})
        assert r.status_code == 409
        assert "already running" in r.json()["detail"].lower()


class TestDemoInvestigationsStatus:
    def test_unknown_id_404(self, client: TestClient) -> None:
        r = client.get("/demo/investigations/demo_nonexistent/status")
        assert r.status_code == 404

    def test_returns_seeded_status(self, client: TestClient) -> None:
        """Direct-seeding via _record_demo_status is visible through the API."""
        seeded = DemoInvestigationStatus(
            investigation_id="demo_seeded1",
            service="redis",
            fault_type="connection_exhaustion",
            phase="investigating",
            phase_label="Investigating",
            progress_pct=70,
        )
        _record_demo_status(app, "demo_seeded1", seeded)
        try:
            r = client.get("/demo/investigations/demo_seeded1/status")
            assert r.status_code == 200
            data = r.json()
            assert data["investigation_id"] == "demo_seeded1"
            assert data["phase"] == "investigating"
            assert data["progress_pct"] == 70
        finally:
            app.state.demo_status.pop("demo_seeded1", None)


# ---------------------------------------------------------------------------
# Pure helpers for the demo lifecycle
# ---------------------------------------------------------------------------


class TestBuildDemoAlert:
    def test_alert_has_six_affected_services_and_neutral_title(self) -> None:
        alert = _build_demo_alert("cartservice", "2026-04-22T00:00:00+00:00")
        assert len(alert["affected_services"]) == 6
        assert "currencyservice" not in alert["affected_services"]
        assert "fault" not in alert["title"].lower()
        assert "anomaly" in alert["title"].lower()
        assert alert["severity"] == "critical"
        assert alert["anomaly_score"] == 1.0
        assert alert["alert_service"] == "cartservice"


class TestRecordDemoStatus:
    def test_record_and_update_roundtrip(self, client: TestClient) -> None:
        # client fixture sets up app.state.demo_status.
        inv_id = "demo_roundtrip1"
        try:
            seeded = DemoInvestigationStatus(
                investigation_id=inv_id,
                service="frontend",
                fault_type="high_latency",
                phase="queued",
                phase_label="Queued",
                progress_pct=0,
            )
            _record_demo_status(app, inv_id, seeded)
            assert app.state.demo_status[inv_id].phase == "queued"
            _update_demo_phase(app, inv_id, "injecting")
            assert app.state.demo_status[inv_id].phase == "injecting"
            assert app.state.demo_status[inv_id].progress_pct == 10
            assert app.state.demo_status[inv_id].phase_label == "Injecting fault"
            _update_demo_phase(app, inv_id, "failed", error="boom")
            assert app.state.demo_status[inv_id].phase == "failed"
            assert app.state.demo_status[inv_id].error == "boom"
        finally:
            app.state.demo_status.pop(inv_id, None)

    def test_update_missing_id_is_noop(self, client: TestClient) -> None:
        _update_demo_phase(app, "does_not_exist", "injecting")
        assert "does_not_exist" not in app.state.demo_status


# ---------------------------------------------------------------------------
# _run_demo_lifecycle (unit-tested directly, not through HTTP)
# ---------------------------------------------------------------------------


class TestRunDemoLifecycle:
    """Exercise the async coroutine directly with mocked subprocess + sleep.

    These tests drive the coroutine start-to-finish against a fake app
    state so they're fast and deterministic — no live Docker stack and
    no real 120 s wait.
    """

    @pytest.fixture
    def fake_app_state(self) -> Any:
        """Build a minimal fake app that _run_demo_lifecycle can use."""
        from collections import OrderedDict as _OrderedDict

        class _FakeApp:
            pass

        fake_app = _FakeApp()
        state = _FakeApp()
        state.agent = _FakeAgent()
        state.demo_lock = __import__("asyncio").Lock()
        state.demo_status = _OrderedDict()
        state.investigations = _OrderedDict()
        fake_app.state = state
        return fake_app

    @pytest.mark.asyncio
    async def test_happy_path_completes(self, fake_app_state: Any) -> None:
        # Seed queued status first (as the POST endpoint does).
        seeded = DemoInvestigationStatus(
            investigation_id="demo_happy1",
            service="cartservice",
            fault_type="service_crash",
            phase="queued",
            phase_label="Queued",
            progress_pct=0,
        )
        _record_demo_status(fake_app_state, "demo_happy1", seeded)

        with (
            patch.object(api_module.subprocess, "run", MagicMock(return_value=None)) as mock_run,
            patch.object(api_module.asyncio, "sleep", new=_async_noop),
        ):
            await _run_demo_lifecycle("demo_happy1", "cartservice", fake_app_state)

        # Final phase should be completed.
        final = fake_app_state.state.demo_status["demo_happy1"]
        assert final.phase == "completed"
        assert final.progress_pct == 100
        assert final.result is not None
        assert final.result.root_cause is not None
        assert final.result.root_cause.service == "cartservice"
        # subprocess.run was called twice (inject + restore).
        assert mock_run.call_count == 2
        call_args = [c[0][0] for c in mock_run.call_args_list]
        # Both calls point at the service_crash script.
        assert call_args[0][2] == "inject"
        assert call_args[1][2] == "restore"
        # Investigation was appended to history.
        assert "demo_happy1" in fake_app_state.state.investigations

    @pytest.mark.asyncio
    async def test_restore_runs_when_inject_fails(self, fake_app_state: Any) -> None:
        """If inject raises, restore is still called in `finally`."""
        import subprocess as _subprocess

        seeded = DemoInvestigationStatus(
            investigation_id="demo_failinject",
            service="cartservice",
            fault_type="service_crash",
            phase="queued",
            phase_label="Queued",
            progress_pct=0,
        )
        _record_demo_status(fake_app_state, "demo_failinject", seeded)

        call_log: list[str] = []

        def _flaky_run(*args: Any, **kwargs: Any) -> None:
            # First call is inject → raise. Second call is restore → succeed.
            action = args[0][2]
            call_log.append(action)
            if action == "inject":
                raise _subprocess.CalledProcessError(1, args[0])
            return None

        with (
            patch.object(api_module.subprocess, "run", side_effect=_flaky_run),
            patch.object(api_module.asyncio, "sleep", new=_async_noop),
        ):
            await _run_demo_lifecycle("demo_failinject", "cartservice", fake_app_state)

        # inject was called, then restore was called — order matters.
        assert call_log == ["inject", "restore"]
        final = fake_app_state.state.demo_status["demo_failinject"]
        assert final.phase == "failed"
        assert final.error is not None
        # Lock is released (not held after finally).
        assert not fake_app_state.state.demo_lock.locked()

    @pytest.mark.asyncio
    async def test_restore_runs_when_agent_raises(self, fake_app_state: Any) -> None:
        """If the agent raises, restore still runs and phase=failed."""
        seeded = DemoInvestigationStatus(
            investigation_id="demo_agentraise",
            service="cartservice",
            fault_type="service_crash",
            phase="queued",
            phase_label="Queued",
            progress_pct=0,
        )
        _record_demo_status(fake_app_state, "demo_agentraise", seeded)
        fake_app_state.state.agent = _FakeAgent(raise_exc=True)

        call_log: list[str] = []

        def _run_ok(*args: Any, **kwargs: Any) -> None:
            call_log.append(args[0][2])
            return None

        with (
            patch.object(api_module.subprocess, "run", side_effect=_run_ok),
            patch.object(api_module.asyncio, "sleep", new=_async_noop),
        ):
            await _run_demo_lifecycle("demo_agentraise", "cartservice", fake_app_state)

        assert call_log == ["inject", "restore"]
        final = fake_app_state.state.demo_status["demo_agentraise"]
        assert final.phase == "failed"
        assert "simulated investigation failure" in final.error.lower()
        assert not fake_app_state.state.demo_lock.locked()


async def _async_noop(*args: Any, **kwargs: Any) -> None:
    """Drop-in for asyncio.sleep that returns immediately."""
    return None
