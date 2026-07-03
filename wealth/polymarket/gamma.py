"""Market discovery via Polymarket's Gamma API (read-only, no auth).

Short-term BTC "Up or Down" markets come in series: 15-minute markets with
computable slugs (btc-updown-15m-<period_start_unix>) and hourly markets whose
slug format has drifted over time. Discovery therefore tries computed slug
candidates first and falls back to scanning soon-ending open markets, so a
slug-format change degrades to a slower lookup instead of a broken bot.

API quirk: `clobTokenIds` and `outcomes` are JSON-encoded *strings*, and token
order must be mapped through `outcomes` — never assumed.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, List, Optional
from zoneinfo import ZoneInfo

import requests

FIFTEEN_MIN_S = 900
HOUR_S = 3600
_ET = ZoneInfo("America/New_York")

# Question/slug fingerprints for the BTC up/down series.
_BTC_UPDOWN_RE = re.compile(r"(btc|bitcoin).*(up.?or.?down|updown)", re.IGNORECASE)


@dataclass
class MarketInfo:
    slug: str
    condition_id: str
    question: str
    token_id_up: str
    token_id_down: str
    start_ts: float           # unix seconds, period start (best effort)
    end_ts: float             # unix seconds, resolution time
    series: str               # "15m" | "hourly"
    neg_risk: bool = False
    closed: bool = False
    price_to_beat: Optional[float] = None  # period-open price when Gamma provides it

    def seconds_to_expiry(self, now: float) -> float:
        return self.end_ts - now


def _parse_iso(ts: Optional[str]) -> Optional[float]:
    if not ts:
        return None
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()


def _maybe_json_list(value) -> List:
    """Gamma returns list fields as JSON-encoded strings; tolerate both."""
    if value is None:
        return []
    if isinstance(value, str):
        return json.loads(value)
    return list(value)


def parse_market(raw: dict, series: str) -> MarketInfo:
    token_ids = _maybe_json_list(raw.get("clobTokenIds"))
    outcomes = [str(o) for o in _maybe_json_list(raw.get("outcomes"))]
    if len(token_ids) != 2 or len(outcomes) != 2:
        raise ValueError(
            f"market {raw.get('slug')}: expected 2 outcomes/tokens, "
            f"got outcomes={outcomes} tokens={len(token_ids)}"
        )
    up_words = {"up", "yes"}
    if outcomes[0].strip().lower() in up_words:
        up, down = token_ids[0], token_ids[1]
    elif outcomes[1].strip().lower() in up_words:
        up, down = token_ids[1], token_ids[0]
    else:
        raise ValueError(f"market {raw.get('slug')}: cannot identify Up outcome in {outcomes}")

    end_ts = _parse_iso(raw.get("endDate"))
    if end_ts is None:
        raise ValueError(f"market {raw.get('slug')}: missing endDate")
    # eventStartTime marks the period open for the up/down series when present.
    start_ts = _parse_iso(raw.get("eventStartTime")) or _parse_iso(raw.get("startDate"))
    if start_ts is None or start_ts < end_ts - 24 * 3600:
        # startDate is often the listing time, far before the period; fall back
        # to end - period length.
        start_ts = end_ts - (FIFTEEN_MIN_S if series == "15m" else HOUR_S)

    price_to_beat = None
    for key in ("priceToBeat", "price_to_beat", "openPrice"):
        if raw.get(key) is not None:
            try:
                price_to_beat = float(raw[key])
            except (TypeError, ValueError):
                pass
            break

    return MarketInfo(
        slug=raw.get("slug", ""),
        condition_id=raw.get("conditionId", ""),
        question=raw.get("question", ""),
        token_id_up=str(up),
        token_id_down=str(down),
        start_ts=start_ts,
        end_ts=end_ts,
        series=series,
        neg_risk=bool(raw.get("negRisk", False)),
        closed=bool(raw.get("closed", False)),
        price_to_beat=price_to_beat,
    )


def slug_15m(period_start: int) -> str:
    return f"btc-updown-15m-{int(period_start)}"


def hourly_slug_candidates(period_start: int) -> List[str]:
    """Best-guess hourly slugs; discovery falls back to a scan if none hit."""
    et = datetime.fromtimestamp(period_start, tz=timezone.utc).astimezone(_ET)
    hour12 = et.strftime("%I").lstrip("0")
    ampm = et.strftime("%p").lower()
    month = et.strftime("%B").lower()
    return [
        f"btc-updown-1h-{int(period_start)}",
        f"btc-updown-{int(period_start)}",
        f"bitcoin-up-or-down-{month}-{et.day}-{hour12}{ampm}-et",
    ]


class GammaClient:
    def __init__(self, base_url: str = "https://gamma-api.polymarket.com",
                 session: Optional[requests.Session] = None, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.timeout = timeout

    def _get(self, path: str, **params) -> list | dict:
        resp = self.session.get(f"{self.base_url}{path}", params=params, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def get_market_by_slug(self, slug: str) -> Optional[dict]:
        results = self._get("/markets", slug=slug)
        return results[0] if results else None

    def list_open_markets_ending_soon(self, limit: int = 100) -> List[dict]:
        return list(self._get(
            "/markets", closed="false", active="true",
            order="endDate", ascending="true", limit=limit,
        ))

    # -- discovery -----------------------------------------------------------
    def _find_by_slugs(self, slugs: Iterable[str], series: str) -> Optional[MarketInfo]:
        for slug in slugs:
            raw = self.get_market_by_slug(slug)
            if raw:
                return parse_market(raw, series)
        return None

    def _find_by_scan(self, series: str, period_start: int, period_s: int) -> Optional[MarketInfo]:
        """Fallback: scan soon-ending open markets for the BTC up/down series."""
        expected_end = period_start + period_s
        for raw in self.list_open_markets_ending_soon():
            text = f"{raw.get('slug', '')} {raw.get('question', '')}"
            if not _BTC_UPDOWN_RE.search(text):
                continue
            end_ts = _parse_iso(raw.get("endDate"))
            if end_ts is None or abs(end_ts - expected_end) > 60:
                continue
            try:
                return parse_market(raw, series)
            except ValueError:
                continue
        return None

    def find_market(self, series: str, period_start: int) -> Optional[MarketInfo]:
        if series == "15m":
            found = self._find_by_slugs([slug_15m(period_start)], series)
            period_s = FIFTEEN_MIN_S
        elif series == "hourly":
            found = self._find_by_slugs(hourly_slug_candidates(period_start), series)
            period_s = HOUR_S
        else:
            raise ValueError(f"unknown series '{series}' (expected '15m' or 'hourly')")
        return found or self._find_by_scan(series, period_start, period_s)

    def discover(self, series: List[str], now: float) -> List[MarketInfo]:
        """Current + next market for each configured series."""
        out: List[MarketInfo] = []
        for s in series:
            period_s = FIFTEEN_MIN_S if s == "15m" else HOUR_S
            current_start = int(now // period_s * period_s)
            for start in (current_start, current_start + period_s):
                m = self.find_market(s, start)
                if m and not m.closed:
                    out.append(m)
        return out
