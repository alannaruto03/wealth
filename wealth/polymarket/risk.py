"""Hard risk limits — evaluated before any order reaches the executor.

Limits are conservative and absolute: per-side inventory caps, a per-window
notional budget, a daily-loss kill switch, and a stale-feed circuit breaker.
The strategy asks; risk answers with a (possibly zero) allowed size.
"""
from __future__ import annotations

from dataclasses import dataclass

from wealth.polymarket.config import PolyBotConfig

MIN_RESTING_SHARES = 5.0   # Polymarket minimum resting order size
MIN_TAKER_NOTIONAL = 1.0   # Polymarket minimum marketable order ($)


@dataclass
class RiskState:
    inv_up: float = 0.0
    inv_down: float = 0.0
    window_spent: float = 0.0        # $ spent on buys this window
    open_buy_notional: float = 0.0   # $ locked in resting buys
    daily_pnl: float = 0.0
    feed_stale: bool = False


class RiskManager:
    def __init__(self, cfg: PolyBotConfig):
        self.cfg = cfg

    def halted(self, rs: RiskState) -> str:
        """Non-empty reason string when trading must stop."""
        if rs.daily_pnl <= -abs(self.cfg.daily_loss_limit):
            return f"daily loss limit hit ({rs.daily_pnl:.2f})"
        if rs.feed_stale:
            return "spot feed stale"
        return ""

    def allowed_buy(self, rs: RiskState, token: str, price: float,
                    size: float, resting: bool) -> float:
        """Cap ``size`` so no hard limit can be exceeded. 0 = don't send."""
        if price <= 0 or size <= 0:
            return 0.0
        inv = rs.inv_up if token == "UP" else rs.inv_down
        size = min(size, max(0.0, self.cfg.max_inventory - inv))
        budget = self.cfg.max_window_notional - rs.window_spent - rs.open_buy_notional
        size = min(size, max(0.0, budget) / price)
        if resting and size < MIN_RESTING_SHARES:
            return 0.0
        if not resting and price * size < MIN_TAKER_NOTIONAL:
            return 0.0
        return size

    def allowed_sell(self, rs: RiskState, token: str, size: float) -> float:
        """Sells only unload real inventory (no shorting outcome tokens)."""
        inv = rs.inv_up if token == "UP" else rs.inv_down
        size = min(size, max(0.0, inv))
        return size if size >= MIN_RESTING_SHARES else 0.0
