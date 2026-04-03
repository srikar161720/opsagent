"""PC algorithm wrapper for causal relationship discovery.

Wraps causal-learn's PC (Peter-Clark) algorithm with Fisher's Z
conditional independence test to discover directed causal graphs
from service metric time-series data.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from causallearn.search.ConstraintBased.PC import pc
from causallearn.utils.cit import fisherz

from src.causal_discovery.graph_utils import CausalEdge


def discover_causal_graph(
    metrics_df: pd.DataFrame,
    alpha: float = 0.05,
    stable: bool = True,
    uc_rule: int = 0,
    uc_priority: int = 2,
) -> Any:
    """Discover causal relationships between services using the PC algorithm.

    Args:
        metrics_df: DataFrame where columns are service metrics and rows are
            time points.  All columns must be numeric (float/int).  No NaNs.
        alpha: Significance level for conditional independence tests.
        stable: Use stabilized skeleton discovery (order-independent).
        uc_rule: Rule for orienting unshielded colliders (0 = uc_sepset).
        uc_priority: Conflict resolution priority (2 = prioritize existing).

    Returns:
        A causal-learn ``CausalGraph`` object.  Edge semantics::

            cg.G.graph[j, i] == 1  and cg.G.graph[i, j] == -1  →  i → j
            cg.G.graph[i, j] == -1 and cg.G.graph[j, i] == -1  →  i — j
            cg.G.graph[i, j] == 1  and cg.G.graph[j, i] == 1   →  i ↔ j
    """
    cg = pc(
        data=metrics_df.values,
        alpha=alpha,
        indep_test=fisherz,
        stable=stable,
        uc_rule=uc_rule,
        uc_priority=uc_priority,
    )
    return cg


def create_time_lags(
    df: pd.DataFrame,
    lags: list[int] | None = None,
) -> pd.DataFrame:
    """Create time-lagged feature columns for causal discovery.

    Lagged columns are named ``{original_col}_lag{n}``.  Rows with NaN
    values introduced by shifting are dropped automatically.

    Args:
        df: DataFrame of windowed metric features (no NaNs expected in input).
        lags: List of integer window offsets.  Defaults to ``[1, 2, 5]``.

    Returns:
        DataFrame with original + lagged columns; leading NaN rows removed.
    """
    if lags is None:
        lags = [1, 2, 5]

    lagged_dfs = [df]
    for lag in lags:
        shifted = df.shift(lag)
        shifted.columns = [f"{col}_lag{lag}" for col in df.columns]
        lagged_dfs.append(shifted)

    result = pd.concat(lagged_dfs, axis=1)
    result = result.dropna()
    return result


def parse_causal_graph(
    cg: Any,
    column_names: list[str],
) -> list[CausalEdge]:
    """Extract directed edges from a causal-learn ``CausalGraph`` object.

    Only directed edges (``i → j``) are returned.  Undirected and
    bidirectional edges are ignored.

    Args:
        cg: The ``CausalGraph`` returned by :func:`discover_causal_graph`.
        column_names: Column names corresponding to the matrix indices,
            in the same order as the columns of the DataFrame passed to PC.

    Returns:
        List of :class:`CausalEdge` instances (``confidence`` is set to 0.0
        and should be populated later via counterfactual scoring).
    """
    graph_matrix: np.ndarray = cg.G.graph
    n = graph_matrix.shape[0]
    edges: list[CausalEdge] = []

    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            # causal-learn convention: graph[j,i]==1 and graph[i,j]==-1 → i→j
            if graph_matrix[j, i] == 1 and graph_matrix[i, j] == -1:
                # Determine lag from column name (e.g. "cpu_lag2" → lag=2)
                source_name = column_names[i]
                target_name = column_names[j]
                lag = _extract_lag(source_name)

                edges.append(
                    CausalEdge(
                        source=source_name,
                        target=target_name,
                        confidence=0.0,
                        lag=lag,
                    )
                )

    return edges


def _extract_lag(column_name: str) -> int:
    """Extract lag value from a column name like ``"cpu_lag2"``.

    Returns 0 if the column has no lag suffix.
    """
    if "_lag" in column_name:
        try:
            return int(column_name.rsplit("_lag", 1)[1])
        except (ValueError, IndexError):
            pass
    return 0
