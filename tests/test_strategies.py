"""Signal-generation tests on synthetic price series."""
import numpy as np
import pandas as pd
import pytest

from wealth.strategies import get_strategy, available_strategies


def idx(n):
    return pd.date_range("2020-01-01", periods=n, freq="D")


def ohlcv(close):
    close = pd.Series(list(close), index=idx(len(close)), dtype=float)
    return pd.DataFrame(
        {
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": 1.0,
        }
    )


def test_registry_has_builtins():
    for name in ["trend_breakout", "dual_momentum", "mean_reversion", "grid", "funding_arb"]:
        assert name in available_strategies()


def test_trend_breakout_goes_long_on_ramp():
    # Monotonic ramp -> after warmup the breakout should be long (weight 1.0).
    prices = np.arange(1, 60, dtype=float)
    data = {"X": ohlcv(prices)}
    strat = get_strategy("trend_breakout", entry_n=20, exit_n=10)
    w = strat.generate_weights(data)
    # During warmup there is no signal.
    assert (w["X"].iloc[:20] == 0).all()
    # On a pure uptrend it ends up fully long.
    assert w["X"].iloc[-1] == pytest.approx(1.0)


def test_trend_breakout_warmup_flat():
    prices = np.arange(1, 30, dtype=float)
    strat = get_strategy("trend_breakout", entry_n=20, exit_n=10)
    w = strat.generate_weights({"X": ohlcv(prices)})
    assert w["X"].iloc[0] == 0.0  # no lookahead -> first bars flat


def test_mean_reversion_buys_dip():
    # Mildly noisy baseline (so rolling std > 0) then a sharp dip -> long,
    # then reverts back above the mean -> flat.
    rng = np.random.default_rng(1)
    base = list(100.0 + rng.normal(0, 0.5, 25))
    prices = base + [80.0] + [100.0] * 5
    strat = get_strategy("mean_reversion", lookback=20, entry_z=1.0, exit_z=0.0)
    w = strat.generate_weights({"X": ohlcv(prices)})
    # On the dip bar (index 25) we should be long.
    assert w["X"].iloc[25] == pytest.approx(1.0)
    # After reverting back to the mean, position is closed.
    assert w["X"].iloc[-1] == pytest.approx(0.0)


def test_dual_momentum_picks_winner():
    n = 200
    # Asset A trends up strongly, B is flat -> dual momentum holds A.
    a = 100 * (1.01 ** np.arange(n))
    b = np.full(n, 100.0)
    data = {"A": ohlcv(a), "B": ohlcv(b)}
    strat = get_strategy("dual_momentum", lookback=126, rebalance=21)
    w = strat.generate_weights(data)
    last = w.iloc[-1]
    assert last["A"] == pytest.approx(1.0)
    assert last["B"] == pytest.approx(0.0)


def test_dual_momentum_goes_to_cash_when_all_negative():
    n = 200
    # Both assets decline -> absolute-momentum filter forces cash.
    a = 100 * (0.99 ** np.arange(n))
    b = 100 * (0.995 ** np.arange(n))
    data = {"A": ohlcv(a), "B": ohlcv(b)}
    strat = get_strategy("dual_momentum", lookback=126, rebalance=21)
    w = strat.generate_weights(data)
    assert w.iloc[-1].sum() == pytest.approx(0.0)


def test_stub_strategies_raise():
    for name in ["grid", "funding_arb"]:
        strat = get_strategy(name)
        with pytest.raises(NotImplementedError):
            strat.generate_weights({"X": ohlcv([1, 2, 3])})


def test_weights_never_exceed_full_investment():
    n = 120
    rng = np.cumsum(np.random.default_rng(0).normal(0, 1, n)) + 100
    data = {"A": ohlcv(rng), "B": ohlcv(rng[::-1] + 50)}
    for name in ["trend_breakout", "mean_reversion", "dual_momentum"]:
        w = get_strategy(name).generate_weights(data)
        gross = w.abs().sum(axis=1)
        assert (gross <= 1.0 + 1e-9).all()
