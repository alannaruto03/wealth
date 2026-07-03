"""Config loading, report building, and CLI parser wiring."""
import numpy as np
import pandas as pd
import pytest

from wealth.config import BotConfig
from wealth.cli import build_parser
from wealth.reporting.report import build_report


def test_config_from_yaml(tmp_path):
    p = tmp_path / "bot.yaml"
    p.write_text(
        "market: crypto\nsymbols: [BTC/USDT]\nstrategy: trend_breakout\n"
        "params: {entry_n: 20, exit_n: 10}\ncash: 5000\nstate_dir: state/test\n"
    )
    cfg = BotConfig.from_yaml(str(p))
    assert cfg.market == "crypto"
    assert cfg.symbols == ["BTC/USDT"]
    assert cfg.params["entry_n"] == 20
    assert cfg.journal_path == "state/test/journal.jsonl"


def test_config_rejects_unknown_keys(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("market: crypto\nbogus_key: 1\n")
    with pytest.raises(ValueError):
        BotConfig.from_yaml(str(p))


def test_config_defaults_paper():
    cfg = BotConfig()
    assert cfg.broker == "paper"
    assert cfg.mode == "paper"


def test_build_report_contains_metrics():
    idx = pd.date_range("2020-01-01", periods=100, freq="D")
    eq = pd.Series(100 * (1.001 ** np.arange(100)), index=idx)
    md = build_report(eq, title="Test")
    assert "# Test" in md
    assert "CAGR" in md
    assert "Max drawdown" in md


def test_parser_backtest_args():
    parser = build_parser()
    args = parser.parse_args(
        ["backtest", "--market", "crypto", "--symbols", "BTC/USDT",
         "--strategy", "trend_breakout"]
    )
    assert args.command == "backtest"
    assert args.symbols == "BTC/USDT"
    assert args.commission_bps == 5.0


def test_parser_run_once():
    parser = build_parser()
    args = parser.parse_args(["run", "--config", "configs/bot.yaml", "--once"])
    assert args.once is True


# ------------------------------------------------------------------ polymarket


def test_polymarket_config_from_yaml(tmp_path):
    from wealth.polymarket.config import PolymarketConfig

    p = tmp_path / "pm.yaml"
    p.write_text(
        "mode: paper\nseries: ['15m']\ncash: 500\nedge_threshold: 0.05\n"
        "state_dir: state/pmtest\n"
    )
    cfg = PolymarketConfig.from_yaml(str(p))
    assert cfg.mode == "paper"
    assert cfg.series == ["15m"]
    assert cfg.edge_threshold == 0.05
    assert cfg.journal_path == "state/pmtest/journal.jsonl"
    assert cfg.state_path == "state/pmtest/executor.json"


def test_polymarket_config_rejects_unknown_keys(tmp_path):
    from wealth.polymarket.config import PolymarketConfig

    p = tmp_path / "pm.yaml"
    p.write_text("mode: paper\ntotally_bogus: 1\n")
    with pytest.raises(ValueError):
        PolymarketConfig.from_yaml(str(p))


def test_polymarket_shipped_config_loads():
    from wealth.polymarket.config import PolymarketConfig

    cfg = PolymarketConfig.from_yaml("configs/polymarket.yaml")
    assert cfg.mode == "paper"
    assert set(cfg.series) == {"hourly", "15m"}


def test_parser_dashboard_address():
    parser = build_parser()
    args = parser.parse_args(["dashboard", "--config", "configs/polymarket.yaml",
                              "--address", "0.0.0.0", "--port", "8501"])
    assert args.address == "0.0.0.0"
    # default stays localhost-only
    args2 = parser.parse_args(["dashboard"])
    assert args2.address == "localhost"


def test_parser_polymarket_run():
    parser = build_parser()
    args = parser.parse_args(
        ["polymarket", "run", "--config", "configs/polymarket.yaml", "--once"]
    )
    assert args.command == "polymarket"
    assert args.pm_command == "run"
    assert args.once is True
    assert args.yes_i_am_sure is False


def test_parser_polymarket_subcommands_wired():
    parser = build_parser()
    for cmd, extra in [("discover", []), ("record", []),
                       ("replay", ["--file", "x.jsonl"]), ("report", [])]:
        args = parser.parse_args(
            ["polymarket", cmd, "--config", "configs/polymarket.yaml", *extra]
        )
        assert args.pm_command == cmd
        assert callable(args.func)
