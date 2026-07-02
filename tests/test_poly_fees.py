"""Taker fee curve: shape, peak, break-even edge."""
from wealth.polymarket.fees import breakeven_edge, taker_fee, taker_fee_per_share


def test_peak_at_half_and_zero_at_extremes():
    assert abs(taker_fee_per_share(0.5, 0.072) - 0.018) < 1e-12
    assert taker_fee_per_share(0.0) == 0.0
    assert taker_fee_per_share(1.0) == 0.0


def test_symmetric_and_scales_with_shares():
    assert abs(taker_fee_per_share(0.3) - taker_fee_per_share(0.7)) < 1e-12
    assert abs(taker_fee(100, 0.5, 0.072) - 1.8) < 1e-9


def test_deep_favorites_are_cheap_to_take():
    # the surviving taker edge: near-certain outcomes carry near-zero fee
    assert taker_fee_per_share(0.97) < 0.0025
    assert breakeven_edge(0.97) < breakeven_edge(0.6)


def test_out_of_range_prices_clamped():
    assert taker_fee_per_share(-0.2) == 0.0
    assert taker_fee_per_share(1.7) == 0.0
