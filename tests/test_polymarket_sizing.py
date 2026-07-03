import pytest

from wealth.polymarket.sizing import MIN_ORDER_SHARES, kelly_binary, stake


def test_kelly_formula():
    # p=0.6, price=0.5 -> (0.6-0.5)/(1-0.5) = 0.2
    assert kelly_binary(0.6, 0.5) == pytest.approx(0.2)
    # p=0.55, price=0.45 -> 0.1/0.55
    assert kelly_binary(0.55, 0.45) == pytest.approx(0.1 / 0.55)


def test_kelly_zero_without_edge():
    assert kelly_binary(0.5, 0.5) == 0.0
    assert kelly_binary(0.4, 0.5) == 0.0  # negative edge floors at 0


def test_kelly_degenerate_prices():
    assert kelly_binary(0.6, 0.0) == 0.0
    assert kelly_binary(0.6, 1.0) == 0.0


def test_stake_basic():
    # kelly = 0.2, quarter kelly on $1000 -> $50, at price 0.5 -> 100 shares
    s = stake(p=0.6, price=0.5, bankroll=1000.0, kelly_mult=0.25, max_stake=100.0)
    assert s.stake_usd == pytest.approx(50.0)
    assert s.shares == pytest.approx(100.0)


def test_stake_capped_by_max_stake():
    s = stake(p=0.9, price=0.5, bankroll=10_000.0, kelly_mult=1.0, max_stake=50.0)
    assert s.stake_usd == pytest.approx(50.0)


def test_stake_capped_by_headroom():
    s = stake(p=0.9, price=0.5, bankroll=10_000.0, kelly_mult=1.0, max_stake=500.0, headroom=25.0)
    assert s.stake_usd == pytest.approx(25.0)


def test_stake_capped_by_bankroll():
    s = stake(p=0.99, price=0.5, bankroll=30.0, kelly_mult=2.0, max_stake=500.0)
    assert s.stake_usd == pytest.approx(30.0)


def test_stake_zero_without_edge():
    s = stake(p=0.5, price=0.55, bankroll=1000.0)
    assert s.stake_usd == 0.0 and s.shares == 0.0


def test_stake_min_size_floor():
    # tiny edge -> stake below 5-share minimum -> zero
    s = stake(p=0.5005, price=0.5, bankroll=1000.0, kelly_mult=0.25, max_stake=50.0)
    assert s.shares == 0.0
    # just over the floor passes
    s2 = stake(p=0.6, price=0.5, bankroll=1000.0, kelly_mult=0.25, max_stake=50.0)
    assert s2.shares >= MIN_ORDER_SHARES
