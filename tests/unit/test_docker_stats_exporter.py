"""Unit tests for the Docker Stats Exporter.

The exporter runs inside Docker in production (see
``infrastructure/docker_stats_exporter/Dockerfile``), but the pure-Python
functions ``_extract_memory`` and ``_build_metrics`` are importable for
test. All Docker SDK interactions are mocked.

The tests focus on the new ``container_spec_memory_limit_bytes`` gauge
introduced to enable memory-saturation detection on the agent side —
without the limit metric, the agent cannot compute
``working_set / limit`` to fire the memory_utilization CRITICAL signal.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from types import ModuleType
from unittest.mock import MagicMock

import pytest

# The exporter module lives outside the ``src/`` tree (it ships as part of
# the Docker image), so load it directly from its file path without needing
# to add the directory to sys.path permanently.
_EXPORTER_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "infrastructure"
    / "docker_stats_exporter"
    / "exporter.py"
)


@pytest.fixture(scope="module")
def exporter() -> ModuleType:
    """Load ``infrastructure/docker_stats_exporter/exporter.py`` as a module.

    The real exporter runs inside a Docker image that ``pip install``s the
    ``docker`` Python SDK. Our host Poetry env doesn't include that package
    (we never run the exporter outside its container), so we register a
    lightweight ``docker`` stub in ``sys.modules`` before importing the
    module. Every test that exercises ``_build_metrics`` passes its own
    ``MagicMock`` client, so the stub doesn't need to do anything beyond
    satisfying the import.
    """
    if "docker" not in sys.modules:
        docker_stub = ModuleType("docker")
        docker_stub.DockerClient = MagicMock()  # type: ignore[attr-defined]
        sys.modules["docker"] = docker_stub

    spec = importlib.util.spec_from_file_location("docker_stats_exporter", _EXPORTER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("docker_stats_exporter", module)
    spec.loader.exec_module(module)
    return module


# ═══════════════════════════════════════════════════════════════════════════
# TestExtractMemory
# ═══════════════════════════════════════════════════════════════════════════


class TestExtractMemory:
    """``_extract_memory`` must return ``(usage, working_set, limit)``.

    The limit field feeds the new ``container_spec_memory_limit_bytes``
    gauge that the agent uses to compute memory_utilization.
    """

    def test_returns_limit_when_present(self, exporter: ModuleType) -> None:
        stats = {
            "memory_stats": {
                "usage": 30 * 1024 * 1024,  # 30 MiB
                "limit": 256 * 1024 * 1024,  # 256 MiB
                "stats": {"inactive_file": 5 * 1024 * 1024},
            }
        }
        usage, working_set, limit = exporter._extract_memory(stats)
        assert usage == pytest.approx(30 * 1024 * 1024)
        # working_set = usage - inactive_file
        assert working_set == pytest.approx(25 * 1024 * 1024)
        assert limit == pytest.approx(256 * 1024 * 1024)

    def test_returns_none_limit_when_absent(self, exporter: ModuleType) -> None:
        """When the Docker API omits the ``limit`` field, ``_extract_memory``
        must return ``None`` for the 3rd element so ``_build_metrics`` can
        skip emitting the gauge for that container."""
        stats = {
            "memory_stats": {
                "usage": 10 * 1024 * 1024,
                "stats": {"inactive_file": 0},
                # No 'limit' key
            }
        }
        usage, working_set, limit = exporter._extract_memory(stats)
        assert usage is not None
        assert working_set is not None
        assert limit is None

    def test_returns_all_none_when_usage_absent(self, exporter: ModuleType) -> None:
        """If ``usage`` is missing, return all-None — nothing to emit."""
        stats = {"memory_stats": {}}
        result = exporter._extract_memory(stats)
        assert result == (None, None, None)


# ═══════════════════════════════════════════════════════════════════════════
# TestBuildMetrics
# ═══════════════════════════════════════════════════════════════════════════


def _fake_container(
    name: str,
    service: str,
    *,
    usage: int = 30 * 1024 * 1024,
    limit: int | None = 256 * 1024 * 1024,
    inactive_file: int = 0,
    cpu_total_ns: int = int(1.23e9),
) -> MagicMock:
    """Build a MagicMock container that behaves like ``docker.models.containers.Container``."""
    mem_stats: dict = {
        "usage": usage,
        "stats": {"inactive_file": inactive_file},
    }
    if limit is not None:
        mem_stats["limit"] = limit

    stats_payload = {
        "cpu_stats": {"cpu_usage": {"total_usage": cpu_total_ns}},
        "memory_stats": mem_stats,
        "networks": {"eth0": {"rx_bytes": 100, "tx_bytes": 200, "rx_errors": 0, "tx_errors": 0}},
        "blkio_stats": {"io_service_bytes_recursive": []},
    }
    c = MagicMock()
    c.name = name
    c.labels = {"com.docker.compose.service": service}
    c.stats.return_value = stats_payload
    return c


class TestBuildMetrics:
    """``_build_metrics`` must emit the new gauge alongside the existing
    container-level metrics, with matching ``{service, name}`` labels so
    Prometheus can join the working-set and limit series without ``on()``.
    """

    def test_emits_spec_memory_limit_bytes_gauge(self, exporter: ModuleType) -> None:
        client = MagicMock()
        client.containers.list.return_value = [
            _fake_container("demo_app-checkoutservice-1", "checkoutservice"),
            _fake_container(
                "demo_app-frontend-1", "frontend", usage=150 * 1024 * 1024, limit=512 * 1024 * 1024
            ),
        ]
        output = exporter._build_metrics(client)

        # HELP/TYPE headers present
        assert "# TYPE container_spec_memory_limit_bytes gauge" in output
        assert "# HELP container_spec_memory_limit_bytes" in output

        # One gauge line per container, with matching label structure
        expected_checkout = (
            'container_spec_memory_limit_bytes{service="checkoutservice",'
            'name="demo_app-checkoutservice-1"} 268435456'
        )
        expected_frontend = (
            'container_spec_memory_limit_bytes{service="frontend",'
            'name="demo_app-frontend-1"} 536870912'
        )
        assert expected_checkout in output
        assert expected_frontend in output

    def test_label_set_matches_working_set_for_ratio_join(self, exporter: ModuleType) -> None:
        """The Prometheus ratio ``working_set / spec_memory_limit`` must
        join natively, which requires both series to carry the identical
        ``{service, name}`` label set."""
        client = MagicMock()
        client.containers.list.return_value = [
            _fake_container("demo_app-redis-1", "redis", usage=20 * 1024 * 1024)
        ]
        output = exporter._build_metrics(client)

        label_fragment = 'service="redis",name="demo_app-redis-1"'
        assert f"container_memory_working_set_bytes{{{label_fragment}}}" in output
        assert f"container_spec_memory_limit_bytes{{{label_fragment}}}" in output

    def test_omits_limit_line_when_docker_returns_none(self, exporter: ModuleType) -> None:
        """A container without a memory limit reported by Docker must be
        absent from the new gauge — but its other metrics must still appear.
        Guard against the `_extract_memory` returning None for limit."""
        client = MagicMock()
        client.containers.list.return_value = [
            _fake_container(
                "demo_app-nolimit-1",
                "nolimit",
                usage=50 * 1024 * 1024,
                limit=None,
            ),
            _fake_container("demo_app-cartservice-1", "cartservice"),
        ]
        output = exporter._build_metrics(client)

        # The container that has no limit does NOT appear in the new gauge...
        assert 'container_spec_memory_limit_bytes{service="nolimit"' not in output
        # ...but its other metrics (CPU, working_set) still appear.
        assert 'container_memory_working_set_bytes{service="nolimit"' in output
        # The cartservice line does appear (it has a limit).
        assert (
            'container_spec_memory_limit_bytes{service="cartservice",'
            'name="demo_app-cartservice-1"} 268435456'
        ) in output

    def test_skips_containers_without_compose_service_label(self, exporter: ModuleType) -> None:
        """Containers that aren't part of docker-compose (no
        ``com.docker.compose.service`` label) are ignored entirely — the
        exporter keys on that label."""
        client = MagicMock()
        unlabeled = _fake_container("stray-container", "ignored")
        unlabeled.labels = {}  # wipe the compose label
        labeled = _fake_container("demo_app-redis-1", "redis")
        client.containers.list.return_value = [unlabeled, labeled]

        output = exporter._build_metrics(client)

        assert 'name="stray-container"' not in output
        assert 'service="redis"' in output
