import math

import numpy as np
import pandas as pd
import pytest

from wealth.polymarket.fair_value import (
    MAX_PROB,
    MIN_PROB,
    GaussianDigitalModel,
    VolEstimator,
    get_model,
)

SIGMA = 0.0005  # per sqrt-second


def test_registry_resolves():
    model = get_model("gaussian_digital")
    assert isinstance(model, GaussianDigitalModel)
    with pytest.raises(ValueError):
        get_model("nope")


def test_up_down_symmetry():
    m = GaussianDigitalModel()
    p_up = m.prob_up(100.0 * 1.001, 100.0, 600, SIGMA)
    p_down_mirror = m.prob_up(100.0 / 1.001, 100.0, 600, SIGMA)
    assert p_up + p_down_mirror == pytest.approx(1.0, abs=1e-9)


def test_at_the_open_is_half():
    m = GaussianDigitalModel()
    assert m.prob_up(100.0, 100.0, 600, SIGMA) == pytest.approx(0.5)


def test_monotone_in_spot():
    m = GaussianDigitalModel()
    spots = [99.0, 99.5, 100.0, 100.5, 101.0]
    probs = [m.prob_up(s, 100.0, 600, SIGMA) for s in spots]
    assert probs == sorted(probs)
    assert probs[0] < 0.5 < probs[-1]


def test_converges_to_indicator_near_expiry():
    m = GaussianDigitalModel()
    assert m.prob_up(100.2, 100.0, 0.5, SIGMA) > 0.95
    assert m.prob_up(99.8, 100.0, 0.5, SIGMA) < 0.05
    # exactly expired
    assert m.prob_up(100.2, 100.0, 0.0, SIGMA) == MAX_PROB
    assert m.prob_up(99.8, 100.0, 0.0, SIGMA) == MIN_PROB
    assert m.prob_up(100.0, 100.0, 0.0, SIGMA) == 0.5


def test_zero_sigma_is_indicator():
    m = GaussianDigitalModel()
    assert m.prob_up(100.1, 100.0, 600, 0.0) == MAX_PROB
    assert m.prob_up(99.9, 100.0, 600, 0.0) == MIN_PROB


def test_clamped_to_price_grid():
    m = GaussianDigitalModel()
    assert m.prob_up(200.0, 100.0, 600, SIGMA) == MAX_PROB
    assert m.prob_up(50.0, 100.0, 600, SIGMA) == MIN_PROB


def test_rejects_bad_inputs():
    m = GaussianDigitalModel()
    with pytest.raises(ValueError):
        m.prob_up(-1.0, 100.0, 600, SIGMA)


def test_vol_estimator_recovers_constant_vol():
    rng = np.random.default_rng(7)
    sigma_1m = 0.001  # stdev of 1m log returns
    rets = rng.normal(0.0, sigma_1m, size=2000)
    closes = pd.Series(100.0 * np.exp(np.cumsum(rets)))
    est = VolEstimator(halflife_s=3600.0, lookback_min=2000)
    sigma = est.update(closes)
    expected = sigma_1m / math.sqrt(60.0)
    assert sigma == pytest.approx(expected, rel=0.15)


def test_vol_estimator_too_short_returns_none():
    est = VolEstimator()
    assert est.update(pd.Series([100.0, 100.1])) is None
