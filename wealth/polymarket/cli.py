"""`wealth polymarket` subcommands.

  run       trade the BTC up/down markets (paper by default), once or looping
  discover  print current/next markets, books, fair value, edges (read-only;
            also the smoke test for slug formats and token ordering)
  record    snapshot books without trading (data for replay)
  replay    drive the strategy across a recording -> honest offline eval
  report    summarize the bot's journal

Live mode moves real money and needs a Polygon private key: it is gated behind
an explicit typed confirmation on top of the config switch.
"""
from __future__ import annotations

import sys
import time
from typing import Optional

from wealth.polymarket.config import PolymarketConfig


def _print_safety_banner(cfg: PolymarketConfig) -> None:
    if cfg.mode == "paper":
        print("[PAPER] simulated fills on real Polymarket order books — no real funds at risk.")
    else:
        print("[LIVE] !!! real money mode — orders will spend real USDC on Polymarket !!!")


def _confirm_live(cfg: PolymarketConfig, assume_yes: bool) -> bool:
    if cfg.mode != "live":
        return True
    _print_safety_banner(cfg)
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        print("live mode needs an interactive confirmation or --yes-i-am-sure", file=sys.stderr)
        return False
    answer = input("Type LIVE to confirm real-money trading: ").strip()
    return answer == "LIVE"


def _build_runner(cfg: PolymarketConfig, with_recorder: bool = False,
                  trading: bool = True):
    """trading=False builds an observe-only runner (paper executor, no orders)."""
    import requests

    from wealth.live.journal import Journal
    from wealth.polymarket.clob import ClobReadClient
    from wealth.polymarket.gamma import GammaClient
    from wealth.polymarket.runner import PolymarketRunner, SpotFeed

    session = requests.Session()
    gamma = GammaClient(cfg.gamma_url, session=session)
    clob = ClobReadClient(cfg.clob_url, session=session)
    spot = SpotFeed(cfg.spot_exchange, cfg.spot_symbol)

    if cfg.mode == "live" and trading:
        from wealth.polymarket.live import LiveExecutor

        executor = LiveExecutor(cfg)
    else:
        from wealth.polymarket.paper import PaperExecutor

        executor = PaperExecutor(starting_cash=cfg.cash, fee_bps=cfg.fee_bps,
                                 state_path=cfg.state_path)

    recorder = None
    if with_recorder:
        from wealth.polymarket.recorder import BookRecorder

        recorder = BookRecorder(cfg.recordings_dir)

    journal = Journal(cfg.journal_path)
    return PolymarketRunner(cfg, gamma, clob, spot, executor,
                            journal=journal, recorder=recorder, trade=trading)


def cmd_run(args) -> int:
    cfg = PolymarketConfig.from_yaml(args.config)
    if not _confirm_live(cfg, args.yes_i_am_sure):
        print("aborted.", file=sys.stderr)
        return 1
    _print_safety_banner(cfg)
    runner = _build_runner(cfg, with_recorder=cfg.record_books)

    if args.once:
        try:
            rec = runner.tick()
        except Exception as exc:  # noqa: BLE001 - single tick: report, don't trace
            print(f"tick failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            print("(check network access to Polymarket/exchange APIs)", file=sys.stderr)
            return 1
        print(f"tick: status={rec.get('status')} equity={rec.get('equity', '-'):.2f} "
              f"spot={rec.get('spot', '-')}")
        for o in rec.get("orders", []):
            print(f"  {o['side']} {o['quantity']:.2f} {o['symbol']} @ {o['price']:.3f} "
                  f"(edge {o.get('edge', 0):.3f})")
        return 0

    print(f"trading {cfg.series} BTC up/down markets every {cfg.interval_seconds}s "
          f"— Ctrl-C to stop")
    runner.run_forever()
    return 0


def cmd_discover(args) -> int:
    """Read-only: what would the bot see right now? Verifies discovery live."""
    cfg = PolymarketConfig.from_yaml(args.config)
    runner = _build_runner(cfg, trading=False)
    now = time.time()

    try:
        runner.spot_feed.refresh()
    except Exception as exc:  # noqa: BLE001
        print(f"spot feed failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    spot = runner.spot_feed.spot
    sigma = runner.spot_feed.sigma
    print(f"spot {cfg.spot_symbol}: {spot}   sigma/sqrt(s): {sigma}")

    try:
        markets = runner.gamma.discover(cfg.series, now)
    except Exception as exc:  # noqa: BLE001
        print(f"gamma discovery failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    if not markets:
        print("no BTC up/down markets found — check series config and slug formats "
              "(gamma discovery fallback also came up empty)", file=sys.stderr)
        return 1
    for m in markets:
        print(f"\n{m.slug}  [{m.series}]  expires in {m.seconds_to_expiry(now):.0f}s")
        print(f"  question: {m.question}")
        print(f"  tokens: UP={m.token_id_up[:16]}… DOWN={m.token_id_down[:16]}…")
        print(f"  price_to_beat: {m.price_to_beat}")
        try:
            book_up = runner.clob.get_book(m.token_id_up)
            book_down = runner.clob.get_book(m.token_id_down)
        except Exception as exc:
            print(f"  books unavailable: {exc}")
            continue
        period_open = m.price_to_beat or spot
        fair_up = runner.model.prob_up(spot, period_open,
                                       m.seconds_to_expiry(now), sigma or 0.0)
        print(f"  UP  book: bid {book_up.best_bid} ask {book_up.best_ask}  fair {fair_up:.3f}")
        print(f"  DOWN book: bid {book_down.best_bid} ask {book_down.best_ask}  "
              f"fair {1 - fair_up:.3f}")
        if book_up.best_ask is not None:
            print(f"  edge UP: {fair_up - book_up.best_ask:+.3f}   "
                  f"edge DOWN: {(1 - fair_up) - book_down.best_ask:+.3f}")
    return 0


def cmd_record(args) -> int:
    cfg = PolymarketConfig.from_yaml(args.config)
    cfg.mode = "paper"  # recording never trades live
    runner = _build_runner(cfg, with_recorder=True, trading=False)
    print(f"recording books to {cfg.recordings_dir}/ every {cfg.interval_seconds}s "
          f"— Ctrl-C to stop")
    runner.run_forever()
    return 0


def cmd_replay(args) -> int:
    cfg = PolymarketConfig.from_yaml(args.config)
    from wealth.live.journal import Journal
    from wealth.polymarket.replay import replay_run

    journal = Journal(args.journal) if args.journal else None
    runner = replay_run(cfg, args.file, journal=journal)
    ex = runner.executor
    print(f"replay done: cash={ex.get_cash():.2f} realized_pnl={ex.realized_pnl:+.2f} "
          f"open_positions={len(ex.get_positions())}")
    if journal is not None:
        equity = journal.equity_curve()
        if not equity.empty:
            from wealth.reporting.report import build_report

            print()
            print(build_report(equity, title=f"polymarket replay of {args.file}"))
    return 0


def cmd_report(args) -> int:
    cfg = PolymarketConfig.from_yaml(args.config)
    from wealth.live.journal import Journal
    from wealth.reporting.report import build_report, write_report

    journal = Journal(cfg.journal_path)
    equity = journal.equity_curve()
    if equity.empty:
        print("no journal data yet — run the bot first (wealth polymarket run --once)")
        return 0
    resolutions = [r for r in journal.records() if r.get("event") == "resolution"]
    wins = sum(1 for r in resolutions if r.get("pnl", 0) > 0)
    title = f"polymarket btc up/down bot ({cfg.mode})"
    print(build_report(equity, title=title))
    if resolutions:
        print(f"\nresolved markets: {len(resolutions)}  "
              f"wins: {wins}  ({wins / len(resolutions):.0%})")
    if args.out:
        write_report(args.out, equity, title=title)
        print(f"\nwrote report to {args.out}/")
    return 0


def add_subparser(sub) -> None:
    pm = sub.add_parser("polymarket", help="trade Polymarket BTC up/down markets (paper by default)")
    pmsub = pm.add_subparsers(dest="pm_command", required=True)

    run = pmsub.add_parser("run", help="run the bot (paper by default)")
    run.add_argument("--config", required=True)
    run.add_argument("--once", action="store_true", help="single tick")
    run.add_argument("--yes-i-am-sure", action="store_true", dest="yes_i_am_sure",
                     help="skip the interactive live-mode confirmation")
    run.set_defaults(func=cmd_run)

    disc = pmsub.add_parser("discover", help="print current markets, books, fair value")
    disc.add_argument("--config", required=True)
    disc.set_defaults(func=cmd_discover)

    rec = pmsub.add_parser("record", help="snapshot books without trading")
    rec.add_argument("--config", required=True)
    rec.set_defaults(func=cmd_record)

    rep = pmsub.add_parser("replay", help="replay a recording through the strategy")
    rep.add_argument("--config", required=True)
    rep.add_argument("--file", required=True, help="recording JSONL from `record`")
    rep.add_argument("--journal", default=None, help="write replay journal here")
    rep.set_defaults(func=cmd_replay)

    report = pmsub.add_parser("report", help="summarize the bot journal")
    report.add_argument("--config", required=True)
    report.add_argument("--out", default=None)
    report.set_defaults(func=cmd_report)
