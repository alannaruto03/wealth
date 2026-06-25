"""Data-layer tests using mocks/stubs — no network."""
import pandas as pd
import pytest

from wealth.data.base import DataProvider, OHLCV_COLUMNS, get_provider
from wealth.data.cache import CachedProvider


def idx(n):
    return pd.date_range("2021-01-01", periods=n, freq="D")


class StubProvider(DataProvider):
    """In-memory provider that counts how often it is hit."""
    def __init__(self):
        self.calls = 0

    def fetch_ohlcv(self, symbols, start=None, end=None, timeframe="1d"):
        self.calls += 1
        out = {}
        for s in symbols:
            close = pd.Series(range(1, 11), index=idx(10), dtype=float)
            out[s] = pd.DataFrame(
                {"open": close, "high": close, "low": close, "close": close, "volume": 1.0}
            )
        return out


def test_normalize_schema():
    raw = pd.DataFrame(
        {"Open": [1.0], "High": [2.0], "Low": [0.5], "Close": [1.5], "Volume": [10]},
        index=pd.to_datetime(["2021-01-01"]),
    )
    norm = DataProvider._normalize(raw)
    assert list(norm.columns) == OHLCV_COLUMNS
    assert isinstance(norm.index, pd.DatetimeIndex)


def test_normalize_strips_timezone():
    raw = pd.DataFrame(
        {"close": [1.0, 2.0]},
        index=pd.to_datetime(["2021-01-01", "2021-01-02"]).tz_localize("UTC"),
    )
    norm = DataProvider._normalize(raw)
    assert norm.index.tz is None


def test_latest_price_uses_last_close():
    stub = StubProvider()
    assert stub.latest_price("BTC/USDT") == pytest.approx(10.0)


def test_cache_round_trip(tmp_path):
    stub = StubProvider()
    cached = CachedProvider(stub, cache_dir=str(tmp_path), market="crypto")
    a = cached.fetch_ohlcv(["BTC/USDT"], start="2021-01-01", end="2021-01-10")
    b = cached.fetch_ohlcv(["BTC/USDT"], start="2021-01-01", end="2021-01-10")
    # Second call served from parquet -> inner provider hit only once.
    assert stub.calls == 1
    assert a["BTC/USDT"].equals(b["BTC/USDT"])


def test_cache_latest_price_not_cached():
    stub = StubProvider()
    cached = CachedProvider(stub, cache_dir="data_cache", market="crypto")
    cached.latest_price("ETH/USDT")
    cached.latest_price("ETH/USDT")
    assert stub.calls == 2  # latest_price always goes live


def test_get_provider_unknown_market():
    with pytest.raises(ValueError):
        get_provider("forex")
