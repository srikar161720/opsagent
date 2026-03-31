"""RCAEval dataset adapter — converts RE1/RE2/RE3 failure cases to OpsAgent format.

Handles three distinct column naming conventions:
  1. RE1-OB: ``data.csv`` with simple ``{service}_{metric}`` columns (51 cols)
  2. RE1-SS/TT: ``data.csv`` with container-metric naming (439-1246 cols)
  3. RE2/RE3: ``metrics.csv`` with container-metric naming (389-1574 cols)

Ground truth is parsed from directory names (no metadata.json exists).
Anomaly timestamps come from ``inject_time.txt`` (Unix epoch seconds).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

# Prefixes that indicate infrastructure noise columns (not actual services)
_NOISE_PREFIXES = ("gke-", "ip-192-168-", "istio-init")

# Simple metric suffixes used in RE1-OB format
_SIMPLE_METRICS = frozenset({"cpu", "mem", "load", "latency", "error"})

# RE1-OB simple metric → OpsAgent canonical name
_SIMPLE_METRIC_RENAME: dict[str, str] = {
    "cpu": "cpu_usage",
    "mem": "memory_usage",
    "load": "load_average",
    "latency": "latency_p99",
    "error": "error_rate",
}


def _is_simple_format(columns: list[str]) -> bool:
    """Detect whether columns use the simple RE1-OB naming convention.

    Simple format: metric suffix after the last underscore is a simple word
    from ``_SIMPLE_METRICS`` (e.g. ``adservice_cpu``, ``frontend-external_load``).

    Container-metric format: metric suffix contains hyphens
    (e.g. ``carts_container-cpu-system-seconds-total``).
    """
    non_time = [c for c in columns if c != "time"]
    if not non_time:
        return False
    # Check the metric suffix (after last underscore) of the first few columns.
    # In simple format, suffixes are single words from _SIMPLE_METRICS.
    # In container format, suffixes contain hyphens (e.g. "container-cpu-system-seconds-total").
    sample = non_time[:10]
    simple_count = 0
    for col in sample:
        last_underscore = col.rfind("_")
        if last_underscore > 0:
            suffix = col[last_underscore + 1 :]
            if suffix in _SIMPLE_METRICS:
                simple_count += 1
    # If most sampled columns have simple metric suffixes, it's simple format
    return simple_count > len(sample) // 2


class RCAEvalDataAdapter:
    """Convert RCAEval failure cases into OpsAgent investigation input format.

    Args:
        dataset_path: Path to a single variant directory, e.g. ``"data/RCAEval/re2"``.
    """

    def __init__(self, dataset_path: str) -> None:
        self.dataset_path = Path(dataset_path)
        self._case_ids: list[str] | None = None

    # ── Public API ───────────────────────────────────────────────────────

    def list_cases(self) -> list[str]:
        """Return sorted list of all valid case IDs.

        Walks three directory levels: system → fault → run.
        Case ID format: ``"{system}/{service_fault}/{run}"``
        (e.g. ``"RE2-OB/checkoutservice_cpu/1"``).
        """
        if self._case_ids is None:
            cases: list[str] = []
            for system_dir in sorted(self.dataset_path.iterdir()):
                if not system_dir.is_dir():
                    continue
                for fault_dir in sorted(system_dir.iterdir()):
                    if not fault_dir.is_dir():
                        continue
                    for run_dir in sorted(fault_dir.iterdir(), key=lambda p: p.name):
                        if not run_dir.is_dir():
                            continue
                        has_data = (run_dir / "metrics.csv").exists() or (
                            run_dir / "data.csv"
                        ).exists()
                        has_inject = (run_dir / "inject_time.txt").exists()
                        if has_data and has_inject:
                            case_id = f"{system_dir.name}/{fault_dir.name}/{run_dir.name}"
                            cases.append(case_id)
            self._case_ids = cases
        return self._case_ids

    def load_case(self, case_id: str) -> dict:
        """Load and convert a single failure case to OpsAgent input format.

        Args:
            case_id: Hierarchical case ID (e.g. ``"RE2-OB/checkoutservice_cpu/1"``).

        Returns:
            Dict with keys: ``case_id``, ``metrics``, ``logs``,
            ``anomaly_timestamp``, ``ground_truth``.
        """
        case_dir = self.dataset_path / case_id
        metrics_flat = self._load_metrics(case_dir)
        logs = self._load_logs(case_dir)
        ground_truth = self._load_ground_truth(case_dir)
        anomaly_timestamp = self._load_inject_timestamp(case_dir)

        return {
            "case_id": case_id,
            "metrics": self._split_metrics_by_service(metrics_flat),
            "logs": logs,
            "anomaly_timestamp": anomaly_timestamp,
            "ground_truth": ground_truth,
        }

    def iter_cases(self) -> Iterator[dict]:
        """Iterate over all cases, yielding one converted dict at a time."""
        for case_id in self.list_cases():
            try:
                yield self.load_case(case_id)
            except Exception as exc:
                logger.warning("Skipping case %s: %s", case_id, exc)

    # ── Internal helpers ─────────────────────────────────────────────────

    def _load_metrics(self, case_dir: Path) -> pd.DataFrame:
        """Load metrics file, filter noise columns, normalize names."""
        metrics_file = case_dir / "metrics.csv"
        if not metrics_file.exists():
            metrics_file = case_dir / "data.csv"
        df = pd.read_csv(metrics_file)

        # Filter infrastructure noise columns
        df = df[[c for c in df.columns if not any(c.startswith(p) for p in _NOISE_PREFIXES)]]
        return df

    def _load_logs(self, case_dir: Path) -> pd.DataFrame | None:
        """Load logs.csv if present (RE2/RE3 only). Returns None for RE1."""
        log_path = case_dir / "logs.csv"
        if not log_path.exists():
            return None
        return pd.read_csv(log_path)

    def _load_ground_truth(self, case_dir: Path) -> dict:
        """Parse ground truth from directory path structure.

        The fault directory name has the format ``{service}_{fault_type}``.
        Split on the last underscore to handle services with hyphens/underscores.
        """
        fault_dir_name = case_dir.parent.name  # e.g. "checkoutservice_cpu"
        last_underscore = fault_dir_name.rfind("_")
        if last_underscore > 0:
            service = fault_dir_name[:last_underscore]
            fault_type = fault_dir_name[last_underscore + 1 :]
        else:
            service = fault_dir_name
            fault_type = "unknown"

        return {
            "root_cause_service": service,
            "fault_type": fault_type,
        }

    def _load_inject_timestamp(self, case_dir: Path) -> str:
        """Load fault injection timestamp from ``inject_time.txt`` as ISO-8601."""
        inject_file = case_dir / "inject_time.txt"
        timestamp_unix = int(inject_file.read_text().strip())
        dt = datetime.fromtimestamp(timestamp_unix, tz=UTC)
        return dt.isoformat()

    def _split_metrics_by_service(self, metrics_df: pd.DataFrame) -> dict[str, pd.DataFrame]:
        """Split flat metrics DataFrame into per-service DataFrames.

        Detects column format (simple vs container-metric) and dispatches
        to the appropriate splitting logic.
        """
        columns = list(metrics_df.columns)
        if _is_simple_format(columns):
            return self._split_simple_format(metrics_df)
        return self._split_container_format(metrics_df)

    def _split_simple_format(self, metrics_df: pd.DataFrame) -> dict[str, pd.DataFrame]:
        """Split RE1-OB simple format: columns like ``adservice_cpu``."""
        time_col = "time" if "time" in metrics_df.columns else None
        non_time = [c for c in metrics_df.columns if c != time_col]

        # Group columns by service prefix
        service_cols: dict[str, list[str]] = {}
        for col in non_time:
            last_underscore = col.rfind("_")
            if last_underscore <= 0:
                continue
            service = col[:last_underscore]
            metric = col[last_underscore + 1 :]
            if metric not in _SIMPLE_METRICS:
                continue
            service_cols.setdefault(service, []).append(col)

        result: dict[str, pd.DataFrame] = {}
        for svc, cols in service_cols.items():
            df = metrics_df[cols].copy()
            # Rename columns: strip service prefix and map to canonical names
            new_names = {}
            for c in cols:
                raw_metric = c[len(svc) + 1 :]
                new_names[c] = _SIMPLE_METRIC_RENAME.get(raw_metric, raw_metric)
            df = df.rename(columns=new_names)
            if time_col:
                df.insert(0, "timestamp", metrics_df[time_col].values)
            result[svc] = df

        return result

    def _split_container_format(self, metrics_df: pd.DataFrame) -> dict[str, pd.DataFrame]:
        """Split container-metric format.

        Handles columns like ``carts_container-cpu-system-seconds-total``.
        """
        time_col = "time" if "time" in metrics_df.columns else None
        non_time = [c for c in metrics_df.columns if c != time_col]

        # Extract service name: everything before the first ``_container-`` or
        # the first segment before a hyphenated metric suffix.
        service_cols: dict[str, list[str]] = {}
        pattern = re.compile(r"^(.+?)_(container-.+|node-.+|kube_.+|.+-.+)$")

        for col in non_time:
            m = pattern.match(col)
            if m:
                service = m.group(1)
                # Skip infrastructure noise that wasn't caught earlier
                if any(service.startswith(p.rstrip("-")) for p in _NOISE_PREFIXES):
                    continue
                service_cols.setdefault(service, []).append(col)

        result: dict[str, pd.DataFrame] = {}
        for svc, cols in service_cols.items():
            df = metrics_df[cols].copy()
            df.columns = [c[len(svc) + 1 :] for c in cols]
            if time_col:
                df.insert(0, "timestamp", metrics_df[time_col].values)
            result[svc] = df

        return result
