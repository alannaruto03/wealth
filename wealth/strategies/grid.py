"""Crypto grid-trading strategy — interface-complete STUB.

Grid trading places a ladder of buy/sell orders at fixed intervals around a
reference price and profits from oscillation in a ranging market. It is
inherently order-book / limit-order based rather than target-weight based, so a
faithful implementation needs the live order API (see wealth/broker). This stub
documents the intended behaviour and the parameters it will take.

Planned params:
  levels        number of grid lines above and below the reference
  spacing_pct   distance between adjacent grid lines, as a fraction
  ref           reference price method ("first" | "sma")
"""
from __future__ import annotations

from typing import Dict

import pandas as pd

from wealth.strategies.base import Strategy, register


@register("grid")
class Grid(Strategy):
    def generate_weights(self, data: Dict[str, pd.DataFrame]) -> pd.DataFrame:
        raise NotImplementedError(
            "grid trading is a planned strategy and not implemented yet; it "
            "requires limit-order support from the live broker layer."
        )
