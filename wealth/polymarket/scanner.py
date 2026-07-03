"""Scan Polymarket's event markets for favorite-longshot-bias candidates.

The documented, persistent retail bias on prediction markets: longshots are
overpriced, near-certainties underpriced. This scanner walks the Gamma API
for liquid binary markets resolving soon, keeps favorites trading in a
configurable band (default 90-97c), and estimates the edge of buying them
after a conservative calibration bump, a haircut, and the event-tier taker
fee. No latency component — this is the slow edge a small player can hold.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional

import httpx

from wealth.polymarket.config import ValueBotConfig
from wealth.polymarket.discovery import GAMMA_BASE
from wealth.polymarket.fees import taker_fee_per_share


@dataclass
class Candidate:
    slug: str
    question: str
    token_id: str
    outcome_index: int          # which outcome we'd buy
    outcome_label: str
    ask: float                  # price we'd pay for the favorite
    spread: float
    volume: float
    liquidity: float
    end_date: Optional[datetime]
    days_left: Optional[float]
    p_true: float               # calibrated win probability estimate
    edge: float                 # p_true - ask - taker fee
    stake_score: float          # ranking key: edge weighted by liquidity


def _loads(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return None
    return v


def _parse_end_date(raw: Dict) -> Optional[datetime]:
    for key in ("endDate", "endDateIso", "end_date_iso"):
        v = raw.get(key)
        if v:
            try:
                return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
            except ValueError:
                continue
    return None


def bias_bump(ask: float, cfg: ValueBotConfig) -> float:
    """Calibration bump: how much richer than price favorites tend to be.

    Linear between (fav_min, bias_bump_low) and (fav_max, bias_bump_high) —
    deliberately conservative versus the published favorite-longshot curves.
    """
    lo, hi = cfg.fav_min, cfg.fav_max
    if hi <= lo:
        return cfg.bias_bump_low
    t = min(1.0, max(0.0, (ask - lo) / (hi - lo)))
    return cfg.bias_bump_low + t * (cfg.bias_bump_high - cfg.bias_bump_low)


def estimate_edge(ask: float, cfg: ValueBotConfig) -> tuple:
    """(p_true, edge) of buying the favorite at ``ask``."""
    p_true = min(0.995, ask + bias_bump(ask, cfg)) - cfg.haircut
    fee = taker_fee_per_share(ask, cfg.event_fee_rate)
    return p_true, p_true - ask - fee


def parse_candidate(raw: Dict, cfg: ValueBotConfig,
                    now: Optional[datetime] = None) -> Optional[Candidate]:
    """Turn one raw Gamma market dict into a Candidate, or None if filtered."""
    slug = raw.get("slug", "") or ""
    for pat in cfg.exclude_slug_patterns:
        if re.search(pat, slug):
            return None
    if raw.get("closed") or raw.get("active") is False:
        return None

    outcomes = _loads(raw.get("outcomes")) or []
    token_ids = _loads(raw.get("clobTokenIds")) or []
    if len(outcomes) != 2 or len(token_ids) != 2:
        return None  # binary markets only

    volume = float(raw.get("volumeNum") or raw.get("volume") or 0)
    liquidity = float(raw.get("liquidityNum") or raw.get("liquidity") or 0)
    if volume < cfg.min_volume or liquidity < cfg.min_liquidity:
        return None

    end_date = _parse_end_date(raw)
    days_left = None
    if end_date is not None:
        now = now or datetime.now(timezone.utc)
        days_left = (end_date - now).total_seconds() / 86400.0
        if days_left < 0 or days_left > cfg.max_days:
            return None
    else:
        return None  # no resolution date -> can't bound holding period

    try:
        best_bid = float(raw.get("bestBid"))
        best_ask = float(raw.get("bestAsk"))
    except (TypeError, ValueError):
        return None
    if not (0.0 < best_bid < 1.0 and 0.0 < best_ask <= 1.0):
        return None
    spread = best_ask - best_bid
    if spread < 0 or spread > cfg.max_spread + 1e-9:
        return None

    # bestBid/bestAsk quote outcome 0; buying outcome 1 costs (1 - bestBid)
    sides = ((0, best_ask), (1, 1.0 - best_bid))
    fav_index, fav_ask = max(sides, key=lambda s: s[1] if s[1] < 1.0 else 0.0)
    if not (cfg.fav_min <= fav_ask <= cfg.fav_max):
        return None

    p_true, edge = estimate_edge(fav_ask, cfg)
    if edge < cfg.min_edge:
        return None

    return Candidate(
        slug=slug,
        question=raw.get("question", ""),
        token_id=str(token_ids[fav_index]),
        outcome_index=fav_index,
        outcome_label=str(outcomes[fav_index]),
        ask=fav_ask,
        spread=spread,
        volume=volume,
        liquidity=liquidity,
        end_date=end_date,
        days_left=days_left,
        p_true=p_true,
        edge=edge,
        stake_score=edge * min(1.0, liquidity / 10_000.0),
    )


def resolved_winner(raw: Dict) -> Optional[int]:
    """Index of the winning outcome for a closed market, else None."""
    if not raw.get("closed"):
        return None
    prices = _loads(raw.get("outcomePrices"))
    if not prices or len(prices) != 2:
        return None
    try:
        p0, p1 = float(prices[0]), float(prices[1])
    except (TypeError, ValueError):
        return None
    if p0 == p1:
        return None  # unresolved / split — leave it alone
    return 0 if p0 > p1 else 1


def rank(candidates: List[Candidate]) -> List[Candidate]:
    return sorted(candidates, key=lambda c: c.stake_score, reverse=True)


class GammaScanner:
    """Paginates Gamma /markets and yields ranked candidates."""

    def __init__(self, cfg: ValueBotConfig, timeout: float = 15.0):
        self.cfg = cfg
        self._client = httpx.Client(base_url=GAMMA_BASE, timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def fetch_markets(self) -> List[Dict]:
        out: List[Dict] = []
        page_size = 500
        for page in range(self.cfg.max_scan_pages):
            r = self._client.get("/markets", params={
                "active": "true", "closed": "false",
                "limit": page_size, "offset": page * page_size,
                "order": "volumeNum", "ascending": "false",
            })
            r.raise_for_status()
            items = r.json()
            if not items:
                break
            out.extend(items)
            if len(items) < page_size:
                break
        return out

    def scan(self) -> List[Candidate]:
        markets = self.fetch_markets()
        cands = [c for c in (parse_candidate(m, self.cfg) for m in markets) if c]
        # one candidate per slug (dedupe defensively)
        seen, unique = set(), []
        for c in rank(cands):
            if c.slug not in seen:
                seen.add(c.slug)
                unique.append(c)
        return unique

    def market_by_slug(self, slug: str) -> Optional[Dict]:
        r = self._client.get("/markets", params={"slug": slug})
        r.raise_for_status()
        items = r.json()
        return items[0] if items else None
