"""Walk-forward optimization — tuning to performance WITHOUT curve-fitting.

The data is split into consecutive folds. For each fold we:
  1. optimize parameters on the in-sample (training) window, then
  2. measure those parameters on the next, unseen out-of-sample window.

We then select the parameters that are most robust *out-of-sample* across folds
(by median OOS score), not the single best in-sample fit. Guardrails:
  * require a minimum number of folds,
  * reject a candidate whose median OOS score is non-positive,
  * report both in-sample and OOS so degradation is visible.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pandas as pd

from wealth.engine.backtest import Backtester
from wealth.engine.costs import CostModel
from wealth.tuning.optimize import param_grid, score_metric
from wealth.strategies.base import get_strategy


def _slice(data: Dict[str, pd.DataFrame], lo, hi) -> Dict[str, pd.DataFrame]:
    return {s: df.iloc[lo:hi] for s, df in data.items()}


@dataclass
class FoldResult:
    fold: int
    best_params: Dict
    in_sample_score: float
    out_sample_score: float


@dataclass
class WalkForwardReport:
    metric: str
    folds: List[FoldResult]
    recommended_params: Optional[Dict]
    recommended_oos_median: float
    accepted: bool
    reason: str = ""
    candidate_scores: Dict[str, float] = field(default_factory=dict)

    def summary_text(self) -> str:
        lines = [f"Walk-forward ({self.metric}) — {len(self.folds)} folds"]
        for f in self.folds:
            lines.append(
                f"  fold {f.fold}: IS={f.in_sample_score:.3f} "
                f"OOS={f.out_sample_score:.3f} params={f.best_params}"
            )
        verdict = "ACCEPTED" if self.accepted else "REJECTED"
        lines.append(
            f"{verdict}: {self.recommended_params} "
            f"(median OOS {self.recommended_oos_median:.3f}) {self.reason}".rstrip()
        )
        return "\n".join(lines)


def walk_forward(
    strategy_name: str,
    data: Dict[str, pd.DataFrame],
    grid: Dict[str, List],
    metric: str = "calmar",
    n_folds: int = 4,
    min_folds: int = 3,
    cost_model: Optional[CostModel] = None,
    cash: float = 100_000.0,
    periods_per_year: Optional[float] = None,
) -> WalkForwardReport:
    bt = Backtester(cost_model=cost_model or CostModel(), cash=cash)
    combos = param_grid(grid)

    # Build evenly sized consecutive segments; fold k trains on seg k, tests k+1.
    any_df = next(iter(data.values()))
    n = len(any_df)
    if n_folds < 2 or n < (min_folds + 1) * 5:
        return WalkForwardReport(
            metric=metric, folds=[], recommended_params=None,
            recommended_oos_median=float("nan"), accepted=False,
            reason="insufficient data for walk-forward",
        )

    seg = n // (n_folds + 1)
    folds: List[FoldResult] = []
    # Track each candidate's OOS scores across folds for robust selection.
    oos_by_params: Dict[str, List[float]] = {str(c): [] for c in combos}
    params_by_key = {str(c): c for c in combos}

    for k in range(n_folds):
        tr_lo, tr_hi = 0, seg * (k + 1)        # expanding in-sample window
        te_lo, te_hi = seg * (k + 1), seg * (k + 2)
        train = _slice(data, tr_lo, tr_hi)
        test = _slice(data, te_lo, te_hi)

        best = None
        best_score = float("-inf")
        for params in combos:
            strat = get_strategy(strategy_name, **params)
            is_score = score_metric(bt.run(strat, train), metric, periods_per_year)
            # OOS score for this candidate on this fold (for robust selection).
            oos = score_metric(
                bt.run(get_strategy(strategy_name, **params), test), metric, periods_per_year
            )
            if not pd.isna(oos):
                oos_by_params[str(params)].append(oos)
            if not pd.isna(is_score) and is_score > best_score:
                best_score, best = is_score, params

        if best is not None:
            test_strat = get_strategy(strategy_name, **best)
            fold_oos = score_metric(bt.run(test_strat, test), metric, periods_per_year)
            folds.append(FoldResult(k, best, best_score, fold_oos))

    # Robust selection: candidate with the best MEDIAN out-of-sample score.
    candidate_medians = {
        key: statistics.median(scores)
        for key, scores in oos_by_params.items()
        if scores
    }
    if not candidate_medians:
        return WalkForwardReport(
            metric=metric, folds=folds, recommended_params=None,
            recommended_oos_median=float("nan"), accepted=False,
            reason="no out-of-sample scores",
        )

    best_key = max(candidate_medians, key=candidate_medians.get)
    best_median = candidate_medians[best_key]
    accepted = len(folds) >= min_folds and best_median > 0
    reason = "" if accepted else "median OOS <= 0 or too few folds; keep current params"

    return WalkForwardReport(
        metric=metric,
        folds=folds,
        recommended_params=params_by_key[best_key],
        recommended_oos_median=best_median,
        accepted=accepted,
        reason=reason,
        candidate_scores=candidate_medians,
    )
