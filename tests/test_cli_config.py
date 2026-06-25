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
