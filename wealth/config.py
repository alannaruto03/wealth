"""Bot configuration: dataclass + YAML loader.

A single config drives `wealth run`, `wealth tune`, and `wealth report` so the
backtest, the live bot, and tuning all agree on market/symbols/strategy/costs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import yaml


@dataclass
class BotConfig:
    market: str = "crypto"                 # "crypto" | "stocks"
    symbols: List[str] = field(default_factory=lambda: ["BTC/USDT"])
    strategy: str = "trend_breakout"
    params: Dict = field(default_factory=dict)
    timeframe: str = "1d"
    cash: float = 100_000.0
    commission_bps: float = 5.0
    slippage_bps: float = 5.0

    broker: str = "paper"                  # "paper" | "ccxt" | "alpaca"
    mode: str = "paper"                    # "paper" | "live"
    exchange: str = "binance"             # for crypto brokers/providers

    lookback_bars: int = 400
    interval_seconds: Optional[int] = None  # default: derived from timeframe
    state_dir: str = "state"
    periods_per_year: Optional[float] = None  # default: inferred from data

    # Default parameter grid used by `wealth tune` when none is supplied.
    tune_grid: Dict[str, List] = field(default_factory=dict)

    @property
    def state_path(self) -> str:
        return f"{self.state_dir}/broker.json"

    @property
    def journal_path(self) -> str:
        return f"{self.state_dir}/journal.jsonl"

    @classmethod
    def from_yaml(cls, path: str) -> "BotConfig":
        with open(path) as f:
            raw = yaml.safe_load(f) or {}
        known = {f.name for f in cls.__dataclass_fields__.values()}
        unknown = set(raw) - known
        if unknown:
            raise ValueError(f"unknown config keys: {sorted(unknown)}")
        return cls(**raw)

    def to_dict(self) -> Dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}
