"""Antonacci-style Dual Momentum (cross-sectional + absolute).

At each rebalance:
  * relative momentum: rank assets by trailing ``lookback`` return
  * absolute momentum: only hold the top asset if its trailing return beats
    cash (``abs_threshold``, default 0 = positive). Otherwise go to cash.
Holds a single asset at 100% (or cash). Rebalances every ``rebalance`` bars.

This is naturally cross-sectional: it consumes all symbols and emits a joint
weight frame.
"""
from __future__ import annotations

from typing import Dict

import numpy as np
import pandas as pd

from wealth.strategies.base import Strategy, register


@register("dual_momentum")
class DualMomentum(Strategy):
    def __init__(
        self,
        lookback: int = 126,
        rebalance: int = 21,
        abs_threshold: float = 0.0,
        **params,
    ):
        super().__init__(
            lookback=lookback,
            rebalance=rebalance,
            abs_threshold=abs_threshold,
            **params,
        )
        self.lookback = int(lookback)
        self.rebalance = int(rebalance)
        self.abs_threshold = float(abs_threshold)

    def warmup(self) -> int:
        return self.lookback + 1

    def generate_weights(self, data: Dict[str, pd.DataFrame]) -> pd.DataFrame:
        closes = self._close_frame(data)
        # Trailing return over the lookback window, known as of bar t.
        mom = closes / closes.shift(self.lookback) - 1.0

        weights = pd.DataFrame(0.0, index=closes.index, columns=closes.columns)
        held = None  # currently held symbol (or None for cash)

        for i, ts in enumerate(closes.index):
            if i < self.lookback:
                continue
            # Rebalance on a fixed cadence relative to the first valid bar.
            if (i - self.lookback) % self.rebalance == 0:
                row = mom.loc[ts]
                if row.notna().any():
                    best = row.idxmax()
                    if row[best] > self.abs_threshold:
                        held = best
                    else:
                        held = None  # absolute filter -> cash
            if held is not None:
                weights.at[ts, held] = 1.0

        return weights
