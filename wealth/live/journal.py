"""Append-only JSONL journal — the bot's track record.

Every tick records its timestamp, target weights, orders placed, and an equity
snapshot. Reports and the tuning loop read this back. Append-only so history is
never silently rewritten.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List

import pandas as pd


class Journal:
    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    def append(self, record: Dict[str, Any]) -> None:
        with open(self.path, "a") as f:
            f.write(json.dumps(record, default=str) + "\n")

    def records(self) -> List[Dict[str, Any]]:
        if not os.path.exists(self.path):
            return []
        out = []
        with open(self.path) as f:
            for line in f:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out

    def equity_curve(self) -> pd.Series:
        """Reconstruct the equity curve from logged snapshots."""
        recs = [r for r in self.records() if r.get("event") == "tick" and "equity" in r]
        if not recs:
            return pd.Series(dtype=float, name="equity")
        idx = pd.to_datetime([r["timestamp"] for r in recs])
        vals = [float(r["equity"]) for r in recs]
        return pd.Series(vals, index=idx, name="equity")

    def last_bar(self) -> Any:
        """Timestamp of the most recently traded bar (for idempotency)."""
        for r in reversed(self.records()):
            if r.get("event") == "tick" and r.get("bar_ts"):
                return r["bar_ts"]
        return None
