"""Polymarket bot configuration: dataclass + YAML loader.

Mirrors wealth.config.BotConfig conventions: one YAML drives the runner and
the report so both agree on markets, model parameters, and risk limits.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

import yaml


@dataclass
class PolyBotConfig:
    # market selection
    asset: str = "BTC"              # BTC | ETH | SOL | XRP
    horizon: str = "15m"            # 15m | 5m | 1h
    mode: str = "paper"             # paper | live

    # capital (paper cash; live uses the funded account balance)
    cash: float = 1_000.0

    # fair-value model
    vol_halflife_s: float = 90.0    # EWMA half-life for 1s realized vol
    vol_floor: float = 1e-5         # per-sqrt-second log-return vol floor
    vol_seed: float = 8e-5          # starting per-sqrt-second vol before warmup

    # maker quoting
    quote_size: float = 20.0        # shares per quote (resting minimum is 5)
    min_half_spread: float = 0.02   # never quote tighter than this to fair value
    vol_half_spread_mult: float = 1.2   # half-spread widens with sigma*sqrt(tau)
    inventory_skew: float = 0.04    # max fair-value shift at full inventory
    requote_ticks: int = 2          # cancel/replace when quote drifts this many ticks
    impulse_bps: float = 8.0        # spot move (bps) inside impulse_window_s -> pull quotes
    impulse_window_s: float = 3.0
    no_quote_final_s: float = 45.0  # stop maker quoting this close to window end

    # selective taker (edge must beat the p(1-p) fee curve plus this margin)
    taker_edge_margin: float = 0.04
    taker_max_price: float = 0.98   # never take above this price
    taker_final_cutoff_s: float = 5.0

    # complete-set capture (buy YES+NO when combined asks < 1 - margin)
    complete_set_margin: float = 0.015
    complete_set_max_shares: float = 100.0

    # fees (taker-only, fee = shares * rate * p * (1-p); makers pay zero)
    taker_fee_rate: float = 0.072

    # hard risk limits
    max_inventory: float = 200.0    # max net shares per side per window
    max_window_notional: float = 150.0  # max $ spent per window
    daily_loss_limit: float = 50.0  # kill switch: halt for the day past this loss
    feed_stale_s: float = 10.0      # halt quoting if the spot feed is older than this

    # infrastructure
    state_dir: str = "state/polymarket"
    poll_interval_s: float = 1.0    # REST fallback polling cadence
    prefer_websocket: bool = True   # try WS feeds first, degrade to REST polling
    resolution_timeout_s: float = 180.0  # wait this long for Gamma to resolve a window

    @property
    def journal_path(self) -> str:
        return f"{self.state_dir}/journal.jsonl"

    @property
    def horizon_seconds(self) -> int:
        return {"5m": 300, "15m": 900, "1h": 3600}[self.horizon]

    @classmethod
    def from_yaml(cls, path: str) -> "PolyBotConfig":
        return _from_yaml(cls, path)

    def to_dict(self) -> Dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


@dataclass
class ValueBotConfig:
    """Longshot-bias fader over long-dated event markets (hold to resolution)."""
    mode: str = "paper"             # paper | live (live not implemented)
    cash: float = 1_000.0

    # scan filters
    fav_min: float = 0.90           # favorite price band to buy into
    fav_max: float = 0.97
    min_volume: float = 5_000.0     # $ lifetime volume
    min_liquidity: float = 500.0    # $ book liquidity
    max_days: float = 30.0          # only markets resolving within this window
    max_spread: float = 0.02
    exclude_slug_patterns: list = None  # default set in __post_init__

    # edge model (conservative favorite-longshot calibration)
    bias_bump_low: float = 0.020    # bump at fav_min
    bias_bump_high: float = 0.010   # bump at fav_max
    haircut: float = 0.005          # humility discount on p_true
    event_fee_rate: float = 0.035   # event-tier taker fee rate
    min_edge: float = 0.004

    # sizing (capped fractional Kelly)
    kelly_fraction: float = 0.25
    max_stake_per_market: float = 25.0
    max_total_exposure: float = 300.0
    max_positions: int = 25
    min_stake: float = 2.0

    # infrastructure
    scan_interval_s: float = 1_800.0
    max_scan_pages: int = 4         # x500 markets/page
    state_dir: str = "state/polymarket"

    def __post_init__(self):
        if self.exclude_slug_patterns is None:
            self.exclude_slug_patterns = ["-updown-"]

    @property
    def journal_path(self) -> str:
        return f"{self.state_dir}/value_journal.jsonl"

    @property
    def positions_path(self) -> str:
        return f"{self.state_dir}/value_positions.json"

    @classmethod
    def from_yaml(cls, path: str) -> "ValueBotConfig":
        return _from_yaml(cls, path)

    def to_dict(self) -> Dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


def _from_yaml(cls, path: str):
    with open(path) as f:
        raw = yaml.safe_load(f) or {}
    known = {f.name for f in cls.__dataclass_fields__.values()}
    unknown = set(raw) - known
    if unknown:
        raise ValueError(f"unknown config keys: {sorted(unknown)}")
    return cls(**raw)
