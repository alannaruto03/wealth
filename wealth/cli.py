"""wealth — command-line entry point.

Subcommands:
  backtest  prove a strategy on history -> metrics + plots
  run       run the bot hands-off (paper by default), once or looping
  tune      walk-forward re-optimize parameters (out-of-sample, anti-overfit)
  report    summarize the live/paper bot's journal
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import List, Optional

from wealth.config import BotConfig
from wealth.data.base import get_provider
from wealth.engine.backtest import Backtester
from wealth.engine.costs import CostModel
from wealth.strategies.base import get_strategy, available_strategies


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _cost_model(cfg) -> CostModel:
    return CostModel(commission_bps=cfg.commission_bps, slippage_bps=cfg.slippage_bps)


def _build_broker(cfg: BotConfig, provider):
    """Construct the broker named in the config. Default is PaperBroker."""
    if cfg.broker == "paper":
        from wealth.broker.paper import PaperBroker

        return PaperBroker(
            price_fn=lambda s: provider.latest_price(s, timeframe=cfg.timeframe),
            starting_cash=cfg.cash,
            cost_model=_cost_model(cfg),
            state_path=cfg.state_path,
        )
    if cfg.broker == "ccxt":
        from wealth.broker.ccxt_broker import CCXTBroker

        return CCXTBroker(exchange=cfg.exchange, mode=cfg.mode)
    if cfg.broker == "alpaca":
        from wealth.broker.alpaca import AlpacaBroker

        return AlpacaBroker(mode=cfg.mode)
    raise ValueError(f"unknown broker {cfg.broker!r}")


def _split_symbols(s: str) -> List[str]:
    return [x.strip() for x in s.split(",") if x.strip()]


# --------------------------------------------------------------------------- #
# subcommands
# --------------------------------------------------------------------------- #
def cmd_backtest(args) -> int:
    provider = get_provider(args.market)
    data = provider.fetch_ohlcv(
        _split_symbols(args.symbols), start=args.start, end=args.end, timeframe=args.timeframe
    )
    if not data:
        print("no data returned for the requested symbols/range", file=sys.stderr)
        return 1

    strat = get_strategy(args.strategy)
    bt = Backtester(
        cost_model=CostModel(args.commission_bps, args.slippage_bps), cash=args.cash
    )
    result = bt.run(strat, data)

    from wealth.reporting.report import build_report, write_report

    title = f"{args.strategy} on {args.symbols} ({args.market})"
    print(build_report(result.equity, result.trades, result.weights, title=title))
    if args.out:
        path = write_report(
            args.out, result.equity, result.trades, result.weights, title=title
        )
        print(f"\nwrote report to {args.out}/ ({path})")
    return 0


def cmd_run(args) -> int:
    cfg = BotConfig.from_yaml(args.config)
    if args.once:
        pass  # single tick below
    _print_safety_banner(cfg)

    provider = get_provider(cfg.market, exchange=cfg.exchange) if cfg.market == "crypto" \
        else get_provider(cfg.market)
    broker = _build_broker(cfg, provider)
    strat = get_strategy(cfg.strategy, **cfg.params)

    from wealth.live.journal import Journal
    from wealth.live.runner import Runner
    from wealth.live.scheduler import Scheduler

    journal = Journal(cfg.journal_path)
    runner = Runner(
        strat, broker, provider, symbols=cfg.symbols, timeframe=cfg.timeframe,
        lookback_bars=cfg.lookback_bars, journal=journal,
    )
    sched = Scheduler(runner, interval_seconds=cfg.interval_seconds)

    if args.once:
        rec = sched.run_once()
        print(f"tick: status={rec.get('status')} bar={rec.get('bar_ts')} "
              f"equity={rec.get('equity', '-')}")
        if rec.get("orders"):
            for o in rec["orders"]:
                print(f"  {o['side']} {o['quantity']:.6f} {o['symbol']} @ {o['price']:.4f}")
    else:
        print(f"running every {sched.interval}s — Ctrl-C to stop")
        sched.run_forever()
    return 0


def cmd_tune(args) -> int:
    cfg = BotConfig.from_yaml(args.config)
    provider = get_provider(cfg.market, exchange=cfg.exchange) if cfg.market == "crypto" \
        else get_provider(cfg.market)
    data = provider.fetch_ohlcv(
        cfg.symbols, start=args.start, end=args.end, timeframe=cfg.timeframe
    )
    if not data:
        print("no data for tuning", file=sys.stderr)
        return 1

    grid = cfg.tune_grid or _default_grid(cfg.strategy)
    if not grid:
        print(f"no tune grid for strategy {cfg.strategy!r}; set tune_grid in config",
              file=sys.stderr)
        return 1

    from wealth.tuning.walkforward import walk_forward

    report = walk_forward(
        cfg.strategy, data, grid, metric=args.metric, n_folds=args.folds,
        cost_model=_cost_model(cfg), cash=cfg.cash,
        periods_per_year=cfg.periods_per_year,
    )
    print(report.summary_text())
    if report.accepted:
        print(f"\nSuggested params (review before applying): {report.recommended_params}")
    else:
        print("\nNo robust improvement found out-of-sample — keep current params.")
    if args.out:
        import os
        os.makedirs(args.out, exist_ok=True)
        with open(os.path.join(args.out, "tune.md"), "w") as f:
            f.write(report.summary_text() + "\n")
        print(f"wrote {args.out}/tune.md")
    return 0


def cmd_report(args) -> int:
    cfg = BotConfig.from_yaml(args.config)
    from wealth.live.journal import Journal
    from wealth.broker.base import Position  # noqa: F401  (clarity)

    journal = Journal(cfg.journal_path)
    equity = journal.equity_curve()
    if equity.empty:
        print("no journal data yet — run the bot first (wealth run --once)")
        return 0

    from wealth.reporting.report import build_report, write_report

    title = f"{cfg.strategy} paper bot ({cfg.market})"
    print(build_report(equity, periods_per_year=cfg.periods_per_year, title=title))
    if args.out:
        write_report(args.out, equity, periods_per_year=cfg.periods_per_year, title=title)
        print(f"\nwrote report to {args.out}/")
    return 0


# --------------------------------------------------------------------------- #
# misc
# --------------------------------------------------------------------------- #
def _default_grid(strategy: str) -> dict:
    return {
        "trend_breakout": {"entry_n": [10, 20, 40, 55], "exit_n": [5, 10, 20]},
        "dual_momentum": {"lookback": [63, 126, 189, 252], "rebalance": [21]},
        "mean_reversion": {"lookback": [10, 20, 40], "entry_z": [1.0, 1.5, 2.0]},
    }.get(strategy, {})


def _print_safety_banner(cfg: BotConfig) -> None:
    if cfg.broker == "paper" or cfg.mode == "paper":
        print("[PAPER] simulated money on live prices — no real funds at risk.")
    else:
        print("[LIVE] !!! real money mode — orders will use real funds !!!")


def cmd_dashboard(args) -> int:
    """Launch the Streamlit dashboard (wealth[dashboard] extra required)."""
    import subprocess
    from importlib import util as importlib_util

    if importlib_util.find_spec("streamlit") is None:
        print("dashboard needs Streamlit + Plotly. Install with:\n"
              "  pip install -e '.[dashboard]'", file=sys.stderr)
        return 1

    app_path = os.path.join(os.path.dirname(__file__), "dashboard", "app.py")
    env = dict(os.environ)
    if args.config:
        env["WEALTH_DASHBOARD_CONFIG"] = args.config
    cmd = [
        sys.executable, "-m", "streamlit", "run", app_path,
        "--server.port", str(args.port),
    ]
    if args.headless:
        cmd += ["--server.headless", "true"]
    print(f"launching dashboard on http://localhost:{args.port} …")
    return subprocess.call(cmd, env=env)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="wealth", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    bt = sub.add_parser("backtest", help="prove a strategy on history")
    bt.add_argument("--market", default="crypto", choices=["crypto", "stocks"])
    bt.add_argument("--symbols", required=True, help="comma-separated, e.g. BTC/USDT,ETH/USDT")
    bt.add_argument("--strategy", required=True, choices=available_strategies())
    bt.add_argument("--start", default=None)
    bt.add_argument("--end", default=None)
    bt.add_argument("--timeframe", default="1d")
    bt.add_argument("--cash", type=float, default=100_000.0)
    bt.add_argument("--commission-bps", type=float, default=5.0, dest="commission_bps")
    bt.add_argument("--slippage-bps", type=float, default=5.0, dest="slippage_bps")
    bt.add_argument("--out", default=None, help="directory for summary.md + plots")
    bt.set_defaults(func=cmd_backtest)

    run = sub.add_parser("run", help="run the bot (paper by default)")
    run.add_argument("--config", required=True)
    run.add_argument("--once", action="store_true", help="single tick (cron/systemd)")
    run.set_defaults(func=cmd_run)

    tune = sub.add_parser("tune", help="walk-forward re-optimize (out-of-sample)")
    tune.add_argument("--config", required=True)
    tune.add_argument("--metric", default="calmar")
    tune.add_argument("--folds", type=int, default=4)
    tune.add_argument("--start", default=None)
    tune.add_argument("--end", default=None)
    tune.add_argument("--out", default=None)
    tune.set_defaults(func=cmd_tune)

    rep = sub.add_parser("report", help="summarize the paper/live bot journal")
    rep.add_argument("--config", required=True)
    rep.add_argument("--out", default=None)
    rep.set_defaults(func=cmd_report)

    dash = sub.add_parser("dashboard", help="launch the Streamlit dashboard")
    dash.add_argument("--config", default="configs/bot.yaml")
    dash.add_argument("--port", type=int, default=8501)
    dash.add_argument("--headless", action="store_true", help="no auto-open browser")
    dash.set_defaults(func=cmd_dashboard)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
