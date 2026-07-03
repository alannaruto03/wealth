"""Position sizing for binary payoffs: fractional Kelly with hard caps.

A share bought at ``price`` pays $1 on a win, $0 on a loss. Full Kelly for
that bet is (p - price) / (1 - price); we scale it down (quarter-Kelly by
default) because our probability estimate is noisy, then cap by per-trade
stake and remaining exposure headroom.
"""
from __future__ import annotations

from dataclasses import dataclass

# Polymarket CLOB minimums.
MIN_ORDER_SHARES = 5.0
MIN_ORDER_NOTIONAL = 1.0  # USDC


def kelly_binary(p: float, price: float) -> float:
    """Optimal bankroll fraction for a $1-payout share bought at ``price``."""
    if not 0.0 < price < 1.0:
        return 0.0
    return max(0.0, (p - price) / (1.0 - price))


@dataclass
class SizedOrder:
    shares: float
    stake_usd: float


def stake(
    p: float,
    price: float,
    bankroll: float,
    kelly_mult: float = 0.25,
    max_stake: float = 50.0,
    headroom: float = float("inf"),
) -> SizedOrder:
    """USDC stake and share count for one entry; zero if edge or caps kill it.

    ``headroom`` is the remaining exposure allowance (per-market/total caps
    already netted out by the risk manager).
    """
    frac = kelly_binary(p, price) * kelly_mult
    usd = min(frac * bankroll, max_stake, headroom, bankroll)
    if usd <= 0 or price <= 0:
        return SizedOrder(0.0, 0.0)
    shares = usd / price
    if shares < MIN_ORDER_SHARES or usd < MIN_ORDER_NOTIONAL:
        return SizedOrder(0.0, 0.0)
    return SizedOrder(shares=shares, stake_usd=usd)
