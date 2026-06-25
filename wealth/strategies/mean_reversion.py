"""Z-score mean-reversion (single asset, applied per symbol independently).

Compute a rolling z-score of price vs its moving average. Buy when price is
stretched below the mean (z <= -entry_z), exit when it reverts (z >= exit_z).
Optionally take symmetric shorts when ``allow_short`` is set.

Capital is split equally across symbols holding a position.
"""
from __future__ import annotations

from typing import Dict

import numpy as np
import pandas as pd

from wealth.strategies.base import Strategy, register


@register("mean_reversion")
class MeanReversion(Strategy):
    def __init__(
        self,
        lookback: int = 20,
        entry_z: float = 1.0,
        exit_z: float = 0.0,
        allow_short: bool = False,
        **params,
    ):
        super().__init__(
            lookback=lookback,
            entry_z=entry_z,
            exit_z=exit_z,
            allow_short=allow_short,
            **params,
        )
        self.lookback = int(lookback)
        self.entry_z = float(entry_z)
        self.exit_z = float(exit_z)
        self.allow_short = bool(allow_short)

    def warmup(self) -> int:
        return self.lookback + 1

    def generate_weights(self, data: Dict[str, pd.DataFrame]) -> pd.DataFrame:
        closes = self._close_frame(data)
        positions = pd.DataFrame(0.0, index=closes.index, columns=closes.columns)

        for sym in closes.columns:
            c = closes[sym]
            # Rolling stats use only prior bars (shift(1)) -> no lookahead.
            mean = c.shift(1).rolling(self.lookback).mean()
            std = c.shift(1).rolling(self.lookback).std(ddof=0)
            z = (c - mean) / std.replace(0.0, np.nan)

            state = np.zeros(len(c), dtype=float)
            pos = 0.0
            zv = z.to_numpy()
            for i in range(len(c)):
                if np.isnan(zv[i]):
                    state[i] = 0.0
                    continue
                if pos == 0.0:
                    if zv[i] <= -self.entry_z:
                        pos = 1.0
                    elif self.allow_short and zv[i] >= self.entry_z:
                        pos = -1.0
                elif pos > 0.0:
                    if zv[i] >= -self.exit_z:
                        pos = 0.0
                elif pos < 0.0:
                    if zv[i] <= self.exit_z:
                        pos = 0.0
                state[i] = pos
            positions[sym] = state

        # Equal weight across active symbols; sum(|w|) <= 1.
        gross = positions.abs().sum(axis=1)
        weights = positions.div(gross.where(gross > 0, 1.0), axis=0)
        return weights.fillna(0.0)
