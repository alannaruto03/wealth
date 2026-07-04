"""Polymarket bot configuration: dataclass + YAML loader.

Mirrors wealth.config.BotConfig (strict unknown-key rejection, state/journal
path properties) but is a separate dataclass: the Polymarket bot has its own
knobs (edge threshold, Kelly fraction, market series) that would pollute the
portfolio bot's config.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import yaml


@dataclass
class PolymarketConfig:
    mode: str = "paper"                     # "paper" | "live" — the ONLY switch to go live
    series: List[str] = field(default_factory=lambda: ["hourly", "15m"])
    cash: float = 1_000.0                   # paper bankroll (USDC terms)
    interval_seconds: float = 3.0           # tick cadence

    # fair value
    fair_model: str = "gaussian_digital"
    fair_params: Dict = field(default_factory=dict)
    spot_exchange: str = "binance"
    spot_symbol: str = "BTC/USDT"

    # signal
    edge_threshold: float = 0.03            # enter when fair - ask - fee > this
    take_profit_edge: Optional[float] = None  # sell when bid >= fair + this; None = hold to resolution
    min_seconds_to_expiry: float = 20.0     # never enter closer to expiry than this

    # sizing / risk
    kelly_fraction: float = 0.25
    max_stake_per_trade: float = 50.0       # USDC
    max_market_exposure: float = 100.0      # cost basis per market (both tokens)
    max_total_exposure: float = 300.0
    daily_loss_limit: float = 0.05          # fraction of day-start equity -> kill switch
    max_spread: float = 0.05                # skip books wider than this
    min_book_depth_usd: float = 100.0       # at best ask
    stale_spot_max_s: float = 10.0
    stale_book_max_s: float = 10.0
    fee_bps: float = 0.0                    # taker fee (Polymarket: currently 0)

    state_dir: str = "state/polymarket"
    record_books: bool = False              # snapshot books to recordings/ while running

    # live-view publishing (web/index.html on Vercel or any static host)
    publish: bool = False                   # push snapshots to a secret GitHub gist
    publish_every_s: float = 60.0
    publish_token_env: str = "WEALTH_PUBLISH_TOKEN"

    # endpoints (overridable in tests)
    gamma_url: str = "https://gamma-api.polymarket.com"
    clob_url: str = "https://clob.polymarket.com"

    # live-only
    order_type: str = "FOK"                 # "FOK" | "GTC"
    private_key_env: str = "POLYMARKET_PRIVATE_KEY"
    funder: Optional[str] = None            # Polymarket proxy wallet address
    signature_type: int = 1

    @property
    def state_path(self) -> str:
        return f"{self.state_dir}/executor.json"

    @property
    def journal_path(self) -> str:
        return f"{self.state_dir}/journal.jsonl"

    @property
    def recordings_dir(self) -> str:
        return f"{self.state_dir}/recordings"

    @classmethod
    def from_yaml(cls, path: str) -> "PolymarketConfig":
        with open(path) as f:
            raw = yaml.safe_load(f) or {}
        known = {f.name for f in cls.__dataclass_fields__.values()}
        unknown = set(raw) - known
        if unknown:
            raise ValueError(f"unknown config keys: {sorted(unknown)}")
        return cls(**raw)

    def to_dict(self) -> Dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}
