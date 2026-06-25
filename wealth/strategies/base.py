"""Strategy abstract base class and registry.

A Strategy is a *pure signal generator*: given aligned OHLCV history for one or
more symbols, it emits target portfolio weights. It never touches cash or sends
orders — the engine (backtest) and the runner (live) translate weights into
fills. This is what lets the exact same strategy code run in backtest and live.

Lookahead rule: the weight on row ``t`` may only use data up to and including
row ``t``. The engine applies weights on the *next* bar, so strategies must NOT
pre-shift their own signals.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable, Dict, Type

import pandas as pd

STRATEGY_REGISTRY: Dict[str, Type["Strategy"]] = {}


def register(name: str) -> Callable[[Type["Strategy"]], Type["Strategy"]]:
    """Class decorator that records a strategy under ``name``."""
    def deco(cls: Type["Strategy"]) -> Type["Strategy"]:
        key = name.lower()
        if key in STRATEGY_REGISTRY:
            raise ValueError(f"strategy {name!r} already registered")
        cls.name = key
        STRATEGY_REGISTRY[key] = cls
        return cls
    return deco


def get_strategy(name: str, **params) -> "Strategy":
    """Instantiate a registered strategy by name with ``params``."""
    key = name.lower()
    if key not in STRATEGY_REGISTRY:
        available = ", ".join(sorted(STRATEGY_REGISTRY)) or "(none)"
        raise KeyError(f"unknown strategy {name!r}; available: {available}")
    return STRATEGY_REGISTRY[key](**params)


def available_strategies() -> list[str]:
    return sorted(STRATEGY_REGISTRY)


class Strategy(ABC):
    """Base class for all strategies."""

    name: str = "base"

    def __init__(self, **params):
        self.params = params

    @abstractmethod
    def generate_weights(self, data: Dict[str, pd.DataFrame]) -> pd.DataFrame:
        """Return target weights as a DataFrame indexed by date.

        Parameters
        ----------
        data : dict[str, DataFrame]
            symbol -> OHLCV DataFrame with a shared DatetimeIndex and at least
            a ``close`` column (lowercase OHLCV).

        Returns
        -------
        DataFrame
            index = dates, columns = symbols, values = target weight in
            ``[-1, 1]`` (fraction of equity). 0 = flat. By convention
            ``sum(|w|) <= 1`` so the book is never more than fully invested.
        """

    def warmup(self) -> int:
        """Leading bars with no valid signal (e.g. the longest lookback)."""
        return 0

    # -- helpers shared by concrete strategies -------------------------------
    @staticmethod
    def _close_frame(data: Dict[str, pd.DataFrame]) -> pd.DataFrame:
        """Stack the ``close`` column of every symbol into one aligned frame."""
        closes = {sym: df["close"] for sym, df in data.items()}
        return pd.DataFrame(closes).sort_index()

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        p = ", ".join(f"{k}={v}" for k, v in self.params.items())
        return f"{self.__class__.__name__}({p})"
