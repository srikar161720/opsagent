"""Unit tests for causal discovery module.

Tests cover graph dataclasses, PC algorithm wrapper, time-lag generation,
causal graph parsing, and counterfactual confidence scoring.  Synthetic
data with a known A→B→C causal chain is used for ground-truth validation.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.causal_discovery.counterfactual import (
    calculate_counterfactual_confidence,
    compute_baseline_stats,
)
from src.causal_discovery.graph_utils import CausalEdge, CausalGraph
from src.causal_discovery.pc_algorithm import (
    create_time_lags,
    discover_causal_graph,
    parse_causal_graph,
)

# ── Fixtures ─────────────────────────────────────────────────────────────────


def _generate_synthetic_causal_data(
    n_samples: int = 500,
    seed: int = 42,
) -> pd.DataFrame:
    """Generate synthetic data with known causal structure: A → B → C."""
    rng = np.random.default_rng(seed)
    service_a = rng.normal(loc=50, scale=5, size=n_samples)
    service_b = 0.7 * service_a + rng.normal(0, 3, size=n_samples)
    service_c = 0.6 * service_b + rng.normal(0, 3, size=n_samples)
    return pd.DataFrame(
        {
            "service_a": service_a,
            "service_b": service_b,
            "service_c": service_c,
        }
    )


@pytest.fixture()
def synthetic_df() -> pd.DataFrame:
    """Synthetic A→B→C DataFrame with 500 rows."""
    return _generate_synthetic_causal_data()


@pytest.fixture()
def small_df() -> pd.DataFrame:
    """Small DataFrame for shape-focused tests."""
    return _generate_synthetic_causal_data(n_samples=100)


@pytest.fixture()
def baseline_stats(synthetic_df: pd.DataFrame) -> dict[str, dict[str, float]]:
    """Baseline stats from first 200 rows."""
    return compute_baseline_stats(synthetic_df.iloc[:200])


# ── CausalEdge Tests ─────────────────────────────────────────────────────────


class TestCausalEdge:
    def test_construction(self) -> None:
        edge = CausalEdge(
            source="cartservice",
            target="frontend",
            confidence=0.85,
            lag=1,
            evidence="High correlation",
        )
        assert edge.source == "cartservice"
        assert edge.target == "frontend"
        assert edge.confidence == 0.85
        assert edge.lag == 1
        assert edge.evidence == "High correlation"

    def test_defaults(self) -> None:
        edge = CausalEdge(source="a", target="b", confidence=0.5)
        assert edge.lag == 0
        assert edge.evidence == ""


# ── CausalGraph Tests ────────────────────────────────────────────────────────


class TestCausalGraph:
    def test_to_ascii_with_edges(self) -> None:
        edges = [
            CausalEdge("cart", "front", 0.82, lag=1),
            CausalEdge("cart", "checkout", 0.71, lag=2),
        ]
        graph = CausalGraph(
            edges=edges,
            root_cause="cart",
            root_cause_confidence=0.87,
        )
        ascii_out = graph.to_ascii()
        assert "cart" in ascii_out
        assert "ROOT CAUSE" in ascii_out
        assert "87%" in ascii_out
        assert "front" in ascii_out
        assert "checkout" in ascii_out

    def test_to_ascii_empty(self) -> None:
        graph = CausalGraph()
        assert "no causal edges" in graph.to_ascii()

    def test_to_ascii_only_root_cause_edges(self) -> None:
        """Edges not originating from root_cause should be excluded."""
        edges = [
            CausalEdge("A", "B", 0.9, lag=1),
            CausalEdge("B", "C", 0.8, lag=1),
        ]
        graph = CausalGraph(edges=edges, root_cause="A", root_cause_confidence=0.9)
        ascii_out = graph.to_ascii()
        assert "B" in ascii_out  # A→B shown
        # B→C should NOT appear (source != root_cause)
        assert "→ C" not in ascii_out

    def test_top_edges_sorted(self) -> None:
        edges = [
            CausalEdge("a", "b", 0.3),
            CausalEdge("b", "c", 0.9),
            CausalEdge("c", "d", 0.6),
        ]
        graph = CausalGraph(edges=edges, root_cause="a", root_cause_confidence=0.5)
        top = graph.top_edges(2)
        assert len(top) == 2
        assert top[0].confidence == 0.9
        assert top[1].confidence == 0.6

    def test_top_edges_more_than_available(self) -> None:
        edges = [CausalEdge("a", "b", 0.5)]
        graph = CausalGraph(edges=edges, root_cause="a", root_cause_confidence=0.5)
        assert len(graph.top_edges(10)) == 1


# ── create_time_lags Tests ───────────────────────────────────────────────────


class TestCreateTimeLags:
    def test_column_count(self, small_df: pd.DataFrame) -> None:
        """Original + 3 lag sets = 4× columns."""
        lagged = create_time_lags(small_df, lags=[1, 2, 5])
        expected = len(small_df.columns) * 4  # original + 3 lags
        assert lagged.shape[1] == expected

    def test_no_nans(self, small_df: pd.DataFrame) -> None:
        lagged = create_time_lags(small_df, lags=[1, 2, 5])
        assert lagged.isna().sum().sum() == 0

    def test_row_count_after_dropna(self, small_df: pd.DataFrame) -> None:
        """Max lag = 5 → first 5 rows dropped."""
        lagged = create_time_lags(small_df, lags=[1, 2, 5])
        assert lagged.shape[0] == len(small_df) - 5

    def test_column_names(self, small_df: pd.DataFrame) -> None:
        lagged = create_time_lags(small_df, lags=[1, 3])
        for col in small_df.columns:
            assert col in lagged.columns
            assert f"{col}_lag1" in lagged.columns
            assert f"{col}_lag3" in lagged.columns

    def test_default_lags(self, small_df: pd.DataFrame) -> None:
        """Default lags are [1, 2, 5]."""
        lagged = create_time_lags(small_df)
        assert lagged.shape[1] == len(small_df.columns) * 4

    def test_single_lag(self, small_df: pd.DataFrame) -> None:
        lagged = create_time_lags(small_df, lags=[1])
        assert lagged.shape[1] == len(small_df.columns) * 2
        assert lagged.shape[0] == len(small_df) - 1


# ── discover_causal_graph Tests ──────────────────────────────────────────────


class TestDiscoverCausalGraph:
    def test_returns_causal_graph_object(self, synthetic_df: pd.DataFrame) -> None:
        cg = discover_causal_graph(synthetic_df, alpha=0.05)
        # causal-learn CausalGraph has a G attribute with a graph matrix
        assert hasattr(cg, "G")
        assert hasattr(cg.G, "graph")

    def test_graph_matrix_shape(self, synthetic_df: pd.DataFrame) -> None:
        cg = discover_causal_graph(synthetic_df, alpha=0.05)
        n_cols = len(synthetic_df.columns)
        assert cg.G.graph.shape == (n_cols, n_cols)

    def test_discovers_a_to_b_edge(self, synthetic_df: pd.DataFrame) -> None:
        """PC must recover A→B from synthetic A→B→C data."""
        cg = discover_causal_graph(synthetic_df, alpha=0.05)
        graph = cg.G.graph
        a, b = 0, 1  # service_a=0, service_b=1

        # Directed: graph[j,i]==1 and graph[i,j]==-1 → i→j
        has_directed_a_to_b = graph[b, a] == 1 and graph[a, b] == -1
        # Or undirected (acceptable): graph[i,j]==-1 and graph[j,i]==-1
        has_undirected = graph[a, b] == -1 and graph[b, a] == -1
        assert has_directed_a_to_b or has_undirected, (
            f"Expected edge between A and B. graph[a,b]={graph[a, b]}, graph[b,a]={graph[b, a]}"
        )

    def test_discovers_b_to_c_edge(self, synthetic_df: pd.DataFrame) -> None:
        """PC must recover B→C from synthetic A→B→C data."""
        cg = discover_causal_graph(synthetic_df, alpha=0.05)
        graph = cg.G.graph
        b, c = 1, 2  # service_b=1, service_c=2

        has_directed_b_to_c = graph[c, b] == 1 and graph[b, c] == -1
        has_undirected = graph[b, c] == -1 and graph[c, b] == -1
        assert has_directed_b_to_c or has_undirected, (
            f"Expected edge between B and C. graph[b,c]={graph[b, c]}, graph[c,b]={graph[c, b]}"
        )

    def test_no_direct_a_to_c_edge(self, synthetic_df: pd.DataFrame) -> None:
        """PC should not find a direct A→C edge (conditioning on B removes it)."""
        cg = discover_causal_graph(synthetic_df, alpha=0.05)
        graph = cg.G.graph
        a, c = 0, 2
        # No edge means both entries are 0
        assert graph[a, c] == 0 and graph[c, a] == 0, (
            f"Unexpected direct edge A→C. graph[a,c]={graph[a, c]}, graph[c,a]={graph[c, a]}"
        )


# ── parse_causal_graph Tests ─────────────────────────────────────────────────


class TestParseCausalGraph:
    def test_extracts_directed_edges(self, synthetic_df: pd.DataFrame) -> None:
        cg = discover_causal_graph(synthetic_df, alpha=0.05)
        col_names = list(synthetic_df.columns)
        edges = parse_causal_graph(cg, col_names)
        assert isinstance(edges, list)
        for edge in edges:
            assert isinstance(edge, CausalEdge)

    def test_edge_sources_and_targets_are_column_names(self, synthetic_df: pd.DataFrame) -> None:
        cg = discover_causal_graph(synthetic_df, alpha=0.05)
        col_names = list(synthetic_df.columns)
        edges = parse_causal_graph(cg, col_names)
        for edge in edges:
            assert edge.source in col_names
            assert edge.target in col_names

    def test_lag_extraction_from_column_name(self) -> None:
        """Edges from lagged columns should have correct lag value."""
        from src.causal_discovery.pc_algorithm import _extract_lag

        assert _extract_lag("cpu_lag2") == 2
        assert _extract_lag("memory_usage_lag5") == 5
        assert _extract_lag("cpu_usage") == 0
        assert _extract_lag("service_a") == 0

    def test_confidence_defaults_to_zero(self, synthetic_df: pd.DataFrame) -> None:
        """Parsed edges should have confidence=0.0 (to be filled later)."""
        cg = discover_causal_graph(synthetic_df, alpha=0.05)
        col_names = list(synthetic_df.columns)
        edges = parse_causal_graph(cg, col_names)
        for edge in edges:
            assert edge.confidence == 0.0


# ── compute_baseline_stats Tests ─────────────────────────────────────────────


class TestComputeBaselineStats:
    def test_all_columns_present(self, synthetic_df: pd.DataFrame) -> None:
        stats = compute_baseline_stats(synthetic_df)
        for col in synthetic_df.columns:
            assert col in stats
            assert "mean" in stats[col]
            assert "std" in stats[col]

    def test_values_are_floats(self, synthetic_df: pd.DataFrame) -> None:
        stats = compute_baseline_stats(synthetic_df)
        for col_stats in stats.values():
            assert isinstance(col_stats["mean"], float)
            assert isinstance(col_stats["std"], float)

    def test_std_non_negative(self, synthetic_df: pd.DataFrame) -> None:
        stats = compute_baseline_stats(synthetic_df)
        for col_stats in stats.values():
            assert col_stats["std"] >= 0.0

    def test_mean_reasonable(self, synthetic_df: pd.DataFrame) -> None:
        """service_a has loc=50, so mean should be close."""
        stats = compute_baseline_stats(synthetic_df)
        assert abs(stats["service_a"]["mean"] - 50.0) < 2.0


# ── calculate_counterfactual_confidence Tests ────────────────────────────────


class TestCounterfactualConfidence:
    def test_confidence_in_range(
        self,
        synthetic_df: pd.DataFrame,
        baseline_stats: dict[str, dict[str, float]],
    ) -> None:
        conf, explanation = calculate_counterfactual_confidence(
            metrics_df=synthetic_df,
            cause_service="service_a",
            effect_service="service_b",
            anomaly_window=(300, 400),
            baseline_stats=baseline_stats,
        )
        assert 0.0 <= conf <= 1.0

    def test_explanation_is_nonempty_string(
        self,
        synthetic_df: pd.DataFrame,
        baseline_stats: dict[str, dict[str, float]],
    ) -> None:
        _, explanation = calculate_counterfactual_confidence(
            metrics_df=synthetic_df,
            cause_service="service_a",
            effect_service="service_b",
            anomaly_window=(300, 400),
            baseline_stats=baseline_stats,
        )
        assert isinstance(explanation, str)
        assert len(explanation) > 0

    def test_explanation_contains_service_names(
        self,
        synthetic_df: pd.DataFrame,
        baseline_stats: dict[str, dict[str, float]],
    ) -> None:
        _, explanation = calculate_counterfactual_confidence(
            metrics_df=synthetic_df,
            cause_service="service_a",
            effect_service="service_b",
            anomaly_window=(300, 400),
            baseline_stats=baseline_stats,
        )
        assert "service_a" in explanation
        assert "service_b" in explanation

    def test_high_confidence_for_strong_cause(
        self,
        baseline_stats: dict[str, dict[str, float]],
    ) -> None:
        """Injecting a large anomaly in A should yield high confidence for A→B."""
        rng = np.random.default_rng(99)
        n = 500
        a = rng.normal(50, 5, n)
        b = 0.7 * a + rng.normal(0, 3, n)
        # Inject strong anomaly in A at rows 300-400
        a[300:400] = 100.0
        b[300:400] = 0.7 * 100.0 + rng.normal(0, 3, 100)
        df = pd.DataFrame({"service_a": a, "service_b": b})

        stats = compute_baseline_stats(df.iloc[:200])
        conf, _ = calculate_counterfactual_confidence(
            metrics_df=df,
            cause_service="service_a",
            effect_service="service_b",
            anomaly_window=(300, 400),
            baseline_stats=stats,
        )
        assert conf > 0.3, f"Expected high confidence for strong anomaly, got {conf}"

    def test_low_confidence_for_unrelated(self) -> None:
        """Two independent series should have low confidence."""
        rng = np.random.default_rng(42)
        n = 500
        a = rng.normal(50, 5, n)
        b = rng.normal(50, 5, n)  # Independent of A
        df = pd.DataFrame({"service_a": a, "service_b": b})

        stats = compute_baseline_stats(df.iloc[:200])
        conf, _ = calculate_counterfactual_confidence(
            metrics_df=df,
            cause_service="service_a",
            effect_service="service_b",
            anomaly_window=(300, 400),
            baseline_stats=stats,
        )
        assert conf < 0.3, f"Expected low confidence for independent series, got {conf}"
