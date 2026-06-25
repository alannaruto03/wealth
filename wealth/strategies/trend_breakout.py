"""Donchian-channel trend-following / breakout strategy.

Classic Turtle-style rule, per symbol and independent:
  * go long when today's close >= the highest close of the prior ``entry_n`` bars
  * exit to flat when today's close <= the lowest close of the prior ``exit_n`` bars
Capital is split equally across whichever symbols are currently long.

Few parameters by design (entry_n, exit_n) so it resists overfitting.
"""
from __future__ import annotations

from typing import Dict

import numpy as np
import pandas as pd

from wealth.strategies.base import Strategy, register


@register("trend_breakout")
class TrendBreakout(Strategy):
    def __init__(self, entry_n: int = 20, exit_n: int = 10, **params):
        super().__init__(entry_n=entry_n, exit_n=exit_n, **params)
        self.entry_n = int(entry_n)
        self.exit_n = int(exit_n)

    def warmup(self) -> int:
        return max(self.entry_n, self.exit_n) + 1

    def generate_weights(self, data: Dict[str, pd.DataFrame]) -> pd.DataFrame:
        closes = self._close_frame(data)
        positions = pd.DataFrame(0.0, index=closes.index, columns=closes.columns)

        for sym in closes.columns:
            c = closes[sym]
            # Prior-window extremes: shift(1) so bar t only sees bars < t for the
            # channel, then compares against today's close (no lookahead).
            entry_high = c.shift(1).rolling(self.entry_n).max()
            exit_low = c.shift(1).rolling(self.exit_n).min()

            long_state = np.zeros(len(c), dtype=bool)
            in_pos = False
            cvals = c.to_numpy()
            eh = entry_high.to_numpy()
            el = exit_low.to_numpy()
            for i in range(len(c)):
                if np.isnan(eh[i]) or np.isnan(el[i]):
                    long_state[i] = False
                    continue
                if not in_pos:
                    if cvals[i] >= eh[i]:
                        in_pos = True
                else:
                    if cvals[i] <= el[i]:
                        in_pos = False
                long_state[i] = in_pos
            positions[sym] = long_state.astype(float)

        # Equal-weight across symbols that are currently long; sum(|w|) <= 1.
        n_long = positions.sum(axis=1)
        weights = positions.div(n_long.where(n_long > 0, 1.0), axis=0)
        return weights.fillna(0.0)
