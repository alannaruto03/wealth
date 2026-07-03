"""Paper value bot: buy underpriced favorites, hold to resolution.

Slow by design — a scan every ~30 minutes, positions held for days. The edge
is statistical (favorite-longshot bias), so the risk controls are the
strategy: fractional Kelly, hard caps on per-market stake / total exposure /
position count, and a haircut baked into the edge estimate. A 95c favorite
that loses wipes ~20 winners; sizing is what survives that.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Dict, Optional

from wealth.live.journal import Journal
from wealth.polymarket.config import ValueBotConfig
from wealth.polymarket.scanner import GammaScanner, rank, resolved_winner


@dataclass
class ValuePosition:
    slug: str
    question: str
    token_id: str
    outcome_index: int
    outcome_label: str
    entry_price: float
    shares: float
    stake: float
    p_true: float
    edge: float
    entry_ts: str
    end_date: str = ""


def kelly_stake(equity: float, p_true: float, ask: float,
                cfg: ValueBotConfig) -> float:
    """Capped fractional Kelly for a binary buy at ``ask``.

    Full Kelly for win-prob p at price q is (p - q) / (1 - q); we take a
    configured fraction of that and clamp to the per-market cap.
    """
    if ask >= 1.0 or ask <= 0.0:
        return 0.0
    f_star = (p_true - ask) / (1.0 - ask)
    if f_star <= 0:
        return 0.0
    stake = cfg.kelly_fraction * f_star * equity
    stake = min(stake, cfg.max_stake_per_market)
    return stake if stake >= cfg.min_stake else 0.0


class ValueBot:
    def __init__(self, cfg: ValueBotConfig, journal: Journal,
                 scanner: Optional[GammaScanner] = None, verbose: bool = True):
        self.cfg = cfg
        self.journal = journal
        self.scanner = scanner or GammaScanner(cfg)
        self.verbose = verbose
        self.cash = cfg.cash
        self.positions: Dict[str, ValuePosition] = {}
        self._load_state()

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(f"[value] {msg}", flush=True)

    # -- state -----------------------------------------------------------------
    def _load_state(self) -> None:
        path = self.cfg.positions_path
        if not os.path.exists(path):
            return
        with open(path) as f:
            raw = json.load(f)
        self.cash = float(raw.get("cash", self.cfg.cash))
        self.positions = {
            p["slug"]: ValuePosition(**p) for p in raw.get("positions", [])
        }

    def _save_state(self) -> None:
        path = self.cfg.positions_path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            json.dump({
                "cash": self.cash,
                "positions": [asdict(p) for p in self.positions.values()],
            }, f, indent=2)

    # -- accounting --------------------------------------------------------------
    @property
    def exposure(self) -> float:
        return sum(p.stake for p in self.positions.values())

    @property
    def equity(self) -> float:
        # positions marked at entry price (conservative; no MTM feed needed)
        return self.cash + self.exposure

    # -- cycle -------------------------------------------------------------------
    def run_once(self) -> Dict:
        settled = self._settle_resolved()
        opened = self._open_new()
        self._save_state()
        self.journal.append({
            "event": "tick", "timestamp": _iso(), "equity": round(self.equity, 4),
            "bar_ts": int(time.time()),
        })
        summary = {
            "opened": opened, "settled": settled,
            "positions": len(self.positions),
            "exposure": round(self.exposure, 2),
            "cash": round(self.cash, 2), "equity": round(self.equity, 2),
        }
        self._log(f"cycle: {summary}")
        return summary

    def run_forever(self) -> None:
        while True:
            try:
                self.run_once()
            except KeyboardInterrupt:
                raise
            except Exception as e:
                self._log(f"cycle failed: {e!r} — retrying next interval")
                self.journal.append({"event": "error", "timestamp": _iso(),
                                     "error": repr(e)})
            time.sleep(self.cfg.scan_interval_s)

    # -- settlement ----------------------------------------------------------------
    def _settle_resolved(self) -> int:
        n = 0
        for slug in list(self.positions):
            pos = self.positions[slug]
            try:
                raw = self.scanner.market_by_slug(slug)
            except Exception:
                continue  # transient fetch problem; try next cycle
            if raw is None:
                continue
            winner = resolved_winner(raw)
            if winner is None:
                continue
            won = winner == pos.outcome_index
            payout = pos.shares if won else 0.0
            self.cash += payout
            pnl = payout - pos.stake
            del self.positions[slug]
            n += 1
            self._log(f"settled {slug}: {'WON' if won else 'LOST'} pnl={pnl:+.2f}")
            self.journal.append({
                "event": "value_settle", "timestamp": _iso(), "slug": slug,
                "question": pos.question, "outcome_label": pos.outcome_label,
                "won": won, "entry_price": pos.entry_price,
                "shares": pos.shares, "stake": pos.stake,
                "payout": payout, "pnl": pnl, "cash": round(self.cash, 4),
            })
        return n

    # -- entries ---------------------------------------------------------------------
    def _open_new(self) -> int:
        candidates = rank([c for c in self.scanner.scan()
                           if c.slug not in self.positions])
        n = 0
        for c in candidates:
            if len(self.positions) >= self.cfg.max_positions:
                break
            room = self.cfg.max_total_exposure - self.exposure
            if room < self.cfg.min_stake:
                break
            stake = min(kelly_stake(self.equity, c.p_true, c.ask, self.cfg),
                        room, self.cash)
            if stake < self.cfg.min_stake:
                continue
            shares = stake / c.ask
            self.cash -= stake
            pos = ValuePosition(
                slug=c.slug, question=c.question, token_id=c.token_id,
                outcome_index=c.outcome_index, outcome_label=c.outcome_label,
                entry_price=c.ask, shares=shares, stake=stake,
                p_true=c.p_true, edge=c.edge, entry_ts=_iso(),
                end_date=c.end_date.isoformat() if c.end_date else "",
            )
            self.positions[c.slug] = pos
            n += 1
            self._log(f"opened {c.slug}: {c.outcome_label} @ {c.ask:.3f} "
                      f"stake={stake:.2f} edge={c.edge:.3f}")
            self.journal.append({
                "event": "value_open", "timestamp": _iso(), "slug": c.slug,
                "question": c.question, "outcome_label": c.outcome_label,
                "price": c.ask, "shares": shares, "stake": stake,
                "p_true": c.p_true, "edge": c.edge,
                "days_left": c.days_left, "cash": round(self.cash, 4),
            })
        return n


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()
