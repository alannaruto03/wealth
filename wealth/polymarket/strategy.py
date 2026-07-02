"""HybridMaker — the decision core, as a pure function of market state.

What the consistently profitable 15-minute accounts do, distilled:

1. QUOTE  — rest bids on BOTH outcome tokens around a model fair value
            (bid UP near p - h, bid DOWN near (1-p) - h). Both legs filling
            means owning a complete set bought below $1 -> locked profit at
            settlement. Makers pay no fee and earn rebates.
2. PULL   — on a Binance impulse, cancel everything first, requote after the
            model re-converges. Stale quotes against faster flow are how
            makers bleed (adverse selection).
3. PAIR   — if ask(UP) + ask(DOWN) < 1 - margin, buy both at market:
            risk-free at settlement (classic complete-set capture).
4. TAKE   — cross the spread only when model edge clearly beats the
            p*(1-p) taker fee curve plus a margin. Post-Jan-2026 this
            survives mostly on deep favorites near expiry where the fee -> 0.
5. FADE   — no new maker quotes in the final seconds; unload inventory asks
            when holding size.

Pure: state in, desired orders out. The runner diffs desired vs open orders
and the RiskManager caps sizes. No network, no clocks — fully unit-testable.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional

from wealth.polymarket.config import PolyBotConfig
from wealth.polymarket.fees import taker_fee_per_share
from wealth.polymarket.feeds import BookTop
from wealth.polymarket.model import fair_up_probability


@dataclass
class MarketState:
    now: float
    window_end: float
    price_to_beat: float
    spot: Optional[float]
    sigma_s: float
    up: BookTop
    down: BookTop
    tick: float = 0.01
    inv_up: float = 0.0
    inv_down: float = 0.0
    impulse_bps: float = 0.0
    feed_stale: bool = False

    @property
    def tau_s(self) -> float:
        return max(0.0, self.window_end - self.now)


@dataclass
class DesiredOrder:
    token: str          # "UP" | "DOWN"
    side: str           # "buy" | "sell"
    price: float
    size: float
    kind: str           # "maker" | "taker" | "pair"


@dataclass
class Decision:
    pull_all: bool = False
    orders: List[DesiredOrder] = field(default_factory=list)
    fair_up: Optional[float] = None
    reason: str = ""


def round_to_tick(price: float, tick: float, up: bool) -> float:
    n = price / tick
    n = math.ceil(n - 1e-9) if up else math.floor(n + 1e-9)
    return round(n * tick, 6)


class HybridMaker:
    def __init__(self, cfg: PolyBotConfig):
        self.cfg = cfg

    def decide(self, s: MarketState) -> Decision:
        cfg = self.cfg
        if s.spot is None or s.feed_stale:
            return Decision(pull_all=True, reason="no fresh spot")

        p = fair_up_probability(s.spot, s.price_to_beat, s.sigma_s, s.tau_s)
        d = Decision(fair_up=p)

        # 2. PULL: spot is moving too fast for our quotes to be trustworthy.
        if s.impulse_bps >= cfg.impulse_bps:
            d.pull_all = True
            d.reason = f"impulse {s.impulse_bps:.1f}bps"
            return d

        # 3. PAIR: complete set below $1 (risk-free at settlement).
        if s.up.ask is not None and s.down.ask is not None:
            pair_cost = s.up.ask + s.down.ask
            if pair_cost < 1.0 - cfg.complete_set_margin:
                size = min(s.up.ask_size, s.down.ask_size, cfg.complete_set_max_shares)
                if size > 0:
                    d.orders.append(DesiredOrder("UP", "buy", s.up.ask, size, "pair"))
                    d.orders.append(DesiredOrder("DOWN", "buy", s.down.ask, size, "pair"))
                    d.reason = f"complete set @ {pair_cost:.3f}"

        # 4. TAKE: only when edge > fee + margin (fee -> 0 near 0/1).
        if s.tau_s > cfg.taker_final_cutoff_s:
            for token, book, fair in (("UP", s.up, p), ("DOWN", s.down, 1.0 - p)):
                ask = book.ask
                if ask is None or ask > cfg.taker_max_price:
                    continue
                edge = fair - ask
                if edge > taker_fee_per_share(ask, cfg.taker_fee_rate) + cfg.taker_edge_margin:
                    size = min(cfg.quote_size, book.ask_size)
                    if size > 0:
                        d.orders.append(DesiredOrder(token, "buy", ask, size, "taker"))

        # 1./5. QUOTE: two-sided bids around fair value, none in the endgame.
        if s.tau_s > cfg.no_quote_final_s:
            half = max(cfg.min_half_spread,
                       cfg.vol_half_spread_mult * s.sigma_s * math.sqrt(s.tau_s))
            net = s.inv_up - s.inv_down
            skew = -cfg.inventory_skew * max(-1.0, min(1.0, net / cfg.max_inventory))
            center = min(max(p + skew, 0.02), 0.98)

            for token, fair, book, inv in (
                ("UP", center, s.up, s.inv_up),
                ("DOWN", 1.0 - center, s.down, s.inv_down),
            ):
                bid = round_to_tick(fair - half, s.tick, up=False)
                # stay maker: never price through the current ask
                if book.ask is not None:
                    bid = min(bid, round_to_tick(book.ask - s.tick, s.tick, up=False))
                if s.tick <= bid <= 1.0 - s.tick:
                    d.orders.append(DesiredOrder(token, "buy", bid, cfg.quote_size, "maker"))

                # unload inventory at fair + half (also maker, fee-free)
                if inv > 0:
                    ask_px = round_to_tick(fair + half, s.tick, up=True)
                    if book.bid is not None:
                        ask_px = max(ask_px, round_to_tick(book.bid + s.tick, s.tick, up=True))
                    if s.tick <= ask_px <= 1.0 - s.tick:
                        d.orders.append(DesiredOrder(token, "sell", ask_px, inv, "maker"))
        else:
            d.pull_all = True
            d.reason = d.reason or "endgame: no maker quotes"

        return d
