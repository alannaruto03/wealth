"""Run the bot's tick on a fixed cadence.

A deliberately simple loop: call ``runner.tick()`` every ``interval_seconds``.
One-shot mode (``run_once``) is for cron/systemd; the looping mode is for a
long-running process. State + idempotency live in the broker and journal, so a
crash-and-restart is safe.
"""
from __future__ import annotations

import time
from typing import Optional

from wealth.live.runner import Runner

_TIMEFRAME_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
    "1w": 604800,
}


def interval_for_timeframe(timeframe: str) -> int:
    return _TIMEFRAME_SECONDS.get(timeframe, 86400)


class Scheduler:
    def __init__(self, runner: Runner, interval_seconds: Optional[int] = None):
        self.runner = runner
        self.interval = interval_seconds or interval_for_timeframe(runner.timeframe)

    def run_once(self) -> dict:
        return self.runner.tick()

    def run_forever(self, max_ticks: Optional[int] = None, sleep_fn=time.sleep) -> None:
        """Loop ticks until interrupted. ``max_ticks`` bounds it for testing."""
        ticks = 0
        try:
            while max_ticks is None or ticks < max_ticks:
                record = self.runner.tick()
                status = record.get("status")
                ts = record.get("bar_ts", "-")
                print(f"[tick] bar={ts} status={status} equity={record.get('equity', '-')}")
                ticks += 1
                if max_ticks is not None and ticks >= max_ticks:
                    break
                sleep_fn(self.interval)
        except KeyboardInterrupt:  # pragma: no cover - interactive
            print("scheduler stopped by user")
