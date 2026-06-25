"""Cash-and-carry funding-rate arbitrage — interface-complete STUB.

Market-neutral crypto trade: hold spot long and short the perpetual future of
the same asset, collecting the funding rate while being delta-neutral. Requires
spot + perp price feeds and the funding-rate series, plus a venue that supports
both legs — so it cannot be expressed as a simple long-only weight frame and is
deferred until the live broker layer exposes perps and funding data.

Planned params:
  symbol        underlying (e.g. "BTC")
  min_funding   only carry when annualized funding exceeds this threshold
"""
from __future__ import annotations

from typing import Dict

import pandas as pd

from wealth.strategies.base import Strategy, register


@register("funding_arb")
class FundingArb(Strategy):
    def generate_weights(self, data: Dict[str, pd.DataFrame]) -> pd.DataFrame:
        raise NotImplementedError(
            "funding-rate arbitrage is a planned strategy and not implemented "
            "yet; it requires spot+perp feeds and funding-rate data."
        )
