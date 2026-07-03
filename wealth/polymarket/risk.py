"""RiskManager: hard limits between the signal and the executor.

Sizing is Kelly-based, but Kelly trusts the model; these caps don't. Exposure
is measured at cost basis. The daily kill switch compares live equity to the
day-start anchor (persisted in executor meta so restarts can't reset it) and,
once tripped, blocks entries for the rest of the UTC day — exits stay allowed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Optional

from wealth.polymarket.config import PolymarketConfig
from wealth.polymarket.executor import TokenPosition
from wealth.polymarket.sizing import SizedOrder, stake
from wealth.polymarket.strategy import TradeIntent


@dataclass
class RiskDecision:
    approved: bool
    shares: float = 0.0
    stake_usd: float = 0.0
    reason: str = ""


def _utc_day(now: float) -> str:
    return datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y-%m-%d")


class RiskManager:
    def __init__(self, cfg: PolymarketConfig, meta: Dict):
        """``meta`` is the executor's persisted metadata dict (mutated in place)."""
        self.cfg = cfg
        self.meta = meta

    # -- daily kill switch -----------------------------------------------------
    def roll_day_anchor(self, now: float, equity: float) -> None:
        anchor = self.meta.get("day_anchor")
        day = _utc_day(now)
        if not anchor or anchor.get("date") != day:
            self.meta["day_anchor"] = {"date": day, "equity": equity}
            self.meta.pop("kill_switch", None)

    def kill_switch_tripped(self, now: float, equity: float) -> bool:
        if self.meta.get("kill_switch") == _utc_day(now):
            return True
        anchor = self.meta.get("day_anchor")
        if not anchor or anchor.get("date") != _utc_day(now):
            return False
        start = float(anchor["equity"])
        if start > 0 and equity <= start * (1.0 - self.cfg.daily_loss_limit):
            self.meta["kill_switch"] = _utc_day(now)
            return True
        return False

    # -- exposure ---------------------------------------------------------------
    @staticmethod
    def _cost_basis(positions: Dict[str, TokenPosition]) -> float:
        return sum(p.size * p.avg_price for p in positions.values())

    def _market_cost_basis(self, positions: Dict[str, TokenPosition], slug: str) -> float:
        return sum(p.size * p.avg_price for p in positions.values() if p.market_slug == slug)

    def check(
        self,
        intent: TradeIntent,
        positions: Dict[str, TokenPosition],
        cash: float,
        equity: float,
        now: float,
    ) -> RiskDecision:
        if intent.side == "sell":
            # Exits reduce risk; always allowed (size already set by the signal).
            return RiskDecision(True, shares=intent.size or 0.0, reason="exit")

        if self.kill_switch_tripped(now, equity):
            return RiskDecision(False, reason="kill_switch")

        total = self._cost_basis(positions)
        in_market = self._market_cost_basis(positions, intent.market.slug)
        headroom = min(
            self.cfg.max_total_exposure - total,
            self.cfg.max_market_exposure - in_market,
            cash,
        )
        if headroom <= 0:
            return RiskDecision(False, reason="exposure_cap")

        sized: SizedOrder = stake(
            p=intent.fair,
            price=intent.limit_price,
            bankroll=equity,
            kelly_mult=self.cfg.kelly_fraction,
            max_stake=self.cfg.max_stake_per_trade,
            headroom=headroom,
        )
        if sized.shares <= 0:
            return RiskDecision(False, reason="size_below_min")
        return RiskDecision(True, shares=sized.shares, stake_usd=sized.stake_usd, reason="sized")
