"""Time-window aggregation for the real-time Fast Loop pipeline.

Aggregates streaming log events and metrics into fixed-size,
non-overlapping time windows. Each completed window is passed
to FeatureEngineer to compute the feature vector fed to the LSTM-AE.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta


class WindowAggregator:
    """Aggregate streaming log events and metrics into fixed-size time windows."""

    def __init__(self, window_size_seconds: int = 60) -> None:
        self.window_size = timedelta(seconds=window_size_seconds)
        self.current_window_start: datetime | None = None
        self.current_window_logs: list[dict] = []
        self.current_window_metrics: dict[str, list[float]] = defaultdict(list)

    def add_log(
        self,
        timestamp: datetime,
        template_id: int,
        service: str,
    ) -> dict | None:
        """Add a parsed log event to the current window.

        Returns the completed window dict if *timestamp* crosses a window
        boundary, otherwise returns ``None``.

        Note:
            Timestamps must be naive UTC datetimes (no tzinfo). Mixing
            timezone-aware and naive datetimes will raise ``TypeError``.
        """
        if self.current_window_start is None:
            self.current_window_start = self._floor_to_window(timestamp)

        window_start = self._floor_to_window(timestamp)

        if window_start != self.current_window_start:
            completed = self._finalize_window()
            self.current_window_start = window_start
            self.current_window_logs = []
            self.current_window_metrics = defaultdict(list)
            # Add the current log to the new window
            self.current_window_logs.append(
                {"timestamp": timestamp, "template_id": template_id, "service": service}
            )
            return completed

        self.current_window_logs.append(
            {"timestamp": timestamp, "template_id": template_id, "service": service}
        )
        return None

    def add_metric(self, metric_name: str, value: float) -> None:
        """Add a metric observation to the current window."""
        self.current_window_metrics[metric_name].append(value)

    def flush(self) -> dict | None:
        """Emit the current (possibly partial) window and reset state.

        Returns ``None`` if no data has been accumulated.
        """
        if self.current_window_start is None:
            return None
        completed = self._finalize_window()
        self.current_window_start = None
        self.current_window_logs = []
        self.current_window_metrics = defaultdict(list)
        return completed

    def _floor_to_window(self, timestamp: datetime) -> datetime:
        """Floor a timestamp to the start of its window boundary."""
        epoch = datetime(1970, 1, 1)
        total_seconds = int((timestamp - epoch).total_seconds())
        window_seconds = int(self.window_size.total_seconds())
        floored = (total_seconds // window_seconds) * window_seconds
        return epoch + timedelta(seconds=floored)

    def _finalize_window(self) -> dict:
        """Package the current window data into a dict for FeatureEngineer."""
        assert self.current_window_start is not None
        return {
            "window_start": self.current_window_start,
            "window_end": self.current_window_start + self.window_size,
            "logs": list(self.current_window_logs),
            "metrics": dict(self.current_window_metrics),
        }
