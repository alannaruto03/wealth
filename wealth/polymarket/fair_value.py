"""Fair probability that BTC finishes the period up, and the vol feed behind it.

The market pays $1 if BTC's price at period end is above the period open.
Model it as a digital option: with drift ~0 over minutes, P(up) is the
probability that a Gaussian log-return over the remaining time is positive
given the distance already travelled from the open.
"""
from __future__ import annotations

import math
from abc import ABC, abstractmethod
from typing import Dict, Optional, Type

import numpy as np
import pandas as pd

# Polymarket's price grid is $0.001..$0.999; never claim more certainty.
MIN_PROB = 0.001
MAX_PROB = 0.999


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


class FairValueModel(ABC):
    """P(BTC at expiry > period open) given current state."""

    def __init__(self, **params):
        self.params = params

    @abstractmethod
    def prob_up(
        self,
        spot: float,
        period_open: float,
        t_remaining_s: float,
        sigma_per_sqrt_s: float,
    ) -> float:
        """Probability in [MIN_PROB, MAX_PROB] that spot ends above period_open."""


MODEL_REGISTRY: Dict[str, Type[FairValueModel]] = {}


def register(name: str):
    def deco(cls: Type[FairValueModel]) -> Type[FairValueModel]:
        MODEL_REGISTRY[name] = cls
        return cls
    return deco


def get_model(name: str, **params) -> FairValueModel:
    if name not in MODEL_REGISTRY:
        raise ValueError(f"unknown fair-value model '{name}'; available: {sorted(MODEL_REGISTRY)}")
    return MODEL_REGISTRY[name](**params)


@register("gaussian_digital")
class GaussianDigitalModel(FairValueModel):
    """P(up) = Phi( ln(spot/open) / (sigma * sqrt(T)) ), zero drift."""

    def prob_up(
        self,
        spot: float,
        period_open: float,
        t_remaining_s: float,
        sigma_per_sqrt_s: float,
    ) -> float:
        if spot <= 0 or period_open <= 0:
            raise ValueError("spot and period_open must be positive")
        distance = math.log(spot / period_open)
        if t_remaining_s <= 0 or sigma_per_sqrt_s <= 0:
            # Expired (or no vol signal): the outcome is the current indicator.
            if distance > 0:
                p = 1.0
            elif distance < 0:
                p = 0.0
            else:
                p = 0.5
        else:
            p = _norm_cdf(distance / (sigma_per_sqrt_s * math.sqrt(t_remaining_s)))
        return min(MAX_PROB, max(MIN_PROB, p))


class VolEstimator:
    """EWMA realized volatility from 1-minute closes, in per-sqrt-second units.

    sigma_per_sqrt_s * sqrt(seconds) gives the stdev of the log-return over
    that horizon under the iid assumption.
    """

    def __init__(self, halflife_s: float = 300.0, lookback_min: int = 120):
        self.halflife_s = halflife_s
        self.lookback_min = lookback_min
        self.sigma_per_sqrt_s: Optional[float] = None

    def update(self, closes: pd.Series) -> Optional[float]:
        """Feed recent 1m closes (chronological). Returns sigma or None if too short."""
        closes = closes.dropna().astype(float).tail(self.lookback_min)
        if len(closes) < 3:
            return self.sigma_per_sqrt_s
        rets = np.log(closes / closes.shift(1)).dropna()
        # halflife in bars: one bar = 60s
        var_1m = float(rets.pow(2).ewm(halflife=self.halflife_s / 60.0).mean().iloc[-1])
        self.sigma_per_sqrt_s = math.sqrt(max(var_1m, 0.0) / 60.0)
        return self.sigma_per_sqrt_s
