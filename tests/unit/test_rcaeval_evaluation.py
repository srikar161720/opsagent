"""Unit tests for ``tests/evaluation/rcaeval_evaluation.py``.

All tests mock the ``RCAEvalDataAdapter`` (no actual file reads) and the
``AgentExecutor`` (no Docker stack). Verifies filter / subsample / resume
logic, per-case JSON shape, summary math, and CLI argument handling.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from tests.evaluation import rcaeval_evaluation

# ── Test fixtures ────────────────────────────────────────────────────────────


def _synthetic_case(case_id: str, gt_service: str = "cartservice") -> dict:
    """Minimal case dict matching ``RCAEvalDataAdapter.load_case`` output."""
    return {
        "case_id": case_id,
        "metrics": {
            "cartservice": pd.DataFrame({"cpu_usage": [0.1, 0.2, 0.3]}),
            "frontend": pd.DataFrame({"cpu_usage": [0.05, 0.06, 0.07]}),
        },
        "logs": None,
        "anomaly_timestamp": "2024-01-01T00:00:00+00:00",
        "ground_truth": {
            "root_cause_service": gt_service,
            "fault_type": "cpu",
        },
    }


def _mock_agent_always_predicts(service: str, confidence: float = 0.75) -> MagicMock:
    """MagicMock agent that always predicts ``service`` as root cause."""
    agent = MagicMock()
    agent.investigate.return_value = {
        "root_cause": service,
        "root_cause_confidence": confidence,
        "top_3_predictions": [service, "other1", "other2"],
        "confidence": confidence,
        "rca_report": "# stub\n",
        "recommended_actions": [],
    }
    return agent


# ── _case_id_to_filename ─────────────────────────────────────────────────────


class TestCaseIdToFilename:
    def test_slashes_replaced_with_double_underscore(self) -> None:
        assert (
            rcaeval_evaluation._case_id_to_filename("RE2-OB/checkoutservice_cpu/1")
            == "RE2-OB__checkoutservice_cpu__1.json"
        )

    def test_no_slashes_no_change_except_extension(self) -> None:
        assert rcaeval_evaluation._case_id_to_filename("flat") == "flat.json"


# ── _filter_cases ────────────────────────────────────────────────────────────


class TestFilterCases:
    def test_ob_filter_keeps_only_ob_cases(self) -> None:
        cases = ["RE1-OB/a/1", "RE1-SS/b/1", "RE1-TT/c/1", "RE2-OB/d/1"]
        assert rcaeval_evaluation._filter_cases(cases, "-OB/") == [
            "RE1-OB/a/1",
            "RE2-OB/d/1",
        ]

    def test_none_filter_returns_all(self) -> None:
        cases = ["RE1-OB/a/1", "RE1-SS/b/1"]
        assert rcaeval_evaluation._filter_cases(cases, None) == cases


# ── _build_alert ─────────────────────────────────────────────────────────────


class TestBuildAlert:
    def test_populates_expected_fields(self) -> None:
        case = _synthetic_case("RE1-OB/cartservice_cpu/1")
        alert = rcaeval_evaluation._build_alert(case)
        assert alert["anomaly_score"] == 1.0
        assert alert["severity"] == "evaluation"
        # Services come from metric-dict keys, sorted
        assert alert["affected_services"] == ["cartservice", "frontend"]
        # Title should NOT mention "fault injection" (per CLAUDE.md gotcha).
        assert "fault" not in alert["title"].lower()
        assert "anomaly" in alert["title"].lower()


# ── _build_record ────────────────────────────────────────────────────────────


class TestBuildRecord:
    def test_record_has_all_spec_fields(self) -> None:
        case = _synthetic_case("RE2-OB/cartservice_cpu/1", gt_service="cartservice")
        prediction = {
            "root_cause": "cartservice",
            "root_cause_confidence": 0.75,
            "top_3_predictions": ["cartservice", "frontend", "redis"],
            "confidence": 0.75,
        }
        record = rcaeval_evaluation._build_record(case, prediction, "re2", 12.3)
        # All 9 spec fields present
        for field in (
            "case_id",
            "dataset",
            "fault_type",
            "ground_truth",
            "predicted_root_cause",
            "top_3_predictions",
            "confidence",
            "is_correct",
            "notes",
        ):
            assert field in record, f"Missing spec field: {field}"
        assert record["is_correct"] is True
        assert record["fault_type"] == "cpu"
        assert record["ground_truth"] == "cartservice"
        assert record["investigation_duration_seconds"] == 12.3

    def test_wrong_prediction_marks_is_correct_false(self) -> None:
        case = _synthetic_case("RE2-OB/cartservice_cpu/1", gt_service="cartservice")
        prediction = {
            "root_cause": "frontend",
            "top_3_predictions": ["frontend"],
            "confidence": 0.5,
        }
        record = rcaeval_evaluation._build_record(case, prediction, "re2", 1.0)
        assert record["is_correct"] is False


# ── evaluate_on_rcaeval ──────────────────────────────────────────────────────


class TestEvaluateOnRcaeval:
    @patch("tests.evaluation.rcaeval_evaluation.RCAEvalDataAdapter")
    def test_iterates_all_cases_writes_per_case_json(
        self,
        mock_adapter_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        case_ids = ["RE1-OB/cartservice_cpu/1", "RE1-OB/frontend_mem/1"]
        mock_adapter = MagicMock()
        mock_adapter.list_cases.return_value = case_ids
        mock_adapter.load_case.side_effect = [
            _synthetic_case(case_ids[0], gt_service="cartservice"),
            _synthetic_case(case_ids[1], gt_service="frontend"),
        ]
        mock_adapter_cls.return_value = mock_adapter

        agent = _mock_agent_always_predicts("cartservice")

        summary = rcaeval_evaluation.evaluate_on_rcaeval(
            agent=agent,
            dataset_path="/fake/re1",
            results_output_dir=str(tmp_path),
            inter_case_delay_seconds=0,
            rate_limit_retries=0,
        )

        # Per-case JSONs written
        written = sorted(p.name for p in tmp_path.glob("*.json"))
        assert "RE1-OB__cartservice_cpu__1.json" in written
        assert "RE1-OB__frontend_mem__1.json" in written
        assert "summary.json" in written

        # Summary math: 1 correct (cartservice), 1 wrong (frontend_mem GT
        # is frontend but agent always predicts cartservice) → Recall@1 = 0.5
        assert summary["total_cases"] == 2
        assert summary["recall_at_1"] == pytest.approx(0.5)

    @patch("tests.evaluation.rcaeval_evaluation.RCAEvalDataAdapter")
    def test_subsample_caps_iteration(
        self,
        mock_adapter_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        case_ids = [f"RE1-OB/svc{i}_cpu/1" for i in range(5)]
        mock_adapter = MagicMock()
        mock_adapter.list_cases.return_value = case_ids
        mock_adapter.load_case.side_effect = [_synthetic_case(cid) for cid in case_ids]
        mock_adapter_cls.return_value = mock_adapter

        agent = _mock_agent_always_predicts("cartservice")
        summary = rcaeval_evaluation.evaluate_on_rcaeval(
            agent=agent,
            dataset_path="/fake/re1",
            results_output_dir=str(tmp_path),
            subsample=2,
            inter_case_delay_seconds=0,
            rate_limit_retries=0,
        )
        assert summary["total_cases"] == 2
        # Only 2 agent invocations
        assert agent.investigate.call_count == 2

    @patch("tests.evaluation.rcaeval_evaluation.RCAEvalDataAdapter")
    def test_resume_skips_already_written_cases(
        self,
        mock_adapter_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        case_ids = ["RE1-OB/cartservice_cpu/1", "RE1-OB/frontend_cpu/1"]
        mock_adapter = MagicMock()
        mock_adapter.list_cases.return_value = case_ids

        # Pre-populate first case as if a previous run already completed it.
        existing = {
            "case_id": case_ids[0],
            "dataset": "re1",
            "fault_type": "cpu",
            "ground_truth": "cartservice",
            "predicted_root_cause": "cartservice",
            "top_3_predictions": ["cartservice"],
            "confidence": 0.75,
            "is_correct": True,
            "investigation_duration_seconds": 10.0,
            "detection_latency_seconds": 0.0,
            "notes": "",
        }
        with (tmp_path / "RE1-OB__cartservice_cpu__1.json").open("w") as f:
            json.dump(existing, f)

        # load_case should only be called for the SECOND case
        mock_adapter.load_case.return_value = _synthetic_case(case_ids[1], gt_service="frontend")
        mock_adapter_cls.return_value = mock_adapter

        agent = _mock_agent_always_predicts("frontend")
        summary = rcaeval_evaluation.evaluate_on_rcaeval(
            agent=agent,
            dataset_path="/fake/re1",
            results_output_dir=str(tmp_path),
            resume=True,
            inter_case_delay_seconds=0,
            rate_limit_retries=0,
        )

        # Agent only invoked once (second case); total_cases reflects both
        # on-disk records.
        assert agent.investigate.call_count == 1
        assert summary["total_cases"] == 2

    @patch("tests.evaluation.rcaeval_evaluation.RCAEvalDataAdapter")
    def test_system_filter_excludes_non_matching_cases(
        self,
        mock_adapter_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        case_ids = [
            "RE1-OB/cartservice_cpu/1",
            "RE1-SS/carts_cpu/1",
            "RE1-TT/ts-auth_cpu/1",
        ]
        mock_adapter = MagicMock()
        mock_adapter.list_cases.return_value = case_ids
        mock_adapter.load_case.return_value = _synthetic_case(case_ids[0])
        mock_adapter_cls.return_value = mock_adapter

        agent = _mock_agent_always_predicts("cartservice")
        summary = rcaeval_evaluation.evaluate_on_rcaeval(
            agent=agent,
            dataset_path="/fake/re1",
            results_output_dir=str(tmp_path),
            system_filter="-OB/",
            inter_case_delay_seconds=0,
            rate_limit_retries=0,
        )
        # Only the OB case should run
        assert agent.investigate.call_count == 1
        assert summary["total_cases"] == 1

    @patch("tests.evaluation.rcaeval_evaluation.RCAEvalDataAdapter")
    def test_agent_exception_does_not_abort_run(
        self,
        mock_adapter_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        case_ids = ["RE1-OB/cartservice_cpu/1", "RE1-OB/frontend_cpu/1"]
        mock_adapter = MagicMock()
        mock_adapter.list_cases.return_value = case_ids
        mock_adapter.load_case.side_effect = [
            _synthetic_case(case_ids[0]),
            _synthetic_case(case_ids[1]),
        ]
        mock_adapter_cls.return_value = mock_adapter

        agent = MagicMock()
        # First call crashes, second succeeds
        agent.investigate.side_effect = [
            RuntimeError("transient API error"),
            {
                "root_cause": "frontend",
                "top_3_predictions": ["frontend"],
                "confidence": 0.6,
            },
        ]

        summary = rcaeval_evaluation.evaluate_on_rcaeval(
            agent=agent,
            dataset_path="/fake/re1",
            results_output_dir=str(tmp_path),
            inter_case_delay_seconds=0,
            rate_limit_retries=0,
        )
        # Both cases produce per-case JSONs (first has root_cause=unknown)
        assert summary["total_cases"] == 2


# ── CLI arg parsing ──────────────────────────────────────────────────────────


class TestCLIParsing:
    def test_variant_required(self) -> None:
        with pytest.raises(SystemExit):
            rcaeval_evaluation._parse_args([])

    def test_variant_must_be_known(self) -> None:
        with pytest.raises(SystemExit):
            rcaeval_evaluation._parse_args(["--variant", "re99"])

    def test_default_system_filter_is_ob(self) -> None:
        args = rcaeval_evaluation._parse_args(["--variant", "re1"])
        assert args.system_filter == "ob"
        assert args.subsample is None
        assert args.resume is False

    def test_resume_subsample_flags_respected(self) -> None:
        args = rcaeval_evaluation._parse_args(["--variant", "re2", "--subsample", "3", "--resume"])
        assert args.variant == "re2"
        assert args.subsample == 3
        assert args.resume is True

    def test_rate_limit_cli_defaults(self) -> None:
        """Session-15 rate-limit defaults surface on argparse."""
        args = rcaeval_evaluation._parse_args(["--variant", "re1"])
        assert args.inter_case_delay == rcaeval_evaluation._DEFAULT_INTER_CASE_DELAY_SECONDS
        assert args.rate_limit_retries == rcaeval_evaluation._DEFAULT_RATE_LIMIT_RETRIES
        assert (
            args.rate_limit_backoff_seconds
            == rcaeval_evaluation._DEFAULT_RATE_LIMIT_BACKOFF_SECONDS
        )

    def test_rate_limit_cli_overrides(self) -> None:
        """Rate-limit flags override defaults when passed explicitly."""
        args = rcaeval_evaluation._parse_args(
            [
                "--variant",
                "re1",
                "--inter-case-delay",
                "0",
                "--rate-limit-retries",
                "5",
                "--rate-limit-backoff-seconds",
                "30",
            ]
        )
        assert args.inter_case_delay == 0.0
        assert args.rate_limit_retries == 5
        assert args.rate_limit_backoff_seconds == 30.0


# ── Rate-limit detection + retry logic ───────────────────────────────────────


class TestIsRateLimitError:
    """_is_rate_limit_error must detect 429 / RESOURCE_EXHAUSTED in a wide
    range of exception messages and exception chains produced by the
    google-genai → tenacity → langchain → langgraph wrapper stack."""

    def test_direct_resource_exhausted(self) -> None:
        exc = RuntimeError("429 RESOURCE_EXHAUSTED")
        assert rcaeval_evaluation._is_rate_limit_error(exc) is True

    def test_direct_429_too_many(self) -> None:
        exc = RuntimeError("HTTP 429 Too Many Requests from Gemini")
        assert rcaeval_evaluation._is_rate_limit_error(exc) is True

    def test_quota_message(self) -> None:
        exc = RuntimeError("Resource has been exhausted (e.g. check quota).")
        assert rcaeval_evaluation._is_rate_limit_error(exc) is True

    def test_chained_exception(self) -> None:
        """Wrapped 429 (e.g. langchain → langgraph) is detected via cause chain."""
        inner = RuntimeError("429 RESOURCE_EXHAUSTED")
        try:
            raise ValueError("wrapper message, no rate limit marker") from inner
        except ValueError as outer:
            assert rcaeval_evaluation._is_rate_limit_error(outer) is True

    def test_non_rate_limit_error(self) -> None:
        """Normal runtime error must NOT be classified as rate-limit."""
        exc = RuntimeError("NullPointerException in frobnicator")
        assert rcaeval_evaluation._is_rate_limit_error(exc) is False

    def test_connection_error_not_rate_limit(self) -> None:
        """Transient connection errors aren't rate-limits."""
        exc = ConnectionError("Connection refused")
        assert rcaeval_evaluation._is_rate_limit_error(exc) is False


class TestUnknownPrediction:
    def test_stub_shape(self) -> None:
        stub = rcaeval_evaluation._unknown_prediction(RuntimeError("boom"))
        assert stub["root_cause"] == "unknown"
        assert stub["confidence"] == 0.0
        assert stub["top_3_predictions"] == []
        assert "boom" in stub["rca_report"]


class TestInvokeAgentWithRetry:
    """The case-level rate-limit-aware retry wrapper."""

    def _fake_case(self) -> dict:
        return {
            "case_id": "RE1-OB/cart_cpu/1",
            "metrics": {"cartservice": pd.DataFrame({"cpu_usage": [0.1]})},
            "logs": None,
            "anomaly_timestamp": "2024-01-01T00:00:00+00:00",
            "ground_truth": {"root_cause_service": "cartservice", "fault_type": "cpu"},
        }

    def test_success_first_attempt(self) -> None:
        agent = MagicMock()
        agent.investigate.return_value = {"root_cause": "cartservice", "confidence": 0.9}
        result, attempts = rcaeval_evaluation._invoke_agent_with_retry(
            agent,
            self._fake_case(),
            rate_limit_retries=3,
            rate_limit_backoff_seconds=1,
        )
        assert attempts == 1
        assert result["root_cause"] == "cartservice"

    def test_non_rate_limit_error_returns_unknown_immediately(self) -> None:
        """A non-rate-limit error must NOT trigger retries and must
        produce an 'unknown' stub on the first attempt."""
        agent = MagicMock()
        agent.investigate.side_effect = RuntimeError("malformed case")
        with patch("tests.evaluation.rcaeval_evaluation.time.sleep") as mock_sleep:
            result, attempts = rcaeval_evaluation._invoke_agent_with_retry(
                agent,
                self._fake_case(),
                rate_limit_retries=3,
                rate_limit_backoff_seconds=1,
            )
        assert attempts == 1
        assert result["root_cause"] == "unknown"
        # No sleep because no retry happened
        mock_sleep.assert_not_called()

    def test_rate_limit_recovery_on_second_attempt(self) -> None:
        """429 on first call, success on second — returns the success
        with attempts=2, and the retry sleep is the base backoff."""
        agent = MagicMock()
        agent.investigate.side_effect = [
            RuntimeError("429 RESOURCE_EXHAUSTED"),
            {"root_cause": "cartservice", "confidence": 0.7},
        ]
        with patch("tests.evaluation.rcaeval_evaluation.time.sleep") as mock_sleep:
            result, attempts = rcaeval_evaluation._invoke_agent_with_retry(
                agent,
                self._fake_case(),
                rate_limit_retries=3,
                rate_limit_backoff_seconds=10,
            )
        assert attempts == 2
        assert result["root_cause"] == "cartservice"
        # First retry waits base * 2**0 = 10s
        mock_sleep.assert_called_once_with(10.0)

    def test_rate_limit_exponential_backoff(self) -> None:
        """Three 429s, success on 4th attempt — backoff doubles each time:
        10s → 20s → 40s."""
        agent = MagicMock()
        agent.investigate.side_effect = [
            RuntimeError("429 RESOURCE_EXHAUSTED"),
            RuntimeError("RESOURCE_EXHAUSTED quota"),
            RuntimeError("429 Too Many Requests"),
            {"root_cause": "cartservice", "confidence": 0.5},
        ]
        with patch("tests.evaluation.rcaeval_evaluation.time.sleep") as mock_sleep:
            result, attempts = rcaeval_evaluation._invoke_agent_with_retry(
                agent,
                self._fake_case(),
                rate_limit_retries=5,
                rate_limit_backoff_seconds=10,
            )
        assert attempts == 4
        assert result["root_cause"] == "cartservice"
        # Sleeps: 10 (1st→2nd), 20 (2nd→3rd), 40 (3rd→4th)
        assert [call.args[0] for call in mock_sleep.call_args_list] == [10, 20, 40]

    def test_rate_limit_exhausted_returns_unknown(self) -> None:
        """Persistent 429s + retries exhausted → 'unknown' record."""
        agent = MagicMock()
        agent.investigate.side_effect = RuntimeError("429 RESOURCE_EXHAUSTED")
        with patch("tests.evaluation.rcaeval_evaluation.time.sleep"):
            result, attempts = rcaeval_evaluation._invoke_agent_with_retry(
                agent,
                self._fake_case(),
                rate_limit_retries=2,  # 1 initial + 2 retries = 3 attempts
                rate_limit_backoff_seconds=1,
            )
        assert attempts == 3
        assert result["root_cause"] == "unknown"
        assert "429 RESOURCE_EXHAUSTED" in result["rca_report"]


class TestInterCaseDelay:
    """The inter-case sleep in ``evaluate_on_rcaeval`` main loop."""

    @patch("tests.evaluation.rcaeval_evaluation.RCAEvalDataAdapter")
    @patch("tests.evaluation.rcaeval_evaluation.time.sleep")
    def test_sleeps_between_cases_but_not_after_last(
        self,
        mock_sleep: MagicMock,
        mock_adapter_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        """3 cases → sleep called 2 times (after case 1 and 2, not case 3)."""
        case_ids = ["RE1-OB/a/1", "RE1-OB/b/1", "RE1-OB/c/1"]
        mock_adapter = MagicMock()
        mock_adapter.list_cases.return_value = case_ids
        mock_adapter.load_case.side_effect = [_synthetic_case(cid) for cid in case_ids]
        mock_adapter_cls.return_value = mock_adapter

        agent = _mock_agent_always_predicts("cartservice")
        rcaeval_evaluation.evaluate_on_rcaeval(
            agent=agent,
            dataset_path="/fake/re1",
            results_output_dir=str(tmp_path),
            inter_case_delay_seconds=7.0,
            rate_limit_retries=0,
        )

        delay_calls = [call for call in mock_sleep.call_args_list if call.args[0] == 7.0]
        assert len(delay_calls) == 2, (
            f"Expected 2 inter-case sleeps of 7.0s (between 3 cases), "
            f"got {len(delay_calls)}"
        )

    @patch("tests.evaluation.rcaeval_evaluation.RCAEvalDataAdapter")
    @patch("tests.evaluation.rcaeval_evaluation.time.sleep")
    def test_zero_delay_never_sleeps_between_cases(
        self,
        mock_sleep: MagicMock,
        mock_adapter_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        """inter_case_delay_seconds=0 → no sleep calls for inter-case delay."""
        case_ids = ["RE1-OB/a/1", "RE1-OB/b/1"]
        mock_adapter = MagicMock()
        mock_adapter.list_cases.return_value = case_ids
        mock_adapter.load_case.side_effect = [_synthetic_case(cid) for cid in case_ids]
        mock_adapter_cls.return_value = mock_adapter

        agent = _mock_agent_always_predicts("cartservice")
        rcaeval_evaluation.evaluate_on_rcaeval(
            agent=agent,
            dataset_path="/fake/re1",
            results_output_dir=str(tmp_path),
            inter_case_delay_seconds=0,
            rate_limit_retries=0,
        )

        assert mock_sleep.call_count == 0


class TestRetryRecordsNotes:
    """When the evaluator retries a case due to rate-limiting, the per-case
    JSON's ``notes`` field must record that fact for downstream analysis."""

    @patch("tests.evaluation.rcaeval_evaluation.RCAEvalDataAdapter")
    @patch("tests.evaluation.rcaeval_evaluation.time.sleep")
    def test_successful_retry_records_notes(
        self,
        mock_sleep: MagicMock,
        mock_adapter_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_adapter = MagicMock()
        mock_adapter.list_cases.return_value = ["RE1-OB/cart_cpu/1"]
        mock_adapter.load_case.return_value = _synthetic_case(
            "RE1-OB/cart_cpu/1", gt_service="cartservice"
        )
        mock_adapter_cls.return_value = mock_adapter

        agent = MagicMock()
        agent.investigate.side_effect = [
            RuntimeError("429 RESOURCE_EXHAUSTED"),
            {
                "root_cause": "cartservice",
                "top_3_predictions": ["cartservice"],
                "confidence": 0.7,
            },
        ]

        rcaeval_evaluation.evaluate_on_rcaeval(
            agent=agent,
            dataset_path="/fake/re1",
            results_output_dir=str(tmp_path),
            inter_case_delay_seconds=0,
            rate_limit_retries=3,
            rate_limit_backoff_seconds=1,
        )

        record_path = tmp_path / "RE1-OB__cart_cpu__1.json"
        record = json.loads(record_path.read_text())
        assert "retried" in record["notes"]
        assert record["is_correct"] is True


# ── OB-services whitelist ────────────────────────────────────────────────────


class TestOBWhitelist:
    """Tests for the OB services whitelist that filters out RCAEval dataset
    noise (PassthroughCluster, Sock Shop leakage, loadgenerator, etc.) before
    the agent sees the case."""

    def test_whitelist_contains_all_11_ob_services(self) -> None:
        """The OB whitelist must cover every RCAEval-OB ground-truth service."""
        expected = {
            "adservice",
            "cartservice",
            "checkoutservice",
            "currencyservice",
            "emailservice",
            "frontend",
            "paymentservice",
            "productcatalogservice",
            "recommendationservice",
            "redis",
            "shippingservice",
        }
        assert expected == rcaeval_evaluation._OB_SERVICES

    def test_whitelist_rejects_passthroughcluster(self) -> None:
        """PassthroughCluster is Istio/Envoy noise, not a real service."""
        assert "PassthroughCluster" not in rcaeval_evaluation._OB_SERVICES
        assert "InboundPassthroughClusterIpv4" not in rcaeval_evaluation._OB_SERVICES

    def test_whitelist_rejects_frontend_external_and_main(self) -> None:
        """frontend-external / main are pod labels, not services."""
        assert "frontend-external" not in rcaeval_evaluation._OB_SERVICES
        assert "main" not in rcaeval_evaluation._OB_SERVICES
        assert "frontend-check" not in rcaeval_evaluation._OB_SERVICES
        assert "loadgenerator" not in rcaeval_evaluation._OB_SERVICES

    def test_whitelist_rejects_sock_shop_leakage(self) -> None:
        """RE3-OB CSVs leak the full Sock Shop stack — must be excluded."""
        for noise in (
            "carts",
            "carts-db",
            "catalogue",
            "catalogue-db",
            "orders",
            "orders-db",
            "payment",
            "session-db",
            "user",
            "user-db",
            "shipping",
            "queue-master",
            "rabbitmq",
            "rabbitmq-exporter",
            "front-end",
        ):
            assert noise not in rcaeval_evaluation._OB_SERVICES, (
                f"Sock Shop noise '{noise}' leaked into OB whitelist"
            )

    def test_system_whitelists_ob_matches_ob_services(self) -> None:
        """_SYSTEM_WHITELISTS['ob'] points to _OB_SERVICES."""
        assert rcaeval_evaluation._SYSTEM_WHITELISTS["ob"] is rcaeval_evaluation._OB_SERVICES

    def test_system_whitelists_ss_tt_all_are_none(self) -> None:
        """SS / TT / all modes disable the whitelist (out of current scope)."""
        assert rcaeval_evaluation._SYSTEM_WHITELISTS["ss"] is None
        assert rcaeval_evaluation._SYSTEM_WHITELISTS["tt"] is None
        assert rcaeval_evaluation._SYSTEM_WHITELISTS["all"] is None

    def test_filter_drops_noise_services(self) -> None:
        """_filter_case_to_whitelist removes services not in the whitelist."""
        polluted_case = {
            "case_id": "RE1-OB/adservice_cpu/1",
            "metrics": {
                "adservice": pd.DataFrame({"cpu_usage": [0.1]}),
                "PassthroughCluster": pd.DataFrame({"cpu_usage": [0.5]}),
                "frontend-external": pd.DataFrame({"cpu_usage": [0.2]}),
                "main": pd.DataFrame({"cpu_usage": [0.3]}),
                "carts": pd.DataFrame({"cpu_usage": [0.4]}),
                "cartservice": pd.DataFrame({"cpu_usage": [0.15]}),
            },
            "logs": None,
            "anomaly_timestamp": "2024-01-01T00:00:00+00:00",
            "ground_truth": {"root_cause_service": "adservice", "fault_type": "cpu"},
        }
        filtered = rcaeval_evaluation._filter_case_to_whitelist(
            polluted_case, rcaeval_evaluation._OB_SERVICES
        )
        assert set(filtered["metrics"].keys()) == {"adservice", "cartservice"}
        # Non-metric fields unchanged
        assert filtered["case_id"] == "RE1-OB/adservice_cpu/1"
        assert filtered["ground_truth"] == polluted_case["ground_truth"]
        # Original case is not mutated
        assert "PassthroughCluster" in polluted_case["metrics"]

    def test_filter_is_noop_when_whitelist_is_none(self) -> None:
        """Whitelist=None disables filtering; case returned as-is."""
        case = {
            "case_id": "x",
            "metrics": {"anyservice": pd.DataFrame({"cpu_usage": [0.1]})},
            "logs": None,
            "anomaly_timestamp": "2024-01-01T00:00:00+00:00",
            "ground_truth": {"root_cause_service": "x", "fault_type": "cpu"},
        }
        result = rcaeval_evaluation._filter_case_to_whitelist(case, None)
        assert result is case

    def test_filter_preserves_ground_truth_service(self) -> None:
        """Every RCAEval-OB fault-directory service is in the whitelist —
        so the filter can never drop the ground-truth service."""
        ground_truth_services = {
            # Union across RE1-OB, RE2-OB, RE3-OB fault directories
            "adservice",
            "cartservice",
            "checkoutservice",
            "currencyservice",
            "emailservice",
            "productcatalogservice",
            "recommendationservice",
        }
        assert ground_truth_services.issubset(rcaeval_evaluation._OB_SERVICES)

    @patch("tests.evaluation.rcaeval_evaluation.RCAEvalDataAdapter")
    def test_evaluate_applies_whitelist_to_each_case(
        self,
        mock_adapter_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        """End-to-end: evaluate_on_rcaeval with whitelist passes only
        whitelisted services into agent.investigate."""
        polluted = {
            "case_id": "RE1-OB/adservice_cpu/1",
            "metrics": {
                "adservice": pd.DataFrame({"cpu_usage": [0.1]}),
                "PassthroughCluster": pd.DataFrame({"cpu_usage": [0.5]}),
                "cartservice": pd.DataFrame({"cpu_usage": [0.15]}),
            },
            "logs": None,
            "anomaly_timestamp": "2024-01-01T00:00:00+00:00",
            "ground_truth": {"root_cause_service": "adservice", "fault_type": "cpu"},
        }
        mock_adapter = MagicMock()
        mock_adapter.list_cases.return_value = ["RE1-OB/adservice_cpu/1"]
        mock_adapter.load_case.return_value = polluted
        mock_adapter_cls.return_value = mock_adapter

        agent = MagicMock()
        agent.investigate.return_value = {
            "root_cause": "adservice",
            "top_3_predictions": ["adservice"],
            "confidence": 0.7,
        }

        rcaeval_evaluation.evaluate_on_rcaeval(
            agent=agent,
            dataset_path="/fake/re1",
            results_output_dir=str(tmp_path),
            whitelist=rcaeval_evaluation._OB_SERVICES,
            inter_case_delay_seconds=0,
            rate_limit_retries=0,
        )

        agent.investigate.assert_called_once()
        call_kwargs = agent.investigate.call_args.kwargs
        passed_metrics = call_kwargs.get("metrics", {})
        assert set(passed_metrics.keys()) == {"adservice", "cartservice"}
        assert "PassthroughCluster" not in passed_metrics
