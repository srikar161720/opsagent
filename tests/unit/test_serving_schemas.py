"""Tests for src.serving.schemas.

Pure Pydantic validation — no FastAPI / Streamlit runtime needed.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.serving.schemas import (
    AlertPayload,
    DemoInvestigationRequest,
    DemoInvestigationStatus,
    HealthStatus,
    InvestigationRequest,
    InvestigationResponse,
    RootCauseResult,
    TopologyResponse,
)


class TestAlertPayload:
    def test_valid(self) -> None:
        payload = AlertPayload(
            service="cartservice",
            metric="latency_p99",
            value=500.0,
            threshold=200.0,
            timestamp="2026-04-21T00:00:00Z",
        )
        assert payload.service == "cartservice"
        assert payload.value == 500.0

    def test_coerces_int_to_float(self) -> None:
        payload = AlertPayload(
            service="cartservice",
            metric="latency_p99",
            value=500,  # type: ignore[arg-type]
            threshold=200,  # type: ignore[arg-type]
            timestamp="t",
        )
        assert isinstance(payload.value, float)
        assert isinstance(payload.threshold, float)

    def test_rejects_missing_required_fields(self) -> None:
        with pytest.raises(ValidationError):
            AlertPayload(service="cartservice", metric="latency_p99")  # type: ignore[call-arg]


class TestInvestigationRequest:
    def test_defaults(self) -> None:
        req = InvestigationRequest(
            alert=AlertPayload(
                service="cartservice",
                metric="latency_p99",
                value=1.0,
                threshold=0.5,
                timestamp="t",
            ),
        )
        assert req.time_range_minutes == 30

    def test_rejects_out_of_range_time_window(self) -> None:
        alert = AlertPayload(
            service="cartservice",
            metric="latency_p99",
            value=1.0,
            threshold=0.5,
            timestamp="t",
        )
        with pytest.raises(ValidationError):
            InvestigationRequest(alert=alert, time_range_minutes=0)
        with pytest.raises(ValidationError):
            InvestigationRequest(alert=alert, time_range_minutes=181)


class TestRootCauseResult:
    def test_valid(self) -> None:
        rc = RootCauseResult(service="cartservice", confidence=0.75)
        assert rc.component is None

    def test_confidence_range(self) -> None:
        with pytest.raises(ValidationError):
            RootCauseResult(service="cartservice", confidence=1.5)
        with pytest.raises(ValidationError):
            RootCauseResult(service="cartservice", confidence=-0.1)


class TestInvestigationResponse:
    def test_minimal(self) -> None:
        resp = InvestigationResponse(investigation_id="inv_abc", status="completed")
        assert resp.top_3_predictions == []
        assert resp.evidence == []
        assert resp.recommendations == []
        assert resp.duration_seconds == 0.0

    def test_roundtrip_json(self) -> None:
        resp = InvestigationResponse(
            investigation_id="inv_abc",
            status="completed",
            root_cause=RootCauseResult(service="cartservice", confidence=0.75),
            top_3_predictions=["cartservice", "redis", "checkoutservice"],
            report="# Root cause\ncartservice",
            recommendations=["Restart cartservice"],
            duration_seconds=42.0,
        )
        data = resp.model_dump_json()
        parsed = InvestigationResponse.model_validate_json(data)
        assert parsed.root_cause is not None
        assert parsed.root_cause.service == "cartservice"
        assert parsed.top_3_predictions[0] == "cartservice"


class TestHealthStatus:
    def test_valid(self) -> None:
        h = HealthStatus(
            status="healthy",
            components={"prometheus": "connected", "llm": "available"},
        )
        assert h.components["prometheus"] == "connected"


class TestTopologyResponse:
    def test_defaults(self) -> None:
        t = TopologyResponse()
        assert t.nodes == []
        assert t.edges == []
        assert t.subgraph_of is None

    def test_roundtrip(self) -> None:
        t = TopologyResponse(
            nodes=[{"name": "cartservice"}],
            edges=[{"source": "cartservice", "target": "redis"}],
            subgraph_of="cartservice",
        )
        dumped = t.model_dump()
        assert dumped["nodes"][0]["name"] == "cartservice"
        assert dumped["subgraph_of"] == "cartservice"


class TestDemoInvestigationRequest:
    def test_valid_service(self) -> None:
        req = DemoInvestigationRequest(service="cartservice")
        assert req.service == "cartservice"

    def test_all_six_services_accepted(self) -> None:
        for svc in (
            "cartservice",
            "checkoutservice",
            "frontend",
            "paymentservice",
            "productcatalogservice",
            "redis",
        ):
            req = DemoInvestigationRequest(service=svc)  # type: ignore[arg-type]
            assert req.service == svc

    def test_rejects_invalid_service(self) -> None:
        with pytest.raises(ValidationError):
            DemoInvestigationRequest(service="currencyservice")  # type: ignore[arg-type]
        with pytest.raises(ValidationError):
            DemoInvestigationRequest(service="unknown")  # type: ignore[arg-type]


class TestDemoInvestigationStatus:
    def test_minimal_queued(self) -> None:
        status = DemoInvestigationStatus(
            investigation_id="demo_abc123",
            service="cartservice",
            fault_type="service_crash",
            phase="queued",
            phase_label="Queued",
        )
        assert status.progress_pct == 0
        assert status.started_at is None
        assert status.completed_at is None
        assert status.error is None
        assert status.result is None

    def test_with_result_roundtrip(self) -> None:
        result = InvestigationResponse(
            investigation_id="demo_abc123",
            status="completed",
            root_cause=RootCauseResult(service="cartservice", confidence=0.75),
            top_3_predictions=["cartservice", "redis", "checkoutservice"],
            report="# Root cause\ncartservice",
            duration_seconds=24.1,
        )
        status = DemoInvestigationStatus(
            investigation_id="demo_abc123",
            service="cartservice",
            fault_type="service_crash",
            phase="completed",
            phase_label="Completed",
            progress_pct=100,
            started_at="2026-04-22T00:00:00+00:00",
            completed_at="2026-04-22T00:03:00+00:00",
            result=result,
        )
        dumped = status.model_dump_json()
        parsed = DemoInvestigationStatus.model_validate_json(dumped)
        assert parsed.result is not None
        assert parsed.result.root_cause is not None
        assert parsed.result.root_cause.service == "cartservice"
        assert parsed.phase == "completed"

    def test_rejects_invalid_phase(self) -> None:
        with pytest.raises(ValidationError):
            DemoInvestigationStatus(
                investigation_id="demo_abc123",
                service="cartservice",
                fault_type="service_crash",
                phase="unknown_phase",  # type: ignore[arg-type]
                phase_label="Unknown",
            )

    def test_rejects_out_of_range_progress(self) -> None:
        with pytest.raises(ValidationError):
            DemoInvestigationStatus(
                investigation_id="demo_abc123",
                service="cartservice",
                fault_type="service_crash",
                phase="waiting",
                phase_label="Waiting",
                progress_pct=101,
            )
