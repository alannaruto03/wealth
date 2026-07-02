"""Fair value for an Up-or-Down window: a digital option on the spot price.

UP pays $1 when the settlement price at window end is >= the window's opening
"price to beat" K. Under a driftless lognormal over the remaining tau seconds:

    p_up = Phi( ln(S / K) / (sigma * sqrt(tau)) )

with sigma the short-horizon realized vol of 1-second log returns
(per-sqrt-second units), estimated by EWMA so it adapts within a window.
"""
from __future__ import annotations

import math
from typing import Optional


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def fair_up_probability(
    spot: float, strike: float, sigma_s: float, tau_s: float
) -> float:
    """P(settle >= strike) for a driftless lognormal; ties resolve UP."""
    if tau_s <= 0:
        return 1.0 if spot >= strike else 0.0
    if spot <= 0 or strike <= 0:
        return 0.5
    if sigma_s <= 0:
        return 1.0 if spot >= strike else 0.0
    d = math.log(spot / strike) / (sigma_s * math.sqrt(tau_s))
    return norm_cdf(d)


class VolEstimator:
    """EWMA realized vol of log returns, normalized per sqrt-second.

    Feed irregularly-spaced (ts, price) samples; the squared return of each
    interval is scaled by its duration so the estimate is per-second variance
    regardless of sampling cadence.
    """

    def __init__(self, halflife_s: float = 90.0, seed: float = 8e-5,
                 floor: float = 1e-5):
        self.halflife_s = halflife_s
        self.floor = floor
        self._var_s = seed * seed          # per-second variance
        self._last_ts: Optional[float] = None
        self._last_price: Optional[float] = None
        self.n_samples = 0

    def update(self, ts: float, price: float) -> None:
        if price <= 0:
            return
        if self._last_ts is None or self._last_price is None:
            self._last_ts, self._last_price = ts, price
            return
        dt = ts - self._last_ts
        if dt <= 0:
            return
        r = math.log(price / self._last_price)
        var_per_s = (r * r) / dt
        alpha = 1.0 - 0.5 ** (dt / self.halflife_s)
        self._var_s += alpha * (var_per_s - self._var_s)
        self._last_ts, self._last_price = ts, price
        self.n_samples += 1

    @property
    def sigma_s(self) -> float:
        """Vol per sqrt-second (multiply by sqrt(seconds) for a horizon)."""
        return max(math.sqrt(self._var_s), self.floor)
