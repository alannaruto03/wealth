"""Summarize the Polymarket bot's journal: per-window PnL and fill stats."""
from __future__ import annotations

from typing import Dict, List

from wealth.live.journal import Journal


def build_poly_report(journal: Journal) -> str:
    recs = journal.records()
    settles = [r for r in recs if r.get("event") == "window_settle"]
    fills = [r for r in recs if r.get("event") == "fill"]
    starts = [r for r in recs if r.get("event") == "window_start"]
    if not settles and not starts:
        return "no journal data yet — run the bot first (wealth poly run …)"

    lines: List[str] = ["# Polymarket bot — journal report", ""]
    if settles:
        pnls = [float(r["pnl"]) for r in settles]
        total = sum(pnls)
        wins = sum(1 for p in pnls if p > 0)
        flat = sum(1 for p in pnls if p == 0)
        start_cash = float(starts[0]["cash"]) if starts else 0.0
        end_cash = float(settles[-1]["cash"])
        counts: Dict[str, int] = {}
        for r in settles:
            for k, v in (r.get("fills") or {}).items():
                counts[k] = counts.get(k, 0) + int(v)
        fees = sum(float(f.get("fee", 0.0)) for f in fills)
        lines += [
            f"windows settled : {len(settles)}",
            f"total PnL       : {total:+.2f}",
            f"win / flat / loss: {wins} / {flat} / {len(pnls) - wins - flat}",
            f"avg PnL/window  : {total / len(pnls):+.3f}",
            f"cash            : {start_cash:.2f} -> {end_cash:.2f}",
            f"fills (maker/taker/pair): {counts.get('maker', 0)} / "
            f"{counts.get('taker', 0)} / {counts.get('pair', 0)}",
            f"taker fees paid : {fees:.4f}",
            "",
            "## last windows",
        ]
        for r in settles[-10:]:
            f = r.get("fills") or {}
            lines.append(
                f"- {r.get('slug', '?')}: outcome={r.get('outcome')} "
                f"pnl={float(r['pnl']):+.2f} "
                f"fills m/t/p={f.get('maker', 0)}/{f.get('taker', 0)}/{f.get('pair', 0)}")
    else:
        lines.append(f"{len(starts)} window(s) started, none settled yet.")
    return "\n".join(lines)
