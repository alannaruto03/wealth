"""Fair-value model: digital pricing limits, monotonicity, vol estimation."""
import math

from wealth.polymarket.model import VolEstimator, fair_up_probability


def test_at_the_money_is_half():
    assert abs(fair_up_probability(100.0, 100.0, 1e-4, 600) - 0.5) < 1e-9


def test_expiry_limits_and_tie_goes_up():
    assert fair_up_probability(101.0, 100.0, 1e-4, 0) == 1.0
    assert fair_up_probability(99.0, 100.0, 1e-4, 0) == 0.0
    assert fair_up_probability(100.0, 100.0, 1e-4, 0) == 1.0  # tie -> UP


def test_monotone_in_spot_and_time():
    p_lo = fair_up_probability(99.9, 100.0, 1e-4, 300)
    p_hi = fair_up_probability(100.1, 100.0, 1e-4, 300)
    assert p_lo < 0.5 < p_hi
    # same distance, less time -> more certain
    p_short = fair_up_probability(100.1, 100.0, 1e-4, 30)
    assert p_short > p_hi


def test_more_vol_pulls_toward_half():
    p_calm = fair_up_probability(100.1, 100.0, 5e-5, 300)
    p_wild = fair_up_probability(100.1, 100.0, 5e-4, 300)
    assert 0.5 < p_wild < p_calm


def test_vol_estimator_converges():
    est = VolEstimator(halflife_s=10, seed=1e-4, floor=1e-6)
    # synthetic 1s ticks with constant 5bp absolute moves -> vol ~5e-4
    px, ts = 100.0, 0.0
    for i in range(600):
        ts += 1.0
        px *= math.exp(5e-4 if i % 2 == 0 else -5e-4)
        est.update(ts, px)
    assert 3e-4 < est.sigma_s < 7e-4


def test_vol_estimator_respects_floor_and_bad_input():
    est = VolEstimator(halflife_s=10, seed=1e-4, floor=1e-3)
    est.update(1.0, 100.0)
    est.update(1.0, 100.0)   # dt == 0 ignored
    est.update(2.0, -5.0)    # bad price ignored
    assert est.sigma_s >= 1e-3
