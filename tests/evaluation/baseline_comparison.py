"""Internal baseline comparisons for OTel Demo fault injection evaluation.

Three baselines that isolate the value of each OpsAgent layer:
1. Rule-Based:      Static thresholds only (no ML, no LLM)
2. AD-Only:         LSTM-AE anomaly scores only (no agent, no causal discovery)
3. LLM-Without-Tools: LLM reasoning from static snapshot (no tool calls)

Usage:
    poetry run python tests/evaluation/baseline_comparison.py \
        --results-dir data/evaluation/results/
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import torch
from dotenv import load_dotenv

from src.data_collection.metrics_collector import MetricsCollector

# Load .env so GEMINI_API_KEY is available to LLMWithoutToolsBaseline when
# the baselines are run via `fault_injection_suite.py --baseline llm-no-tools`
# (the OpsAgent path already calls load_dotenv() inside src/agent/graph.py,
# but the baseline path doesn't import that module).
load_dotenv()

logger = logging.getLogger(__name__)

SERVICES = [
    "cartservice",
    "checkoutservice",
    "currencyservice",
    "frontend",
    "paymentservice",
    "productcatalogservice",
    "redis",
]

METRIC_QUERIES = {
    "cpu_usage": 'rate(container_cpu_usage_seconds_total{service="{service}"}[1m])',
    "memory_usage": 'container_memory_working_set_bytes{service="{service}"}',
}

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class RuleBasedBaseline:
    """Static threshold alerting. Root cause = first service exceeding threshold."""

    def __init__(
        self,
        collector: MetricsCollector | None = None,
        cpu_threshold: float = 0.85,
        memory_threshold_bytes: float = 200_000_000,  # ~200MB
    ) -> None:
        self.collector = collector or MetricsCollector()
        self.cpu_threshold = cpu_threshold
        self.memory_threshold_bytes = memory_threshold_bytes

    def predict(
        self,
        alert: dict[str, Any],
        services: list[str] | None = None,
    ) -> dict[str, Any]:
        """Predict root cause using static thresholds."""
        target_services = services or SERVICES
        scores: list[tuple[str, float]] = []

        for svc in target_services:
            try:
                metrics = self.collector.get_service_metrics(svc, METRIC_QUERIES)
                cpu_vals = metrics.get("cpu_usage", [])
                mem_vals = metrics.get("memory_usage", [])

                cpu_max = max(cpu_vals) if cpu_vals else 0.0
                mem_max = max(mem_vals) if mem_vals else 0.0

                # Check thresholds
                if cpu_max > self.cpu_threshold:
                    scores.append((svc, cpu_max * 2))  # boost breaching services
                elif mem_max > self.memory_threshold_bytes:
                    scores.append((svc, mem_max / self.memory_threshold_bytes))
                else:
                    scores.append((svc, cpu_max))
            except Exception:
                logger.warning("Failed to query metrics for %s", svc)
                scores.append((svc, 0.0))

        scores.sort(key=lambda x: x[1], reverse=True)
        top3 = [s[0] for s in scores[:3]]

        return {
            "root_cause": top3[0] if top3 else "unknown",
            "top_3_predictions": top3,
            "confidence": min(scores[0][1], 1.0) if scores else 0.0,
        }


class ADOnlyBaseline:
    """LSTM-AE anomaly scores only. Root cause = highest reconstruction error."""

    def __init__(
        self,
        model_path: str | None = None,
        threshold: float = 0.253,
        collector: MetricsCollector | None = None,
        scaler_dir: str | None = None,
    ) -> None:
        self.collector = collector or MetricsCollector()
        self.threshold = threshold
        self.model = None
        self.scaler_mean: np.ndarray | None = None
        self.scaler_std: np.ndarray | None = None

        if model_path and Path(model_path).exists():
            from src.anomaly_detection.lstm_autoencoder import LSTMAutoencoder

            checkpoint = torch.load(model_path, map_location="cpu")
            if isinstance(checkpoint, dict) and "model_state_dict" in checkpoint:
                state_dict = checkpoint["model_state_dict"]
            else:
                state_dict = checkpoint
            # Infer input_dim from the first linear layer
            for key, val in state_dict.items():
                if "embedding" in key and val.dim() == 2:
                    input_dim = val.shape[1]
                    break
            else:
                input_dim = 54  # default OTel feature dim
            self.model = LSTMAutoencoder(input_dim=input_dim)
            self.model.load_state_dict(state_dict)
            self.model.eval()

        # Load scaler params if available
        s_dir = Path(scaler_dir or "data/splits/otel")
        if (s_dir / "scaler_mean.npy").exists():
            self.scaler_mean = np.load(s_dir / "scaler_mean.npy")
            self.scaler_std = np.load(s_dir / "scaler_std.npy")

    def predict(
        self,
        alert: dict[str, Any],
        services: list[str] | None = None,
    ) -> dict[str, Any]:
        """Predict root cause using reconstruction error ranking."""
        target_services = services or SERVICES
        errors: list[tuple[str, float]] = []

        for svc in target_services:
            try:
                metrics = self.collector.get_service_metrics(svc, METRIC_QUERIES)
                cpu_vals = metrics.get("cpu_usage", [])
                mem_vals = metrics.get("memory_usage", [])

                # Build simple feature vector from available metrics
                features = []
                for vals in [cpu_vals, mem_vals]:
                    if vals:
                        features.extend(
                            [
                                np.mean(vals),
                                np.std(vals),
                                np.min(vals),
                                np.max(vals),
                            ]
                        )
                    else:
                        features.extend([0.0, 0.0, 0.0, 0.0])

                if self.model is not None:
                    # Pad to model's expected input_dim
                    feature_arr = np.zeros(self.model.embedding.in_features)
                    feature_arr[: len(features)] = features[: len(feature_arr)]

                    if self.scaler_mean is not None and self.scaler_std is not None:
                        std_safe = self.scaler_std.copy()
                        std_safe[std_safe == 0] = 1.0
                        feature_arr = (feature_arr - self.scaler_mean) / std_safe

                    tensor = torch.FloatTensor(feature_arr).unsqueeze(0).unsqueeze(0)
                    with torch.no_grad():
                        error = float(self.model.get_reconstruction_error(tensor).item())
                else:
                    # Fallback: use variance as anomaly proxy
                    all_vals = cpu_vals + mem_vals
                    error = float(np.std(all_vals)) if all_vals else 0.0

                errors.append((svc, error))
            except Exception:
                logger.warning("Failed to score %s", svc)
                errors.append((svc, 0.0))

        errors.sort(key=lambda x: x[1], reverse=True)
        top3 = [s[0] for s in errors[:3]]
        max_error = errors[0][1] if errors else 0.0

        return {
            "root_cause": top3[0] if top3 else "unknown",
            "top_3_predictions": top3,
            "confidence": min(max_error / self.threshold, 1.0) if self.threshold > 0 else 0.0,
        }


class LLMWithoutToolsBaseline:
    """Raw alert + static metric snapshot sent to Gemini without tool calls."""

    def __init__(
        self,
        model_name: str = "gemini-3-flash-preview",
        collector: MetricsCollector | None = None,
    ) -> None:
        self.model_name = model_name
        self.collector = collector or MetricsCollector()

    def predict(
        self,
        alert: dict[str, Any],
        services: list[str] | None = None,
    ) -> dict[str, Any]:
        """Predict root cause using LLM reasoning only (no tool calls)."""
        from langchain_google_genai import ChatGoogleGenerativeAI

        target_services = services or SERVICES

        # Build static metric snapshot
        snapshot_lines: list[str] = []
        for svc in target_services:
            try:
                metrics = self.collector.get_service_metrics(svc, METRIC_QUERIES)
                cpu_vals = metrics.get("cpu_usage", [])
                mem_vals = metrics.get("memory_usage", [])
                cpu_str = f"cpu={np.mean(cpu_vals):.4f}" if cpu_vals else "cpu=N/A"
                mem_str = f"mem={np.mean(mem_vals):.0f}" if mem_vals else "mem=N/A"
                snapshot_lines.append(f"  {svc}: {cpu_str}, {mem_str}")
            except Exception:
                snapshot_lines.append(f"  {svc}: metrics unavailable")

        snapshot = "\n".join(snapshot_lines)

        prompt = (
            f"You are an SRE investigating a microservice incident.\n\n"
            f"ALERT:\n{json.dumps(alert, indent=2)}\n\n"
            f"CURRENT METRICS SNAPSHOT:\n{snapshot}\n\n"
            f"Available services: {', '.join(target_services)}\n\n"
            f"Based on this information, identify the root cause service. "
            f"Respond with ONLY the service name on the first line, "
            f"then your top 3 suspects (one per line), "
            f"then a confidence score (0.0-1.0)."
        )

        try:
            # Pass google_api_key explicitly so the baseline works regardless
            # of whether langchain's auto-detection picks up the env var.
            # load_dotenv() at module level ensures GEMINI_API_KEY is loaded
            # from .env (same pattern as src/agent/graph.py).
            #
            # max_retries=3 wraps the invoke() call in tenacity retry logic
            # so transient network errors (macOS DNS cache blips, Gemini 429
            # rate limits, 5xx) don't cost a whole 35-test run a test.
            # Matches src/agent/graph.py:_get_llm()'s max_retries=3 for
            # consistency with the OpsAgent path.
            llm = ChatGoogleGenerativeAI(
                model=self.model_name,
                temperature=0.1,
                google_api_key=os.environ.get("GEMINI_API_KEY", ""),
                max_retries=3,
            )
            response = llm.invoke(prompt)
            content = (
                response.content if isinstance(response.content, str) else str(response.content)
            )
            return self._parse_response(content, target_services)
        except Exception:
            logger.exception("LLM baseline failed")
            return {
                "root_cause": "unknown",
                "top_3_predictions": [],
                "confidence": 0.0,
            }

    def _parse_response(self, text: str, valid_services: list[str]) -> dict[str, Any]:
        """Parse LLM free-text response to extract service name predictions."""
        lines = [line.strip() for line in text.strip().split("\n") if line.strip()]

        # Find service names in the response
        found_services: list[str] = []
        for line in lines:
            for svc in valid_services:
                if svc in line.lower() and svc not in found_services:
                    found_services.append(svc)

        # Extract confidence if present
        confidence = 0.5  # default
        for line in reversed(lines):
            try:
                val = float(line.strip().rstrip("."))
                if 0.0 <= val <= 1.0:
                    confidence = val
                    break
            except ValueError:
                continue

        root_cause = found_services[0] if found_services else "unknown"
        top3 = found_services[:3]

        return {
            "root_cause": root_cause,
            "top_3_predictions": top3,
            "confidence": confidence,
        }


class BaselineInvestigatorAdapter:
    """Adapts a ``*Baseline.predict() -> dict-of-3`` to match the shape of
    ``AgentExecutor.investigate() -> dict-of-6`` that the fault-injection
    harness expects.

    ``fault_injection_suite.run_fault_injection()`` calls
    ``agent.investigate(alert=..., start_time=...)`` and reads six keys
    from the return (``root_cause``, ``root_cause_confidence``,
    ``top_3_predictions``, ``confidence``, ``rca_report``,
    ``recommended_actions``). Baselines expose a simpler
    ``predict(alert, services) -> {root_cause, top_3_predictions, confidence}``.
    This adapter upshapes that return and synthesises a minimal RCA-report
    stub — baselines do not generate structured reports, and are evaluated
    purely on the correctness of ``root_cause`` / ``top_3_predictions``.

    ``start_time`` is accepted for API parity with ``AgentExecutor.investigate``
    but deliberately ignored — the three baselines are point-in-time (they
    query the live ``MetricsCollector`` at call time), matching what
    OpsAgent's tool layer sees when it runs at the same pre-investigation
    wait offset.

    The ``services`` kwarg to the wrapped baseline is sourced from
    ``alert["affected_services"]`` so currencyservice (intentionally
    excluded from the evaluation suite's alert payload per the Session 12
    gotcha) also stays out of baseline predictions.
    """

    def __init__(self, baseline: Any, kind: str) -> None:
        self.baseline = baseline
        self.kind = kind

    def investigate(
        self,
        alert: dict[str, Any],
        start_time: str | None = None,
    ) -> dict[str, Any]:
        # Silently accept start_time — baselines don't pin windows.
        del start_time
        services = alert.get("affected_services", []) or None
        pred = self.baseline.predict(alert, services=services)

        root_cause = pred.get("root_cause") or "unknown"
        confidence = float(pred.get("confidence", 0.0) or 0.0)
        top_3 = list(pred.get("top_3_predictions") or [])

        top_3_str = ", ".join(top_3) if top_3 else "n/a"
        rca_report = (
            f"[baseline:{self.kind}] Predicted root cause: {root_cause} "
            f"(confidence {confidence:.2f}). Top-3: {top_3_str}."
        )

        return {
            "root_cause": root_cause,
            "root_cause_confidence": confidence,
            "top_3_predictions": top_3,
            "confidence": confidence,
            "rca_report": rca_report,
            "recommended_actions": [],
        }


def run_all_baselines(
    results_dir: str,
    output_dir: str | None = None,
    model_path: str = "models/lstm_autoencoder/finetuned_otel.pt",
) -> dict[str, Any]:
    """Evaluate all three baselines on the same fault injection scenarios.

    For each OpsAgent result JSON, re-inject the fault and run each baseline.
    Since re-injection is expensive, we instead use live Prometheus queries
    during the original fault window (baselines query current state).

    Args:
        results_dir: Path to OpsAgent result JSONs from fault injection suite.
        output_dir: Where to save baseline results. Defaults to results_dir parent.
        model_path: Path to fine-tuned LSTM-AE checkpoint.

    Returns:
        Dict mapping baseline name to summary metrics.
    """
    from tests.evaluation.metrics_calculator import calculate_metrics, load_results

    results = load_results(results_dir)
    if not results:
        logger.warning("No OpsAgent results found in %s", results_dir)
        return {}

    if output_dir:
        out_base = Path(output_dir) / "baseline_results"
    else:
        out_base = Path(results_dir).parent / "baseline_results"
    out_base.mkdir(parents=True, exist_ok=True)

    collector = MetricsCollector()

    baselines: dict[str, RuleBasedBaseline | ADOnlyBaseline | LLMWithoutToolsBaseline] = {
        "rule_based": RuleBasedBaseline(collector=collector),
        "ad_only": ADOnlyBaseline(model_path=model_path, collector=collector),
        "llm_no_tools": LLMWithoutToolsBaseline(collector=collector),
    }

    all_summaries: dict[str, Any] = {}

    for baseline_name, baseline in baselines.items():
        baseline_dir = out_base / baseline_name
        baseline_dir.mkdir(parents=True, exist_ok=True)

        baseline_results: list[dict[str, Any]] = []

        for r in results:
            if r.get("status") != "completed":
                continue

            alert = {
                "title": f"Baseline Evaluation — {r['fault_type']}",
                "severity": "high",
                "timestamp": r.get("alert_time", datetime.now(tz=UTC).isoformat()),
                "anomaly_score": 1.0,
            }

            pred = baseline.predict(alert=alert)

            baseline_record = {
                "test_id": r["test_id"],
                "fault_type": r["fault_type"],
                "run_id": r["run_id"],
                "ground_truth": r["ground_truth"],
                "predicted_root_cause": pred["root_cause"],
                "top_3_predictions": pred["top_3_predictions"],
                "confidence": pred["confidence"],
                "is_correct": pred["root_cause"] == r["ground_truth"],
                "detection_latency_seconds": r.get("detection_latency_seconds", 0),
                "investigation_duration_seconds": 0,  # baselines are near-instant
                "status": "completed",
            }

            baseline_results.append(baseline_record)

            out_file = baseline_dir / f"{r['test_id']}.json"
            with open(out_file, "w") as f:
                json.dump(baseline_record, f, indent=2)

        if baseline_results:
            metrics = calculate_metrics(baseline_results)
            all_summaries[baseline_name] = {
                "recall_at_1": metrics.recall_at_1,
                "recall_at_3": metrics.recall_at_3,
                "avg_detection_latency": metrics.avg_detection_latency,
                "total_cases": len(baseline_results),
                "recall_by_fault": metrics.recall_by_fault,
            }

    return all_summaries


def main() -> None:
    """Run baseline comparison from CLI."""
    parser = argparse.ArgumentParser(description="Run internal baseline comparisons")
    parser.add_argument(
        "--results-dir",
        default="data/evaluation/results/",
        help="Path to OpsAgent result JSONs",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Where to save baseline results",
    )
    parser.add_argument(
        "--model-path",
        default="models/lstm_autoencoder/finetuned_otel.pt",
        help="Path to fine-tuned LSTM-AE model",
    )
    args = parser.parse_args()

    summaries = run_all_baselines(
        results_dir=args.results_dir,
        output_dir=args.output_dir,
        model_path=args.model_path,
    )

    for name, summary in summaries.items():
        print(f"\n{name.upper()}:")
        print(f"  Recall@1:    {summary['recall_at_1']:.1%}")
        print(f"  Recall@3:    {summary['recall_at_3']:.1%}")
        print(f"  Total cases: {summary['total_cases']}")
        if summary.get("recall_by_fault"):
            for fault, r1 in sorted(summary["recall_by_fault"].items()):
                print(f"    {fault}: {r1:.1%}")


if __name__ == "__main__":
    main()
