"""PolymarketRunner: the live loop tying spot, fair value, books, and fills.

Each tick: refresh BTC spot + vol, roll to newly opened markets, settle expired
ones, then per market compare fair probability against both books and hand any
intents through the risk manager to the executor. Every tick journals a record
in the same shape the portfolio bot writes, so Journal.equity_curve(), the
metrics module, and the dashboard read this bot's track record unchanged.

Everything (clients, feeds, clock, sleep) is injectable for offline tests and
replay.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

from wealth.live.journal import Journal
from wealth.polymarket.clob import ClobReadClient, OrderBook
from wealth.polymarket.config import PolymarketConfig
from wealth.polymarket.executor import Fill, PolymarketExecutor
from wealth.polymarket.fair_value import FairValueModel, VolEstimator, get_model
from wealth.polymarket.gamma import GammaClient, MarketInfo
from wealth.polymarket.risk import RiskManager
from wealth.polymarket.strategy import EdgeSignal

DISCOVERY_INTERVAL_S = 30.0
VOL_REFRESH_S = 60.0


class SpotFeed:
    """Latest BTC spot + realized vol from a ccxt exchange, with staleness tracking."""

    def __init__(self, exchange: str = "binance", symbol: str = "BTC/USDT",
                 vol: Optional[VolEstimator] = None,
                 clock: Callable[[], float] = time.time):
        import ccxt  # local import keeps module import cheap for tests/replay

        self.exchange = getattr(ccxt, exchange)({"enableRateLimit": True})
        self.symbol = symbol
        self.vol = vol or VolEstimator()
        self.clock = clock
        self.spot: Optional[float] = None
        self.spot_ts: float = 0.0
        self._vol_ts: float = 0.0

    def refresh(self) -> None:
        import pandas as pd

        now = self.clock()
        ticker = self.exchange.fetch_ticker(self.symbol)
        last = ticker.get("last") or ticker.get("close")
        if last:
            self.spot = float(last)
            self.spot_ts = now
        if now - self._vol_ts >= VOL_REFRESH_S:
            ohlcv = self.exchange.fetch_ohlcv(self.symbol, timeframe="1m",
                                              limit=self.vol.lookback_min)
            closes = pd.Series([row[4] for row in ohlcv], dtype=float)
            self.vol.update(closes)
            self._vol_ts = now

    @property
    def sigma(self) -> Optional[float]:
        return self.vol.sigma_per_sqrt_s

    def age_s(self, now: Optional[float] = None) -> float:
        return (now if now is not None else self.clock()) - self.spot_ts


class PolymarketRunner:
    def __init__(
        self,
        cfg: PolymarketConfig,
        gamma: GammaClient,
        clob: ClobReadClient,
        spot_feed,
        executor: PolymarketExecutor,
        journal: Optional[Journal] = None,
        model: Optional[FairValueModel] = None,
        signal: Optional[EdgeSignal] = None,
        risk: Optional[RiskManager] = None,
        clock: Callable[[], float] = time.time,
        recorder=None,
        trade: bool = True,
        publisher=None,
    ):
        self.cfg = cfg
        self.gamma = gamma
        self.clob = clob
        self.spot_feed = spot_feed
        self.executor = executor
        self.journal = journal
        self.model = model or get_model(cfg.fair_model, **cfg.fair_params)
        self.signal = signal or EdgeSignal(cfg)
        self.risk = risk or RiskManager(cfg, getattr(executor, "meta", {}))
        self.clock = clock
        self.recorder = recorder
        self.trade = trade  # False = observe/record only, never execute
        self.publisher = publisher
        self.active: Dict[str, MarketInfo] = {}
        self._last_discovery = 0.0
        self._last_publish = 0.0
        self._last_publish_error = 0.0

    # -- journal helpers -------------------------------------------------------
    def _journal(self, record: Dict) -> None:
        if self.journal:
            self.journal.append(record)

    def _now_iso(self, now: float) -> str:
        return datetime.fromtimestamp(now, tz=timezone.utc).isoformat()

    # -- market lifecycle -------------------------------------------------------
    def _meta_markets(self) -> Dict:
        return self.executor.meta.setdefault("markets", {})

    def _discover(self, now: float, spot: float) -> None:
        if now - self._last_discovery < DISCOVERY_INTERVAL_S and self.active:
            return
        self._last_discovery = now
        found = self.gamma.discover(self.cfg.series, now)
        meta = self._meta_markets()
        for m in found:
            if m.slug in self.active:
                continue
            self.active[m.slug] = m
            if m.slug not in meta:
                # Prefer Polymarket's own period-open when it publishes one;
                # our spot is a fallback and can differ from the resolution
                # source by a few dollars.
                meta[m.slug] = {
                    "period_open": m.price_to_beat if m.price_to_beat else spot,
                    "period_open_source": "gamma" if m.price_to_beat else "spot",
                    "spot_at_first_sight": spot,
                    "start_ts": m.start_ts,
                    "end_ts": m.end_ts,
                    "token_up": m.token_id_up,
                    "token_down": m.token_id_down,
                    "series": m.series,
                }
                if hasattr(self.executor, "save_meta"):
                    self.executor.save_meta()
                self._journal({
                    "event": "market_open", "timestamp": self._now_iso(now),
                    "slug": m.slug, "series": m.series, "question": m.question,
                    "period_open": meta[m.slug]["period_open"],
                    "period_open_source": meta[m.slug]["period_open_source"],
                    "end_ts": m.end_ts,
                })

    def _settle_expired(self, now: float, spot: float) -> None:
        """Settle every tracked market past expiry — including ones only found
        in persisted meta (positions held across a restart)."""
        meta = self._meta_markets()
        for slug in list(meta):
            info = meta[slug]
            if info["end_ts"] > now:
                continue
            period_open = float(info["period_open"])
            up_wins = spot > period_open
            winner = info["token_up"] if up_wins else info["token_down"]
            pnl = self.executor.settle(self._market_from_meta(slug, info), winner)
            self._journal({
                "event": "resolution", "timestamp": self._now_iso(now),
                "slug": slug, "outcome": "up" if up_wins else "down",
                "spot_at_close": spot, "period_open": period_open, "pnl": pnl,
            })
            meta.pop(slug, None)
            self.active.pop(slug, None)
        if hasattr(self.executor, "save_meta"):
            self.executor.save_meta()

    @staticmethod
    def _market_from_meta(slug: str, info: Dict) -> MarketInfo:
        return MarketInfo(
            slug=slug, condition_id="", question="",
            token_id_up=info["token_up"], token_id_down=info["token_down"],
            start_ts=info.get("start_ts", 0.0), end_ts=info["end_ts"],
            series=info.get("series", ""),
        )

    # -- the tick ----------------------------------------------------------------
    def tick(self) -> Dict:
        now = self.clock()
        self.spot_feed.refresh()
        spot = self.spot_feed.spot
        sigma = self.spot_feed.sigma

        if spot is None or self.spot_feed.age_s(now) > self.cfg.stale_spot_max_s:
            record = {"event": "tick", "timestamp": self._now_iso(now),
                      "bar_ts": str(int(now)), "status": "stale_spot",
                      "cash": self.executor.get_cash(),
                      "equity": self.executor.equity({})}
            self._journal(record)
            return record

        self._settle_expired(now, spot)
        self._discover(now, spot)

        meta = self._meta_markets()
        marks: Dict[str, float] = {}
        prices: Dict[str, float] = {}
        order_rows: List[Dict] = []
        fair_by_market: Dict[str, float] = {}
        guard_reasons: List[str] = []

        equity_pre = self.executor.equity({})
        self.risk.roll_day_anchor(now, equity_pre)

        for slug, market in list(self.active.items()):
            info = meta.get(slug)
            if info is None:
                continue
            book_up = self._safe_book(market.token_id_up)
            book_down = self._safe_book(market.token_id_down)
            for label, book in (("UP", book_up), ("DOWN", book_down)):
                token = market.token_id_up if label == "UP" else market.token_id_down
                if book and book.mid is not None:
                    prices[f"{slug}:{label}"] = book.mid
                if book and book.best_bid is not None:
                    marks[token] = book.best_bid

            t_remaining = market.seconds_to_expiry(now)
            fair_up = self.model.prob_up(spot, float(info["period_open"]),
                                         t_remaining, sigma or 0.0)
            fair_by_market[slug] = fair_up

            if self.recorder is not None:
                self.recorder.snapshot(now=now, market=market, info=info, spot=spot,
                                       sigma=sigma, book_up=book_up, book_down=book_down)

            intents, guards = self.signal.intents(
                market, fair_up, book_up, book_down,
                self.executor.get_positions(), now,
            )
            guard_reasons.extend(f"{g.market_slug}:{g.reason}" for g in guards)

            if not self.trade:
                continue
            for intent in intents:
                equity_now = self.executor.equity(marks)
                decision = self.risk.check(intent, self.executor.get_positions(),
                                           self.executor.get_cash(), equity_now, now)
                if not decision.approved:
                    self._journal({
                        "event": "risk_block", "timestamp": self._now_iso(now),
                        "slug": slug, "side": intent.side, "token_id": intent.token_id,
                        "reason": decision.reason, "edge": intent.edge,
                    })
                    continue
                book = book_up if intent.token_id == market.token_id_up else book_down
                if intent.side == "buy":
                    fill = self.executor.buy(market, intent.token_id,
                                             intent.limit_price, decision.shares, book)
                else:
                    fill = self.executor.sell(market, intent.token_id,
                                              intent.limit_price, decision.shares, book)
                if fill.size > 0:
                    label = "UP" if intent.token_id == market.token_id_up else "DOWN"
                    order_rows.append({
                        "symbol": f"{slug}:{label}", "side": fill.side,
                        "quantity": fill.size, "price": fill.avg_price,
                        "edge": intent.edge, "fair": intent.fair, "reason": intent.reason,
                    })

        equity = self.executor.equity(marks)
        record = {
            "event": "tick",
            "timestamp": self._now_iso(now),
            "bar_ts": str(int(now)),
            "status": ("traded" if order_rows else "hold") if self.trade else "recording",
            "spot": spot,
            "sigma": sigma,
            "fair": fair_by_market,
            "guards": guard_reasons,
            "prices": prices,
            "orders": order_rows,
            "cash": self.executor.get_cash(),
            "equity": equity,
        }
        self._journal(record)
        self._maybe_publish(now)
        return record

    def _maybe_publish(self, now: float) -> None:
        """Push a live snapshot; failures never interrupt trading."""
        if self.publisher is None or self.journal is None:
            return
        if now - self._last_publish < self.cfg.publish_every_s:
            return
        self._last_publish = now
        try:
            from wealth.polymarket.publisher import build_snapshot

            state = {
                "cash": self.executor.get_cash(),
                "positions": {
                    t: {"market_slug": p.market_slug, "size": p.size,
                        "avg_price": p.avg_price}
                    for t, p in self.executor.get_positions().items()
                },
                "meta": self.executor.meta,
            }
            snapshot = build_snapshot(self.journal.records(), state, self.cfg)
            gist_id = self.publisher.publish(snapshot)
            meta_id = self.executor.meta.get("publish_gist_id")
            if meta_id != gist_id:
                self.executor.meta["publish_gist_id"] = gist_id
                if hasattr(self.executor, "save_meta"):
                    self.executor.save_meta()
                print(f"live view feed created: gist {gist_id} — open your "
                      f"static page with ?gist={gist_id}")
        except Exception as exc:  # noqa: BLE001
            if now - self._last_publish_error > 300:  # don't spam the journal
                self._last_publish_error = now
                self._journal({
                    "event": "publish_error", "timestamp": self._now_iso(now),
                    "error": f"{type(exc).__name__}: {exc}",
                })

    def _safe_book(self, token_id: str) -> Optional[OrderBook]:
        try:
            return self.clob.get_book(token_id)
        except Exception:
            return None

    # -- loop ---------------------------------------------------------------------
    def run_forever(self, max_ticks: Optional[int] = None,
                    sleep_fn: Callable[[float], None] = time.sleep) -> None:
        ticks = 0
        backoff = 1.0
        while max_ticks is None or ticks < max_ticks:
            try:
                self.tick()
                backoff = 1.0
            except KeyboardInterrupt:
                raise
            except Exception as exc:  # network blips must not kill the loop
                self._journal({"event": "error", "timestamp": self._now_iso(self.clock()),
                               "error": f"{type(exc).__name__}: {exc}"})
                sleep_fn(min(backoff, 60.0))
                backoff *= 2.0
            ticks += 1
            sleep_fn(self.cfg.interval_seconds)
