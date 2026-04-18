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
    _shuffled_faults,
    run_fault_injection,
)


# ---------------------------------------------------------------------------
# TestFaultScripts
# ---------------------------------------------------------------------------
class TestFaultScripts:
    def test_all_scripts_in_registry(self) -> None:
        # 7 active fault types (cpu_throttling removed in Session 12 —
        # undetectable on idle demo; see fault_injection_suite.py docstring).
        assert len(FAULT_SCRIPTS) == 7

    def test_all_ground_truths_defined(self) -> None:
        assert len(GROUND_TRUTH) == 7

    def test_cpu_throttling_excluded(self) -> None:
        """cpu_throttling was removed in Session 12 after diagnosis showed
        the fault is undetectable on the idle demo (baseline CPU 0.09% of
        a core is far below any reasonable Docker cap)."""
        assert "cpu_throttling" not in FAULT_SCRIPTS
        assert "cpu_throttling" not in GROUND_TRUTH

    def test_main_rejects_unknown_fault_type(
        self,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Invoking the suite with --fault <unknown> must emit a helpful
        error (not a KeyError traceback) and exit non-zero."""
        from tests.evaluation.fault_injection_suite import main as suite_main

        monkeypatch.setattr(
            "sys.argv",
            ["fault_injection_suite", "--fault", "cpu_throttling", "--repetitions", "1"],
        )
        with pytest.raises(SystemExit) as exc_info:
            suite_main()
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "cpu_throttling" in captured.err
        assert "not a registered fault type" in captured.err

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

    def test_config_error_targets_productcatalogservice(self) -> None:
        """config_error must target productcatalogservice (retargeted from currencyservice
        in Session 12 due to v1.10.0 baseline crash-loop issue)."""
        assert GROUND_TRUTH["config_error"] == "productcatalogservice"


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
    def test_alert_excludes_currencyservice(
        self,
        mock_sleep: MagicMock,
        mock_subprocess: MagicMock,
        mock_agent: MagicMock,
        tmp_path: Path,
    ) -> None:
        """The alert sent to the agent must NOT list currencyservice as an
        affected service. v1.10.0 currencyservice SIGSEGVs continuously in
        baseline; surfacing it to the agent causes consistent misattribution
        (see Session 12 diagnosis in fault_injection_suite.py comment)."""
        run_fault_injection(
            "service_crash", 1, tmp_path, mock_agent, max_wait_seconds=120
        )
        # The mock agent's investigate() received the alert as a kwarg.
        alert = mock_agent.investigate.call_args.kwargs["alert"]
        affected = alert["affected_services"]
        assert "currencyservice" not in affected
        # The 6 services that ARE legitimate investigation targets.
        assert set(affected) == {
            "cartservice",
            "checkoutservice",
            "frontend",
            "paymentservice",
            "productcatalogservice",
            "redis",
        }

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
        """Verify the actual evaluation_scenarios.yaml loads correctly.
        7 active fault types after cpu_throttling removal in Session 12."""
        result = _load_per_fault_cooldowns()
        if result:  # Only if the file exists in this environment
            assert len(result) == 7
            assert all(isinstance(v, int) for v in result.values())

    def test_cooldowns_destructive_faults_are_300s(self) -> None:
        """service_crash and cascading_failure use 300s cooldown (raised from 120/240
        in Session 12) — their container restarts leak probe_up=0 residue for
        >5 minutes, so a shorter cooldown pollutes the next test's 10-min window."""
        result = _load_per_fault_cooldowns()
        if result:
            assert result["service_crash"] == 300
            assert result["cascading_failure"] == 300


# ---------------------------------------------------------------------------
# TestShuffledFaults
# ---------------------------------------------------------------------------
class TestShuffledFaults:
    def test_preserves_full_set(self) -> None:
        """Shuffle returns a permutation — same elements, possibly reordered."""
        faults = ["a", "b", "c", "d", "e", "f", "g", "h"]
        result = _shuffled_faults(faults, seed=42)
        assert sorted(result) == sorted(faults)

    def test_deterministic_for_same_seed(self) -> None:
        """Same seed → same permutation."""
        faults = list(FAULT_SCRIPTS.keys())
        a = _shuffled_faults(faults, seed=42)
        b = _shuffled_faults(faults, seed=42)
        assert a == b

    def test_different_seed_different_order(self) -> None:
        """Different seeds produce (typically) different orderings."""
        faults = list(FAULT_SCRIPTS.keys())
        a = _shuffled_faults(faults, seed=1)
        b = _shuffled_faults(faults, seed=99)
        # With 8 faults and two different seeds, equal orderings are
        # astronomically unlikely. If this ever flakes, the seeds collide
        # on a ~1/40320 probability.
        assert a != b

    def test_original_not_mutated(self) -> None:
        """Shuffle must not modify the caller's list."""
        original = ["a", "b", "c"]
        _shuffled_faults(original, seed=7)
        assert original == ["a", "b", "c"]
