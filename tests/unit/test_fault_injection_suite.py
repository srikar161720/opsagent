"""Tests for tests.evaluation.fault_injection_suite."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests.evaluation.fault_injection_suite import (
    FAULT_SCRIPTS,
    GROUND_TRUTH,
    _load_per_fault_cooldowns,
    _resolve_script,
    run_fault_injection,
)


# ---------------------------------------------------------------------------
# TestFaultScripts
# ---------------------------------------------------------------------------
class TestFaultScripts:
    def test_all_scripts_in_registry(self) -> None:
        assert len(FAULT_SCRIPTS) == 8

    def test_all_ground_truths_defined(self) -> None:
        assert len(GROUND_TRUTH) == 8

    def test_scripts_match_ground_truths(self) -> None:
        assert set(FAULT_SCRIPTS.keys()) == set(GROUND_TRUTH.keys())

    def test_all_script_paths_end_with_sh(self) -> None:
        for path in FAULT_SCRIPTS.values():
            assert path.endswith(".sh")

    def test_ground_truth_values_are_known_services(self) -> None:
        known = {
            "cartservice",
            "checkoutservice",
            "currencyservice",
            "frontend",
            "paymentservice",
            "productcatalogservice",
            "redis",
        }
        for svc in GROUND_TRUTH.values():
            assert svc in known


# ---------------------------------------------------------------------------
# TestResolveScript
# ---------------------------------------------------------------------------
class TestResolveScript:
    def test_returns_absolute_path(self) -> None:
        path = _resolve_script("service_crash")
        assert Path(path).is_absolute()

    def test_contains_script_name(self) -> None:
        path = _resolve_script("service_crash")
        assert "01_service_crash.sh" in path


# ---------------------------------------------------------------------------
# TestRunFaultInjection
# ---------------------------------------------------------------------------
class TestRunFaultInjection:
    @pytest.fixture()
    def mock_agent(self) -> MagicMock:
        agent = MagicMock()
        agent.investigate.return_value = {
            "root_cause": "cartservice",
            "root_cause_confidence": 0.85,
            "top_3_predictions": ["cartservice", "redis", "frontend"],
            "confidence": 0.85,
            "rca_report": "## Root Cause: cartservice\n\nTest report.",
            "recommended_actions": ["Restart cartservice"],
        }
        return agent

    @patch("tests.evaluation.fault_injection_suite.subprocess.run")
    @patch("tests.evaluation.fault_injection_suite.time.sleep")
    def test_successful_injection(
        self,
        mock_sleep: MagicMock,
        mock_subprocess: MagicMock,
        mock_agent: MagicMock,
        tmp_path: Path,
    ) -> None:
        result = run_fault_injection(
            "service_crash",
            1,
            tmp_path,
            mock_agent,
            max_wait_seconds=120,
        )
        assert result["status"] == "completed"
        assert result["ground_truth"] == "cartservice"
        assert result["predicted_root_cause"] == "cartservice"
        assert result["is_correct"] is True

    @patch("tests.evaluation.fault_injection_suite.subprocess.run")
    @patch("tests.evaluation.fault_injection_suite.time.sleep")
    def test_failed_injection(
        self,
        mock_sleep: MagicMock,
        mock_subprocess: MagicMock,
        mock_agent: MagicMock,
        tmp_path: Path,
    ) -> None:
        import subprocess

        mock_subprocess.side_effect = subprocess.CalledProcessError(1, "bash")

        result = run_fault_injection(
            "service_crash",
            1,
            tmp_path,
            mock_agent,
        )
        assert result["status"] == "failed"

    @patch("tests.evaluation.fault_injection_suite.subprocess.run")
    @patch("tests.evaluation.fault_injection_suite.time.sleep")
    def test_calls_inject_arg(
        self,
        mock_sleep: MagicMock,
        mock_subprocess: MagicMock,
        mock_agent: MagicMock,
        tmp_path: Path,
    ) -> None:
        run_fault_injection("service_crash", 1, tmp_path, mock_agent)
        first_call = mock_subprocess.call_args_list[0]
        args = first_call[0][0]
        assert args[0] == "bash"
        assert args[2] == "inject"

    @patch("tests.evaluation.fault_injection_suite.subprocess.run")
    @patch("tests.evaluation.fault_injection_suite.time.sleep")
    def test_calls_restore_after(
        self,
        mock_sleep: MagicMock,
        mock_subprocess: MagicMock,
        mock_agent: MagicMock,
        tmp_path: Path,
    ) -> None:
        run_fault_injection("service_crash", 1, tmp_path, mock_agent)
        # Last subprocess call should be restore
        last_call = mock_subprocess.call_args_list[-1]
        args = last_call[0][0]
        assert args[2] == "restore"

    @patch("tests.evaluation.fault_injection_suite.subprocess.run")
    @patch("tests.evaluation.fault_injection_suite.time.sleep")
    def test_saves_result_json(
        self,
        mock_sleep: MagicMock,
        mock_subprocess: MagicMock,
        mock_agent: MagicMock,
        tmp_path: Path,
    ) -> None:
        run_fault_injection("service_crash", 1, tmp_path, mock_agent)
        json_file = tmp_path / "service_crash_run_1.json"
        assert json_file.exists()
        data = json.loads(json_file.read_text())
        assert data["test_id"] == "service_crash_run_1"

    @patch("tests.evaluation.fault_injection_suite.subprocess.run")
    @patch("tests.evaluation.fault_injection_suite.time.sleep")
    def test_saves_rca_report(
        self,
        mock_sleep: MagicMock,
        mock_subprocess: MagicMock,
        mock_agent: MagicMock,
        tmp_path: Path,
    ) -> None:
        run_fault_injection("service_crash", 1, tmp_path, mock_agent)
        report_file = tmp_path / "reports" / "service_crash_run_1.md"
        assert report_file.exists()
        assert "cartservice" in report_file.read_text()

    @patch("tests.evaluation.fault_injection_suite.subprocess.run")
    @patch("tests.evaluation.fault_injection_suite.time.sleep")
    def test_wrong_prediction_flagged(
        self,
        mock_sleep: MagicMock,
        mock_subprocess: MagicMock,
        tmp_path: Path,
    ) -> None:
        agent = MagicMock()
        agent.investigate.return_value = {
            "root_cause": "redis",  # Wrong! Ground truth is frontend
            "top_3_predictions": ["redis"],
            "confidence": 0.5,
            "rca_report": "",
        }
        result = run_fault_injection("high_latency", 1, tmp_path, agent)
        assert result["is_correct"] is False
        assert result["predicted_root_cause"] == "redis"

    @patch("tests.evaluation.fault_injection_suite.subprocess.run")
    @patch("tests.evaluation.fault_injection_suite.time.sleep")
    def test_detection_latency_computed(
        self,
        mock_sleep: MagicMock,
        mock_subprocess: MagicMock,
        mock_agent: MagicMock,
        tmp_path: Path,
    ) -> None:
        result = run_fault_injection("service_crash", 1, tmp_path, mock_agent)
        assert "detection_latency_seconds" in result
        assert result["detection_latency_seconds"] >= 0


# ---------------------------------------------------------------------------
# TestLoadPerFaultCooldowns
# ---------------------------------------------------------------------------
class TestLoadPerFaultCooldowns:
    def test_loads_from_yaml(self, tmp_path: Path) -> None:
        config = {
            "fault_types": [
                {"name": "service_crash", "cooldown_seconds": 120},
                {"name": "cascading_failure", "cooldown_seconds": 240},
            ]
        }
        config_file = tmp_path / "eval.yaml"
        config_file.write_text(
            json.dumps(config)  # yaml.dump would be better but json is valid yaml
        )
        result = _load_per_fault_cooldowns(str(config_file))
        assert result["service_crash"] == 120
        assert result["cascading_failure"] == 240

    def test_missing_file(self) -> None:
        result = _load_per_fault_cooldowns("/nonexistent/path.yaml")
        assert result == {}

    def test_defaults_to_300_if_no_cooldown(self, tmp_path: Path) -> None:
        config = {"fault_types": [{"name": "test_fault"}]}
        config_file = tmp_path / "eval.yaml"
        config_file.write_text(json.dumps(config))
        result = _load_per_fault_cooldowns(str(config_file))
        assert result["test_fault"] == 300

    def test_loads_real_config(self) -> None:
        """Verify the actual evaluation_scenarios.yaml loads correctly."""
        result = _load_per_fault_cooldowns()
        if result:  # Only if the file exists in this environment
            assert len(result) == 8
            assert all(isinstance(v, int) for v in result.values())
