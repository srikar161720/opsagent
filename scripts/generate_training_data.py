"""Collect baseline training data from the running OTel Demo stack.

Queries Prometheus for metric snapshots and Loki for log batches at regular
intervals, saving results to disk for later use in anomaly detection training.

Usage:
    poetry run python scripts/generate_training_data.py
    poetry run python scripts/generate_training_data.py --duration 1h --output-dir data/baseline/
    poetry run python scripts/generate_training_data.py --duration 24h  # Full baseline collection
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

# ── Services to collect data from ────────────────────────────────────────────

SERVICES = [
    "frontend",
    "cartservice",
    "checkoutservice",
    "paymentservice",
    "productcatalogservice",
    "currencyservice",
]

# Service name filter — matches OTel Demo containers by service label
# (exposed by the Docker Stats Exporter from com.docker.compose.service)
_SVC_FILTER = 'service=~"frontend|cartservice|checkoutservice|paymentservice|productcatalogservice|currencyservice|redis"'

# Prometheus metric queries via cAdvisor — container-level metrics per service
METRIC_QUERIES = {
    "cpu_usage_rate": f'rate(container_cpu_usage_seconds_total{{{_SVC_FILTER}}}[1m])',
    "memory_usage_bytes": f'container_memory_usage_bytes{{{_SVC_FILTER}}}',
    "memory_working_set_bytes": f'container_memory_working_set_bytes{{{_SVC_FILTER}}}',
    "network_rx_bytes_rate": f'rate(container_network_receive_bytes_total{{{_SVC_FILTER}}}[1m])',
    "network_tx_bytes_rate": f'rate(container_network_transmit_bytes_total{{{_SVC_FILTER}}}[1m])',
    "network_rx_errors_rate": f'rate(container_network_receive_errors_total{{{_SVC_FILTER}}}[1m])',
    "network_tx_errors_rate": f'rate(container_network_transmit_errors_total{{{_SVC_FILTER}}}[1m])',
    "fs_usage_bytes": f'container_fs_usage_bytes{{{_SVC_FILTER}}}',
}


def parse_duration(duration_str: str) -> int:
    """Parse a duration string like '24h', '30m', '1h30m' into seconds."""
    pattern = r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?"
    match = re.fullmatch(pattern, duration_str)
    if not match or not any(match.groups()):
        raise ValueError(
            f"Invalid duration format: '{duration_str}'. Use format like '24h', '30m', '1h30m', '90s'."
        )
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    return hours * 3600 + minutes * 60 + seconds


class TrainingDataCollector:
    """Collects metrics from Prometheus and logs from Loki at regular intervals."""

    def __init__(
        self,
        output_dir: str,
        prometheus_url: str = "http://localhost:9090",
        loki_url: str = "http://localhost:3100",
        interval_seconds: int = 60,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.prometheus_url = prometheus_url.rstrip("/")
        self.loki_url = loki_url.rstrip("/")
        self.interval_seconds = interval_seconds
        self._stop = False

        # Create output directories
        self.metrics_dir = self.output_dir / "metrics"
        self.logs_dir = self.output_dir / "logs"
        self.metrics_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)

        # Counters
        self.metric_snapshots = 0
        self.log_count = 0
        self._last_log_timestamp_ns = 0  # For Loki pagination

    def _query_prometheus(self, query: str) -> list[dict[str, Any]]:
        """Execute an instant query against the Prometheus HTTP API."""
        try:
            resp = requests.get(
                f"{self.prometheus_url}/api/v1/query",
                params={"query": query},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("status") == "success":
                return data.get("data", {}).get("result", [])
        except requests.RequestException as e:
            print(f"  [WARN] Prometheus query failed: {e}")
        return []

    def _query_loki(self, query: str, start_ns: int, end_ns: int) -> list[dict[str, Any]]:
        """Execute a log query against the Loki HTTP API."""
        try:
            resp = requests.get(
                f"{self.loki_url}/loki/api/v1/query_range",
                params={
                    "query": query,
                    "start": str(start_ns),
                    "end": str(end_ns),
                    "limit": 5000,
                },
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("status") == "success":
                return data.get("data", {}).get("result", [])
        except requests.RequestException as e:
            print(f"  [WARN] Loki query failed: {e}")
        return []

    def collect_metrics_snapshot(self) -> dict[str, Any]:
        """Collect a single metrics snapshot from Prometheus."""
        timestamp = datetime.now(timezone.utc).isoformat()
        snapshot: dict[str, Any] = {"timestamp": timestamp, "metrics": {}}

        for metric_name, query in METRIC_QUERIES.items():
            results = self._query_prometheus(query)
            snapshot["metrics"][metric_name] = []
            for result in results:
                metric_labels = result.get("metric", {})
                # Extract service name from cAdvisor compose service label
                service = (
                    metric_labels.get("service")
                    or metric_labels.get("name")
                    or "unknown"
                )
                value = result.get("value", [None, None])
                snapshot["metrics"][metric_name].append({
                    "service": service,
                    "value": float(value[1]) if value[1] and value[1] != "NaN" else None,
                    "timestamp": value[0] if value[0] else None,
                })

        return snapshot

    def collect_logs(self) -> list[dict[str, Any]]:
        """Collect recent logs from Loki for all services."""
        now_ns = int(time.time() * 1e9)
        # Look back one interval
        start_ns = max(
            self._last_log_timestamp_ns,
            now_ns - (self.interval_seconds * int(1e9)),
        )

        all_logs: list[dict[str, Any]] = []
        # Query all container logs
        query = '{job=~".+"}'
        results = self._query_loki(query, start_ns, now_ns)

        for stream in results:
            labels = stream.get("stream", {})
            for ts_ns, line in stream.get("values", []):
                all_logs.append({
                    "timestamp_ns": ts_ns,
                    "labels": labels,
                    "message": line,
                })

        if all_logs:
            # Update watermark to latest timestamp for next iteration
            max_ts = max(int(log["timestamp_ns"]) for log in all_logs)
            self._last_log_timestamp_ns = max_ts + 1

        return all_logs

    def save_metrics_snapshot(self, snapshot: dict[str, Any], index: int) -> None:
        """Save a metrics snapshot to a JSON file."""
        filename = self.metrics_dir / f"snapshot_{index:06d}.json"
        with open(filename, "w") as f:
            json.dump(snapshot, f, indent=2)

    def save_logs(self, logs: list[dict[str, Any]], index: int) -> None:
        """Save a batch of logs to a JSONL file."""
        if not logs:
            return
        filename = self.logs_dir / f"logs_{index:06d}.jsonl"
        with open(filename, "w") as f:
            for log in logs:
                f.write(json.dumps(log) + "\n")

    def load_or_create_metadata(
        self, duration_seconds: int
    ) -> dict[str, Any]:
        """Load existing metadata for resume, or create new."""
        metadata_path = self.output_dir / "metadata.json"
        if metadata_path.exists():
            with open(metadata_path) as f:
                metadata = json.load(f)
            if metadata.get("status") == "collecting":
                print(f"  Resuming previous collection (snapshots: {metadata.get('metric_snapshots', 0)})")
                self.metric_snapshots = metadata.get("metric_snapshots", 0)
                self.log_count = metadata.get("log_count", 0)
                return metadata

        return {
            "start_time": datetime.now(timezone.utc).isoformat(),
            "end_time": None,
            "status": "collecting",
            "duration_seconds": duration_seconds,
            "duration_hours": round(duration_seconds / 3600, 2),
            "services": SERVICES,
            "log_count": 0,
            "metric_snapshots": 0,
            "interval_seconds": self.interval_seconds,
            "prometheus_url": self.prometheus_url,
            "loki_url": self.loki_url,
        }

    def save_metadata(self, metadata: dict[str, Any]) -> None:
        """Write metadata.json to disk."""
        metadata["metric_snapshots"] = self.metric_snapshots
        metadata["log_count"] = self.log_count
        metadata_path = self.output_dir / "metadata.json"
        with open(metadata_path, "w") as f:
            json.dump(metadata, f, indent=2)

    def signal_handler(self, signum: int, frame: Any) -> None:
        """Handle SIGINT/SIGTERM gracefully."""
        print("\n  Received interrupt signal. Finishing current snapshot...")
        self._stop = True

    def run(self, duration_seconds: int) -> None:
        """Run the collection loop for the specified duration."""
        # Register signal handlers
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)

        metadata = self.load_or_create_metadata(duration_seconds)
        self.save_metadata(metadata)

        start_time = time.time()
        elapsed_at_start = self.metric_snapshots * self.interval_seconds

        print(f"  Collecting data for {duration_seconds}s ({duration_seconds / 3600:.1f}h)")
        print(f"  Prometheus: {self.prometheus_url}")
        print(f"  Loki:       {self.loki_url}")
        print(f"  Interval:   {self.interval_seconds}s")
        print(f"  Output:     {self.output_dir}")
        print()

        while not self._stop:
            elapsed = time.time() - start_time + elapsed_at_start
            if elapsed >= duration_seconds:
                break

            iteration_start = time.time()
            index = self.metric_snapshots

            # Collect metrics
            snapshot = self.collect_metrics_snapshot()
            self.save_metrics_snapshot(snapshot, index)
            self.metric_snapshots += 1

            # Collect logs
            logs = self.collect_logs()
            self.save_logs(logs, index)
            self.log_count += len(logs)

            # Update metadata periodically (every 10 snapshots)
            if self.metric_snapshots % 10 == 0:
                self.save_metadata(metadata)

            remaining = duration_seconds - elapsed
            hours_left = remaining / 3600
            print(
                f"  [{self.metric_snapshots:>5}] "
                f"metrics: {len(snapshot.get('metrics', {}))} queries | "
                f"logs: {len(logs)} | "
                f"total logs: {self.log_count} | "
                f"remaining: {hours_left:.1f}h"
            )

            # Sleep until next interval
            elapsed_in_iteration = time.time() - iteration_start
            sleep_time = max(0, self.interval_seconds - elapsed_in_iteration)
            if sleep_time > 0 and not self._stop:
                time.sleep(sleep_time)

        # Finalize
        if self._stop:
            metadata["status"] = "interrupted"
            print("\n  Collection interrupted by user.")
        else:
            metadata["status"] = "completed"
            print("\n  Collection completed successfully.")

        metadata["end_time"] = datetime.now(timezone.utc).isoformat()
        self.save_metadata(metadata)

        print(f"  Total metric snapshots: {self.metric_snapshots}")
        print(f"  Total log entries: {self.log_count}")
        print(f"  Metadata saved to: {self.output_dir / 'metadata.json'}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect baseline training data from the OTel Demo stack."
    )
    parser.add_argument(
        "--duration",
        type=str,
        default="24h",
        help="Collection duration (e.g., '24h', '1h', '30m', '2h30m'). Default: 24h",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="data/baseline/",
        help="Output directory for collected data. Default: data/baseline/",
    )
    parser.add_argument(
        "--prometheus-url",
        type=str,
        default=os.environ.get("PROMETHEUS_URL", "http://localhost:9090"),
        help="Prometheus base URL. Default: http://localhost:9090",
    )
    parser.add_argument(
        "--loki-url",
        type=str,
        default=os.environ.get("LOKI_URL", "http://localhost:3100"),
        help="Loki base URL. Default: http://localhost:3100",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=60,
        help="Collection interval in seconds. Default: 60",
    )

    args = parser.parse_args()

    duration_seconds = parse_duration(args.duration)

    collector = TrainingDataCollector(
        output_dir=args.output_dir,
        prometheus_url=args.prometheus_url,
        loki_url=args.loki_url,
        interval_seconds=args.interval,
    )
    collector.run(duration_seconds)


if __name__ == "__main__":
    main()
