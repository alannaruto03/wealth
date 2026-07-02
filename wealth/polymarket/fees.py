"""Polymarket taker fee curve for short-horizon crypto markets (2026).

Since Jan 2026 these markets charge takers ``shares * rate * p * (1-p)``
(crypto tier rate ~= 7.2%): the fee peaks at p=0.50 (~1.8c/share) and
vanishes toward 0/1. Makers pay zero and earn rebates. This curve is what
killed naive latency-taking — any taker trade must clear it.
"""
from __future__ import annotations


def taker_fee_per_share(price: float, rate: float = 0.072) -> float:
    """Fee in $ per share for a taker fill at ``price``."""
    p = min(max(price, 0.0), 1.0)
    return rate * p * (1.0 - p)


def taker_fee(shares: float, price: float, rate: float = 0.072) -> float:
    return shares * taker_fee_per_share(price, rate)


def breakeven_edge(price: float, rate: float = 0.072) -> float:
    """Minimum (fair_value - price) edge a taker buy needs to break even."""
    return taker_fee_per_share(price, rate)
