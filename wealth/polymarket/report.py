"""Summarize the Polymarket bots' journals: windows, fills, value positions."""
from __future__ import annotations

import json
import os
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


def build_value_report(journal: Journal, positions_path: str = "") -> str:
    recs = journal.records()
    opens = [r for r in recs if r.get("event") == "value_open"]
    settles = [r for r in recs if r.get("event") == "value_settle"]

    positions: List[Dict] = []
    cash = None
    if positions_path and os.path.exists(positions_path):
        with open(positions_path) as f:
            raw = json.load(f)
        positions = raw.get("positions", [])
        cash = raw.get("cash")

    if not opens and not settles and not positions:
        return ("no value-bot data yet — run the scanner first "
                "(wealth poly value --once)")

    lines: List[str] = ["# Polymarket value bot — journal report", ""]
    if settles:
        pnls = [float(r["pnl"]) for r in settles]
        wins = sum(1 for r in settles if r.get("won"))
        lines += [
            f"settled         : {len(settles)}",
            f"hit rate        : {wins}/{len(settles)} "
            f"({100.0 * wins / len(settles):.1f}%)",
            f"total PnL       : {sum(pnls):+.2f}",
            f"avg PnL/trade   : {sum(pnls) / len(pnls):+.3f}",
        ]
    exposure = sum(float(p.get("stake", 0)) for p in positions)
    lines.append(f"open positions  : {len(positions)} (exposure {exposure:.2f})")
    if cash is not None:
        lines.append(f"cash            : {float(cash):.2f} "
                     f"(equity ~{float(cash) + exposure:.2f})")
    if positions:
        lines += ["", "## open positions"]
        for p in sorted(positions, key=lambda x: x.get("end_date", ""))[:15]:
            lines.append(
                f"- {p.get('slug', '?')}: {p.get('outcome_label')} "
                f"@ {float(p.get('entry_price', 0)):.3f} "
                f"stake={float(p.get('stake', 0)):.2f} "
                f"edge={float(p.get('edge', 0)):.3f}")
    if settles:
        lines += ["", "## last settlements"]
        for r in settles[-10:]:
            lines.append(
                f"- {r.get('slug', '?')}: {'WON' if r.get('won') else 'LOST'} "
                f"pnl={float(r['pnl']):+.2f}")
    return "\n".join(lines)


def candidates_table(candidates, top: int = 20) -> str:
    """Plain-text table of scan candidates for `wealth poly scan`."""
    if not candidates:
        return "no candidates passed the filters right now."
    lines = [f"{'ask':>6} {'edge':>7} {'days':>5} {'volume':>10}  market",
             "-" * 78]
    for c in candidates[:top]:
        days = f"{c.days_left:.1f}" if c.days_left is not None else "?"
        q = (c.question or c.slug)[:44]
        lines.append(f"{c.ask:6.3f} {c.edge:7.4f} {days:>5} {c.volume:10.0f}"
                     f"  {q} [{c.outcome_label}]")
    return "\n".join(lines)
