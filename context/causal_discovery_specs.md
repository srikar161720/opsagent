# Causal Discovery Specifications

**Implementation files:**
- `src/causal_discovery/pc_algorithm.py` — PC algorithm wrapper + time-lagged features
- `src/causal_discovery/counterfactual.py` — Counterfactual confidence scoring
- `src/causal_discovery/graph_utils.py` — `CausalEdge`, `CausalGraph` dataclasses + ASCII rendering
- `tests/unit/test_causal_discovery.py` — Synthetic data validation tests

**Purpose:** Causal discovery is OpsAgent's primary differentiator over correlation-based RCA tools. The PC (Peter-Clark) algorithm produces a directed acyclic graph (DAG) over service metrics, distinguishing true causes from downstream symptoms. Counterfactual confidence then quantifies how much each causal claim is supported by the data.

---

## 1. PC Algorithm Configuration

| Parameter | Value | Rationale |
|---|---|---|
| Algorithm | PC (Peter-Clark) | Well-established, interpretable, sound and complete under faithfulness |
| Significance level (α) | 0.05 | Standard statistical threshold for conditional independence tests |
| Independence test | Fisher's Z | Assumes Gaussian; computationally fast for continuous metrics |
| Max conditioning set size | 4 | Caps exponential search at depth 4; sufficient for OTel Demo chains (max 3 hops); prevents 30+ min runtime at depth 5–6 with 32 columns (4 metrics × 2+ services × 4 lag levels) |
| `stable` | `True` | Ensures order-independent skeleton discovery |
| `uc_rule` | 0 | Default orientation rules (Meek rules) |
| `uc_priority` | 2 | Prioritizes definite non-colliders to reduce orientation conflicts |

**Library:** `causal-learn` (`pip install causal-learn`)
**Import path:** `from causallearn.search.ConstraintBased.PC import pc`

### 1.1 `discover_causal_graph()` — Core PC Wrapper

```python
# src/causal_discovery/pc_algorithm.py
from causallearn.search.ConstraintBased.PC import pc
from causallearn.utils.cit import fisherz
import pandas as pd


def discover_causal_graph(metrics_df: pd.DataFrame, alpha: float = 0.05):
    """
    Discover causal relationships between services using the PC algorithm.

    Args:
        metrics_df: DataFrame where columns = service metrics, rows = time points.
                    All columns must be numeric (float/int). No NaNs.
        alpha:      Significance level for conditional independence tests.

    Returns:
        cg: causal-learn GeneralGraph object. Access edges via cg.G.graph:
            cg.G.graph[i][j] == -1 and cg.G.graph[j][i] == 1  →  i → j (i causes j)
            cg.G.graph[i][j] == -1 and cg.G.graph[j][i] == -1 →  i — j (undirected)
    """
    cg = pc(
        data=metrics_df.values,
        alpha=alpha,
        indep_test=fisherz,
        stable=True,
        uc_rule=0,
        uc_priority=2,
    )
    return cg
```

> **Note:** `metrics_df` should contain only metric columns relevant to the anomaly window (e.g., `latency_p99`, `error_rate`, `cpu_usage` per service). Drop non-metric columns (timestamps, labels) before calling.

---

## 2. Time-Lagged Features

The PC algorithm operates on instantaneous snapshots; it cannot detect A → B relationships where B responds to A with a delay. To capture **temporal causality**, we augment the feature matrix with lagged versions of each metric before running PC.

| Lag | Delay | Captures |
|---|---|---|
| Lag 1 | 60 seconds (1 window) | Immediate upstream → downstream effects |
| Lag 2 | 120 seconds (2 windows) | Short-term propagation (e.g., queue buildup) |
| Lag 5 | 300 seconds (5 windows) | Delayed effects (e.g., memory leak, cold cache) |

### 2.1 `create_time_lags()` — Feature Augmentation

```python
# src/causal_discovery/pc_algorithm.py
def create_time_lags(df: pd.DataFrame, lags: list[int] = [1, 2, 5]) -> pd.DataFrame:
    """
    Create time-lagged feature columns for causal discovery.

    Lagged columns are named: {original_col}_lag{n}
    Rows with NaN values introduced by shifting are dropped automatically.

    Args:
        df:   DataFrame of windowed metric features (no NaNs expected in input).
        lags: List of integer window offsets to create lags for.

    Returns:
        DataFrame with original + lagged columns; leading NaN rows removed.
    """
    lagged_dfs = [df]
    for lag in lags:
        lagged = df.shift(lag)
        lagged.columns = [f"{col}_lag{lag}" for col in df.columns]
        lagged_dfs.append(lagged)

    result = pd.concat(lagged_dfs, axis=1)
    result = result.dropna()
    return result
```

**Typical usage in the agent's `discover_causation` tool:**
```python
# In src/agent/tools/discover_causation.py
lagged_df = create_time_lags(metrics_df, lags=[1, 2, 5])
cg = discover_causal_graph(lagged_df, alpha=0.05)
```

> **Caution:** Lagging quadruples the column count (original + 3 lags). For 15 services × 3 metrics = 45 base columns, the lagged matrix becomes 180 columns (45 × 4). PC runtime scales roughly as O(p²·n) where p = columns — monitor runtime and reduce lags or metric count if needed.

---

## 3. Counterfactual Confidence Scoring

### 3.1 Conceptual Framing

Counterfactual confidence answers: **"If the suspected root cause had not occurred, what is the probability the downstream effect would not have occurred?"**

This transforms a raw causal graph edge (A → B) into an **interventional probability estimate** grounded in the actual anomaly magnitudes observed. This is what separates OpsAgent from CIRCA and other purely algorithmic baselines — the score is human-readable, evidence-backed, and surfaced directly in the RCA report.

### 3.2 `calculate_counterfactual_confidence()` — Full Implementation

```python
# src/causal_discovery/counterfactual.py
import pandas as pd


def calculate_counterfactual_confidence(
    metrics_df: pd.DataFrame,
    cause_service: str,
    effect_service: str,
    anomaly_window: tuple[int, int],  # (start_idx, end_idx) — integer row indices
    baseline_stats: dict,             # {service: {"mean": float, "std": float}}
) -> tuple[float, str]:
    """
    Estimate counterfactual confidence for a causal claim (cause_service → effect_service).

    Method: Interventional Probability Estimation
      1. Measure how anomalous the cause was (z-score relative to baseline).
      2. Measure Pearson correlation between cause and effect over the full series.
      3. Confidence = (correlation²) × min(1.0, |cause_z_score| / 3.0)

    This is a simplified approximation. More rigorous alternatives (do-calculus,
    structural causal models) exist but are computationally expensive for real-time RCA.

    Args:
        metrics_df:      Full metric DataFrame (baseline + anomaly window combined).
        cause_service:   Column name of the causal service metric.
        effect_service:  Column name of the downstream effect metric.
        anomaly_window:  (start_idx, end_idx) row index range of the anomaly period.
        baseline_stats:  Pre-computed per-service mean and std over the baseline period.

    Returns:
        confidence:  Float in [0.0, 1.0]. Higher = stronger causal support.
        explanation: Human-readable sentence for the RCA report.
    """
    start_idx, end_idx = anomaly_window

    # --- Cause anomaly magnitude ---
    cause_anomaly = metrics_df.loc[start_idx:end_idx, cause_service]
    cause_mean = baseline_stats[cause_service]["mean"]
    cause_std = baseline_stats[cause_service]["std"]
    cause_z_score = (cause_anomaly.mean() - cause_mean) / (cause_std + 1e-9)

    # --- Effect anomaly magnitude (for explanation only; not used in confidence calc) ---
    effect_anomaly = metrics_df.loc[start_idx:end_idx, effect_service]
    effect_mean = baseline_stats[effect_service]["mean"]
    effect_std = baseline_stats[effect_service]["std"]
    effect_z_score = (effect_anomaly.mean() - effect_mean) / (effect_std + 1e-9)

    # --- Correlation-based explained variance ---
    correlation = metrics_df[cause_service].corr(metrics_df[effect_service])
    explained_variance = correlation ** 2

    # --- Combine: magnitude × explained variance, capped at 3σ for cause ---
    cause_contribution = min(1.0, abs(cause_z_score) / 3.0)
    confidence = explained_variance * cause_contribution
    confidence = max(0.0, min(1.0, confidence))  # clamp to [0, 1]

    explanation = (
        f"If {cause_service} had remained at baseline levels "
        f"(mean={cause_mean:.2f}, std={cause_std:.2f}), there is a "
        f"{confidence * 100:.0f}% probability that {effect_service} would not "
        f"have experienced the anomaly (observed z-score: {effect_z_score:.1f}σ)."
    )

    return confidence, explanation
```

### 3.3 Baseline Stats Helper

```python
# src/causal_discovery/counterfactual.py
def compute_baseline_stats(baseline_df: pd.DataFrame) -> dict:
    """
    Compute per-column mean and std from the baseline period (pre-fault data).

    Args:
        baseline_df: Metric DataFrame containing only the baseline (normal) window.

    Returns:
        Dict mapping column name → {"mean": float, "std": float}
    """
    stats = {}
    for col in baseline_df.columns:
        stats[col] = {
            "mean": float(baseline_df[col].mean()),
            "std": float(baseline_df[col].std()),
        }
    return stats
```

---

## 4. Causal Graph Output Format

These dataclasses are the canonical output of the causal discovery pipeline, consumed by the agent's `discover_causation` tool and serialized into the RCA report.

```python
# src/causal_discovery/graph_utils.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List


@dataclass
class CausalEdge:
    source: str       # Cause service name (e.g., "cartservice")
    target: str       # Effect service name (e.g., "frontend")
    confidence: float # Counterfactual confidence in [0.0, 1.0]
    lag: int          # Time lag in windows at which this edge was detected
    evidence: str     # Human-readable supporting evidence (for RCA report)


@dataclass
class CausalGraph:
    edges: List[CausalEdge]
    root_cause: str               # Most likely root cause service
    root_cause_confidence: float  # Confidence for the root cause claim

    def to_ascii(self) -> str:
        """
        Render a simple ASCII causal graph for embedding in the RCA report.

        Example output:
            cartservice (root cause, conf=0.87)
              └─[lag=1, conf=0.82]→ checkoutservice
                  └─[lag=2, conf=0.71]→ frontend
        """
        if not self.edges:
            return "  (no causal edges discovered)"

        lines = [
            f"  {self.root_cause} [ROOT CAUSE — confidence: {self.root_cause_confidence:.0%}]"
        ]
        for edge in self.edges:
            if edge.source == self.root_cause:  # only render edges originating from root cause
                lines.append(
                    f"    └─[lag={edge.lag}w, conf={edge.confidence:.0%}]"
                    f"→ {edge.target}"
                )
        return "\n".join(lines)

    def top_edges(self, n: int = 3) -> List[CausalEdge]:
        """Return the n highest-confidence edges, sorted descending."""
        return sorted(self.edges, key=lambda e: e.confidence, reverse=True)[:n]
```

---

## 5. Synthetic Data Testing Strategy

**Task:** `4.9` · **Time estimate:** 4–6 hours
**File:** `tests/unit/test_causal_discovery.py`

Validate the PC algorithm against **known ground truth** before integrating it into the full agent pipeline. Do not rely solely on real OTel Demo data for this — synthetic data with an injected causal structure allows exact correctness checks.

### 5.1 Synthetic Ground Truth Setup

```python
# tests/unit/test_causal_discovery.py
import numpy as np
import pandas as pd
from src.causal_discovery.pc_algorithm import discover_causal_graph, create_time_lags

def generate_synthetic_causal_data(n_samples: int = 500, seed: int = 42) -> pd.DataFrame:
    """
    Generate synthetic time-series with a known causal structure:

        service_A → service_B → service_C

    service_A is the root cause; service_C is the most downstream effect.
    Gaussian noise added to all series to simulate measurement uncertainty.
    """
    rng = np.random.default_rng(seed)
    service_a = rng.normal(loc=50, scale=5, size=n_samples)
    service_b = 0.7 * service_a + rng.normal(0, 3, n_samples)   # A causes B
    service_c = 0.6 * service_b + rng.normal(0, 3, n_samples)   # B causes C
    return pd.DataFrame({"service_a": service_a, "service_b": service_b, "service_c": service_c})
```

### 5.2 Correctness Assertions

```python
def test_pc_discovers_correct_chain():
    """PC algorithm must recover A→B→C from synthetic data."""
    df = generate_synthetic_causal_data()
    cg = discover_causal_graph(df, alpha=0.05)

    graph_matrix = cg.G.graph
    col_names = list(df.columns)
    idx = {name: i for i, name in enumerate(col_names)}

    # Expect directed edge: service_a → service_b
    a, b = idx["service_a"], idx["service_b"]
    assert graph_matrix[a][b] == -1 and graph_matrix[b][a] == 1, \
        "Expected directed edge service_a → service_b"

    # Expect directed edge: service_b → service_c
    b2, c = idx["service_b"], idx["service_c"]
    assert graph_matrix[b2][c] == -1 and graph_matrix[c][b2] == 1, \
        "Expected directed edge service_b → service_c"


def test_counterfactual_confidence_in_range():
    """Counterfactual confidence must always be in [0, 1]."""
    from src.causal_discovery.counterfactual import (
        calculate_counterfactual_confidence,
        compute_baseline_stats,
    )
    df = generate_synthetic_causal_data()
    baseline_df = df.iloc[:200]   # First 200 rows = baseline
    anomaly_start, anomaly_end = 300, 400

    baseline_stats = compute_baseline_stats(baseline_df)
    conf, explanation = calculate_counterfactual_confidence(
        metrics_df=df,
        cause_service="service_a",
        effect_service="service_b",
        anomaly_window=(anomaly_start, anomaly_end),
        baseline_stats=baseline_stats,
    )
    assert 0.0 <= conf <= 1.0, f"Confidence out of range: {conf}"
    assert isinstance(explanation, str) and len(explanation) > 0


def test_time_lags_drop_nans():
    """Lagged DataFrame must have no NaN rows and correct column count."""
    df = generate_synthetic_causal_data(n_samples=100)
    lagged = create_time_lags(df, lags=[1, 2, 5])

    assert lagged.isna().sum().sum() == 0, "Lagged DataFrame contains NaNs"
    expected_cols = len(df.columns) * (1 + 3)  # original + 3 lags
    assert lagged.shape[1] == expected_cols, \
        f"Expected {expected_cols} columns, got {lagged.shape[1]}"
    assert lagged.shape[0] == 100 - 5, \
        "Expected 95 rows after dropping NaNs from max lag=5"
```

### 5.3 Known Failure Modes to Watch For

| Issue | Symptom | Fix |
|---|---|---|
| Undirected edges (A — B) | `graph_matrix[i][j] == -1` but also `graph_matrix[j][i] == -1` | Expected for some edges; treat as bidirectional, use counterfactual to break ties |
| No edges discovered | Empty graph | Check `alpha` — too small makes test too conservative; try `alpha=0.1` |
| Correlated but not causal (confounders) | False edge A → C when true structure is A → B → C | Add conditioning set; causal-learn handles this via PC skeleton phase |
| Runtime explosion on large lag matrix | >60 columns, PC hangs | Reduce `max_cond_set_size`, reduce lag count, or subsample time series |
| `fisherz` assumption violation | Non-Gaussian metrics (counts, rates) | Consider switching `indep_test` to `kci` (kernel-based) — slower but assumption-free |

---

## 6. Integration with the Agent's `discover_causation` Tool

The `discover_causation` tool in `src/agent/tools/discover_causation.py` orchestrates the full causal discovery pipeline end-to-end. The pipeline order is:

```
1. Fetch metric windows from Prometheus (via query_metrics tool)
2. create_time_lags(metrics_df) → lagged feature matrix
3. discover_causal_graph(lagged_df) → raw causal-learn graph
4. Parse cg.G.graph matrix → List[CausalEdge] (directed edges only)
5. compute_baseline_stats(baseline_df) → baseline_stats dict
6. calculate_counterfactual_confidence(...) per edge → confidence + explanation
7. Identify root_cause = edge source with highest confidence and no incoming edges
8. Return CausalGraph(edges, root_cause, root_cause_confidence)
```

The `CausalGraph.to_ascii()` output is embedded directly into the RCA report template. See `context/agent_specs.md` for the full `RCA_REPORT_TEMPLATE` and `AgentState` definition.

---

## 7. Key Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| PC vs. LiNGAM | PC | LiNGAM assumes non-Gaussian noise; PC is more general for metrics |
| Fisher's Z vs. KCI | Fisher's Z | ~10× faster; acceptable for Gaussian-ish metrics like latency/CPU |
| Simplified counterfactual | Correlation² × z-score | Full do-calculus SCM requires explicit causal model; this approximation is sufficient and fast for real-time RCA |
| Fixed α = 0.05 | Not tuned per dataset | Avoids data leakage during evaluation; tune only during dev on synthetic data |
| Max conditioning set = 4 | Hard cap | With time-lagged features (32 columns), unrestricted depth causes 30+ min runtime at depth 5–6. Depth 4 captures all OTel Demo causal chains (max 3 hops) and completes in <30s. C(30,4) = 27,405 — fast; C(30,5) = 142,506 — impractical. |
