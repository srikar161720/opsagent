"""Tests for scripts.inject_faults."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure the project root is on the path for imports
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.inject_faults import preflight_checks, print_summary  # noqa: E402


# ---------------------------------------------------------------------------
# TestPreflightChecks
# ---------------------------------------------------------------------------
class TestPreflightChecks:
    @patch("scripts.inject_faults.subprocess.run")
    @patch("scripts.inject_faults.requests.get")
    @patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"})
    def test_all_checks_pass(
        self,
        mock_get: MagicMock,
        mock_subprocess: MagicMock,
    ) -> None:
        mock_subprocess.return_value = MagicMock(returncode=0, stdout="running")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_get.return_value = mock_resp

        errors = preflight_checks()
        # May have errors about fault scripts or demo services depending on env
        # but Docker, API key, Prometheus, and Loki checks should pass
        docker_errors = [e for e in errors if "Docker daemon" in e]
        api_errors = [e for e in errors if "GEMINI_API_KEY" in e]
        prom_errors = [e for e in errors if "Prometheus" in e]
        loki_errors = [e for e in errors if "Loki" in e]
        assert docker_errors == []
        assert api_errors == []
        assert prom_errors == []
        assert loki_errors == []

    @patch("scripts.inject_faults.subprocess.run")
    @patch("scripts.inject_faults.requests.get")
    @patch.dict("os.environ", {"GEMINI_API_KEY": ""}, clear=False)
    def test_missing_api_key(
        self,
        mock_get: MagicMock,
        mock_subprocess: MagicMock,
    ) -> None:
        mock_subprocess.return_value = MagicMock(returncode=0, stdout="ok")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_get.return_value = mock_resp

        errors = preflight_checks()
        api_errors = [e for e in errors if "GEMINI_API_KEY" in e]
        assert len(api_errors) == 1

    @patch("scripts.inject_faults.subprocess.run")
    @patch("scripts.inject_faults.requests.get")
    @patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"})
    def test_docker_not_running(
        self,
        mock_get: MagicMock,
        mock_subprocess: MagicMock,
    ) -> None:
        mock_subprocess.side_effect = subprocess.CalledProcessError(1, "docker")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_get.return_value = mock_resp

        errors = preflight_checks()
        docker_errors = [e for e in errors if "Docker" in e]
        assert len(docker_errors) >= 1

    @patch("scripts.inject_faults.subprocess.run")
    @patch("scripts.inject_faults.requests.get")
    @patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"})
    def test_prometheus_unreachable(
        self,
        mock_get: MagicMock,
        mock_subprocess: MagicMock,
    ) -> None:
        mock_subprocess.return_value = MagicMock(returncode=0, stdout="ok")

        import requests as req

        def get_side_effect(url: str, **kwargs: object) -> MagicMock:
            if "9090" in url:
                raise req.ConnectionError("Connection refused")
            resp = MagicMock()
            resp.status_code = 200
            return resp

        mock_get.side_effect = get_side_effect

        errors = preflight_checks()
        prom_errors = [e for e in errors if "Prometheus" in e]
        assert len(prom_errors) == 1

    @patch("scripts.inject_faults.subprocess.run")
    @patch("scripts.inject_faults.requests.get")
    @patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"})
    def test_loki_unreachable(
        self,
        mock_get: MagicMock,
        mock_subprocess: MagicMock,
    ) -> None:
        mock_subprocess.return_value = MagicMock(returncode=0, stdout="ok")

        import requests as req

        def get_side_effect(url: str, **kwargs: object) -> MagicMock:
            if "3100" in url:
                raise req.ConnectionError("Connection refused")
            resp = MagicMock()
            resp.status_code = 200
            return resp

        mock_get.side_effect = get_side_effect

        errors = preflight_checks()
        loki_errors = [e for e in errors if "Loki" in e]
        assert len(loki_errors) == 1


# ---------------------------------------------------------------------------
# TestPrintSummary
# ---------------------------------------------------------------------------
class TestPrintSummary:
    def test_handles_empty_results(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        print_summary(str(tmp_path))
        captured = capsys.readouterr()
        assert "No results found" in captured.out

    def test_prints_recall(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        record = {
            "test_id": "crash_1",
            "fault_type": "service_crash",
            "ground_truth": "cartservice",
            "predicted_root_cause": "cartservice",
            "top_3_predictions": ["cartservice"],
            "is_correct": True,
            "detection_latency_seconds": 10.0,
            "investigation_duration_seconds": 45.0,
            "status": "completed",
        }
        (tmp_path / "crash_1.json").write_text(json.dumps(record))
        print_summary(str(tmp_path))
        captured = capsys.readouterr()
        assert "Recall@1" in captured.out
        assert "100.0%" in captured.out

    def test_handles_nonexistent_dir(self, capsys: pytest.CaptureFixture[str]) -> None:
        print_summary("/nonexistent/path")
        captured = capsys.readouterr()
        assert "No results found" in captured.out
