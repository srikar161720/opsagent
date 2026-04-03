"""Counterfactual confidence scoring for causal claims.

Transforms raw causal graph edges into interventional probability estimates
grounded in actual anomaly magnitudes.  The confidence score answers:
*"If the suspected root cause had not occurred, what is the probability
the downstream effect would not have occurred?"*
"""

from __future__ import annotations

import pandas as pd


def compute_baseline_stats(baseline_df: pd.DataFrame) -> dict[str, dict[str, float]]:
    """Compute per-column mean and std from the baseline (normal) period.

    Args:
        baseline_df: Metric DataFrame containing only the baseline window.

    Returns:
        Dict mapping column name → ``{"mean": float, "std": float}``.
    """
    stats: dict[str, dict[str, float]] = {}
    for col in baseline_df.columns:
        stats[col] = {
            "mean": float(baseline_df[col].mean()),
            "std": float(baseline_df[col].std()),
        }
    return stats


def calculate_counterfactual_confidence(
    metrics_df: pd.DataFrame,
    cause_service: str,
    effect_service: str,
    anomaly_window: tuple[int, int],
    baseline_stats: dict[str, dict[str, float]],
) -> tuple[float, str]:
    """Estimate counterfactual confidence for a causal claim.

    Method — Interventional Probability Estimation:

    1. Measure how anomalous the cause was (z-score relative to baseline).
    2. Measure Pearson correlation between cause and effect over the full series.
    3. ``confidence = correlation² × min(1.0, |cause_z_score| / 3.0)``

    This is a simplified approximation.  More rigorous alternatives (do-calculus,
    structural causal models) exist but are computationally expensive for
    real-time RCA.

    Args:
        metrics_df: Full metric DataFrame (baseline + anomaly window combined).
        cause_service: Column name of the causal service metric.
        effect_service: Column name of the downstream effect metric.
        anomaly_window: ``(start_idx, end_idx)`` row index range of the
            anomaly period.
        baseline_stats: Pre-computed per-service mean and std from
            :func:`compute_baseline_stats`.

    Returns:
        A ``(confidence, explanation)`` tuple where *confidence* is a float
        in ``[0.0, 1.0]`` and *explanation* is a human-readable sentence.
    """
    start_idx, end_idx = anomaly_window

    # ── Cause anomaly magnitude ──────────────────────────────────────────
    cause_anomaly = metrics_df.loc[start_idx:end_idx, cause_service]
    cause_mean = baseline_stats[cause_service]["mean"]
    cause_std = baseline_stats[cause_service]["std"]
    cause_z_score = (cause_anomaly.mean() - cause_mean) / (cause_std + 1e-9)

    # ── Effect anomaly magnitude (for explanation only) ──────────────────
    effect_anomaly = metrics_df.loc[start_idx:end_idx, effect_service]
    effect_mean = baseline_stats[effect_service]["mean"]
    effect_std = baseline_stats[effect_service]["std"]
    effect_z_score = (effect_anomaly.mean() - effect_mean) / (effect_std + 1e-9)

    # ── Correlation-based explained variance ─────────────────────────────
    correlation = metrics_df[cause_service].corr(metrics_df[effect_service])
    explained_variance = correlation**2

    # ── Combine: magnitude × explained variance, capped at 3σ ────────────
    cause_contribution = min(1.0, abs(cause_z_score) / 3.0)
    confidence = explained_variance * cause_contribution
    confidence = max(0.0, min(1.0, confidence))

    explanation = (
        f"If {cause_service} had remained at baseline levels "
        f"(mean={cause_mean:.2f}, std={cause_std:.2f}), there is a "
        f"{confidence * 100:.0f}% probability that {effect_service} would not "
        f"have experienced the anomaly (observed z-score: {effect_z_score:.1f}\u03c3)."
    )

    return confidence, explanation
