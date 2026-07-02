"""Per-window orchestrator: discover -> quote -> settle -> roll.

Only trades windows it observes from the very start, because the "price to
beat" (the window's opening price) is captured live from the feed at the
grid boundary — joining mid-window would mean guessing the strike.

The loop is event-paced by the feeds' cadence (WS pushes or ~1s REST polls);
each pass re-derives the desired order set from the strategy and reconciles
it against open orders (cancel/replace — the CLOB has no order modify).
"""
from __future__ import annotations

import asyncio
import time
from typing import Dict, Optional

from wealth.live.journal import Journal
from wealth.polymarket.config import PolyBotConfig
from wealth.polymarket.discovery import (
    GammaClient, UpDownMarket, next_window_start,
)
from wealth.polymarket.execution import Executor
from wealth.polymarket.feeds import FeedManager, MarketData
from wealth.polymarket.model import VolEstimator
from wealth.polymarket.paper import PaperExecutor
from wealth.polymarket.risk import RiskManager, RiskState
from wealth.polymarket.strategy import Decision, HybridMaker, MarketState


class PolyRunner:
    def __init__(self, cfg: PolyBotConfig, journal: Journal,
                 executor: Optional[Executor] = None, verbose: bool = True):
        self.cfg = cfg
        self.journal = journal
        self.executor = executor or PaperExecutor(cfg.cash, cfg.taker_fee_rate)
        self.strategy = HybridMaker(cfg)
        self.risk = RiskManager(cfg)
        self.gamma = GammaClient()
        self.data = MarketData(asset=cfg.asset)
        self.feeds = FeedManager(self.data, cfg.poll_interval_s, cfg.prefer_websocket)
        self.vol = VolEstimator(cfg.vol_halflife_s, cfg.vol_seed, cfg.vol_floor)
        self.daily_pnl = 0.0
        self.verbose = verbose
        self._vol_last_ts = 0.0
        self._basis = 0.0            # EWMA of (chainlink - binance spot)
        self._basis_ts = 0.0
        self._last_take: Dict[str, float] = {}  # token -> book ts last taken

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(f"[poly] {msg}", flush=True)

    # ------------------------------------------------------------------ main --
    async def run(self, windows: Optional[int] = None,
                  duration_s: Optional[float] = None) -> None:
        started = time.time()
        await self.feeds.start([])
        misses = 0
        try:
            while True:
                if windows is not None and windows <= 0:
                    break
                if duration_s is not None and time.time() - started > duration_s:
                    break
                if self.daily_pnl <= -abs(self.cfg.daily_loss_limit):
                    self._log("daily loss limit hit — stopping")
                    self.journal.append({"event": "halt", "timestamp": _iso(),
                                         "reason": "daily_loss_limit"})
                    break

                market = await self._next_market()
                if market is None:
                    misses += 1
                    if misses >= 3:
                        raise RuntimeError(
                            "could not discover 3 consecutive windows via the "
                            "Gamma API — check connectivity and the slug format")
                    continue
                misses = 0
                await self._trade_window(market)
                if windows is not None:
                    windows -= 1
        finally:
            self.executor.cancel_all(time.time())
            await self.feeds.stop()
            self.gamma.close()

    # ------------------------------------------------------- window lifecycle --
    async def _next_market(self) -> Optional[UpDownMarket]:
        h = self.cfg.horizon_seconds
        start_ts = next_window_start(horizon_s=h)
        self._log(f"waiting for window starting at {start_ts} "
                  f"({start_ts - int(time.time())}s away)")
        # sit out the current window; warm up vol/basis, don't hammer Gamma yet
        while time.time() < start_ts - 120:
            self._feed_vol()
            await asyncio.sleep(0.5)
        market = await asyncio.to_thread(
            self.gamma.wait_for_window, self.cfg.asset, self.cfg.horizon,
            start_ts, h, start_ts + min(60, h // 3))
        if market is None:
            self._log(f"window {start_ts}: market not listed on Gamma — skipping")
            return None
        await self.feeds.set_tokens([market.token_up, market.token_down])
        while time.time() < market.start_ts:
            self._feed_vol()
            await asyncio.sleep(min(0.5, max(0.05, market.start_ts - time.time())))
        if time.time() > market.start_ts + 2.0:
            # joined after the open: the strike would be a guess — skip
            self._log(f"window {market.slug}: joined late, skipping")
            return None
        return market

    async def _trade_window(self, market: UpDownMarket) -> None:
        cfg = self.cfg
        now = time.time()
        strike = (self.data.chainlink
                  if self.data.chainlink and now - self.data.chainlink_ts < 5.0
                  else self.data.spot)
        if not strike:
            self._log("no price at window open — skipping window")
            return
        cash_before = self.executor.cash()
        rs = RiskState(daily_pnl=self.daily_pnl)
        self._log(f"window {market.slug}: strike={strike:.2f} cash={cash_before:.2f}")
        self.journal.append({
            "event": "window_start", "timestamp": _iso(), "slug": market.slug,
            "start_ts": market.start_ts, "end_ts": market.end_ts,
            "price_to_beat": strike, "cash": cash_before,
        })

        fill_counts: Dict[str, int] = {"maker": 0, "taker": 0, "pair": 0}
        while time.time() < market.end_ts:
            self._feed_vol()
            self._paper_mark_books(market)
            state = self._state(market, strike, rs)
            decision = self.strategy.decide(state)
            self._reconcile(decision, state, rs)
            self._drain_fills(rs, fill_counts, market.slug)
            await asyncio.sleep(min(cfg.poll_interval_s,
                                    max(0.05, market.end_ts - time.time())))

        # endgame: everything off before resolution
        self.executor.cancel_all(time.time())
        outcome = await self._resolve(market, strike)
        payout = self.executor.settle_window(outcome, time.time())
        cash_after = self.executor.cash()
        pnl = cash_after - cash_before
        self.daily_pnl += pnl
        fees = getattr(self.executor, "fees_paid", 0.0)
        self._log(f"window {market.slug}: outcome={outcome} payout={payout:.2f} "
                  f"pnl={pnl:+.2f} cash={cash_after:.2f}")
        self.journal.append({
            "event": "window_settle", "timestamp": _iso(), "slug": market.slug,
            "outcome": outcome, "payout": payout, "pnl": pnl,
            "cash": cash_after, "fees_paid_total": fees,
            "fills": dict(fill_counts),
        })
        # equity snapshot in the shape wealth.live.journal expects
        self.journal.append({"event": "tick", "timestamp": _iso(),
                             "bar_ts": market.end_ts, "equity": cash_after})

    # --------------------------------------------------------------- helpers --
    def _feed_vol(self) -> None:
        for ts, px in self.data.ticks:
            if ts > self._vol_last_ts:
                self.vol.update(ts, px)
                self._vol_last_ts = ts
        # settlement uses Chainlink; track its basis vs the (faster) spot feed
        d = self.data
        if (d.chainlink and d.spot and d.chainlink_ts > self._basis_ts
                and abs(d.chainlink_ts - d.spot_ts) < 3.0):
            self._basis += 0.05 * ((d.chainlink - d.spot) - self._basis)
            self._basis_ts = d.chainlink_ts

    def _paper_mark_books(self, market: UpDownMarket) -> None:
        if isinstance(self.executor, PaperExecutor):
            self.executor.on_book("UP", self.data.book(market.token_up))
            self.executor.on_book("DOWN", self.data.book(market.token_down))

    def _state(self, market: UpDownMarket, strike: float, rs: RiskState) -> MarketState:
        now = time.time()
        rs.inv_up = self.executor.position("UP")
        rs.inv_down = self.executor.position("DOWN")
        rs.open_buy_notional = sum(
            o.price * o.remaining for o in self.executor.open_orders()
            if o.side == "buy")
        rs.feed_stale = self.data.spot_stale(self.cfg.feed_stale_s, now)
        spot = self.data.spot
        if spot is not None:
            spot += self._basis      # settlement-feed-adjusted spot
        return MarketState(
            now=now, window_end=market.end_ts, price_to_beat=strike,
            spot=spot, sigma_s=self.vol.sigma_s,
            up=self.data.book(market.token_up),
            down=self.data.book(market.token_down),
            tick=market.tick, inv_up=rs.inv_up, inv_down=rs.inv_down,
            impulse_bps=self.data.recent_move_bps(self.cfg.impulse_window_s, now),
            feed_stale=rs.feed_stale,
        )

    def _reconcile(self, d: Decision, s: MarketState, rs: RiskState) -> None:
        now = s.now
        halt_reason = self.risk.halted(rs)
        if halt_reason or d.pull_all:
            self.executor.cancel_all(now)
            if halt_reason:
                return
        # immediate orders first (taker/pair), risk-capped. At most one take
        # per token per book snapshot: paper fills don't consume the real
        # book, so re-taking the same displayed liquidity would be fantasy.
        for o in d.orders:
            if o.kind not in ("taker", "pair"):
                continue
            book_ts = (s.up if o.token == "UP" else s.down).ts
            if book_ts <= self._last_take.get(o.token, -1.0):
                continue
            size = self.risk.allowed_buy(rs, o.token, o.price, o.size, resting=False)
            if size > 0:
                placed = self.executor.place(o.token, o.side, o.price, size, o.kind, now)
                if placed is not None:
                    rs.window_spent += o.price * size
                    self._last_take[o.token] = book_ts

        # maker quotes: cancel/replace toward the desired set
        desired = {(o.token, o.side): o for o in d.orders if o.kind == "maker"}
        if not d.pull_all:
            tol = self.cfg.requote_ticks * s.tick
            for open_o in self.executor.open_orders():
                key = (open_o.token, open_o.side)
                want = desired.get(key)
                if want is None or abs(want.price - open_o.price) >= tol - 1e-9:
                    self.executor.cancel(open_o.order_id, now)
                else:
                    desired.pop(key)  # keep the resting order as-is
        for o in desired.values():
            if d.pull_all:
                break
            if o.side == "buy":
                size = self.risk.allowed_buy(rs, o.token, o.price, o.size, resting=True)
            else:
                size = self.risk.allowed_sell(rs, o.token, o.size)
            if size > 0:
                self.executor.place(o.token, o.side, o.price, size, "maker", now)

    def _drain_fills(self, rs: RiskState, counts: Dict[str, int], slug: str) -> None:
        for f in self.executor.drain_fills():
            counts[f.kind] = counts.get(f.kind, 0) + 1
            if f.side == "buy" and f.kind == "maker":
                rs.window_spent += f.price * f.size
            self._log(f"fill: {f.side} {f.size:.1f} {f.token} @ {f.price:.3f} "
                      f"({f.kind}, fee={f.fee:.4f})")
            self.journal.append({
                "event": "fill", "timestamp": _iso(), "slug": slug,
                "token": f.token, "side": f.side, "price": f.price,
                "size": f.size, "fee": f.fee, "kind": f.kind,
            })

    async def _resolve(self, market: UpDownMarket, strike: float) -> str:
        """Gamma's resolved outcome is truth; Chainlink/spot compare is fallback."""
        deadline = time.time() + self.cfg.resolution_timeout_s
        while time.time() < deadline:
            refreshed = await asyncio.to_thread(self.gamma.refresh, market)
            if refreshed.outcome:
                return refreshed.outcome
            await asyncio.sleep(3.0)
        settle_px = (self.data.chainlink
                     if self.data.chainlink and
                     abs(self.data.chainlink_ts - market.end_ts) < 10.0
                     else self.data.spot)
        self._log("resolution timeout — falling back to price comparison")
        return "UP" if settle_px is not None and settle_px >= strike else "DOWN"


def _iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
