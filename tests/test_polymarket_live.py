"""LiveExecutor arg-mapping against a mocked py_clob_client + the safety gate.

No network, no real keys: the fake modules capture what would be sent.
"""
import sys
import types

import pytest

from wealth.polymarket.config import PolymarketConfig
from wealth.polymarket.gamma import MarketInfo


def make_market(neg_risk=False):
    return MarketInfo(slug="m-1", condition_id="c", question="q",
                      token_id_up="UP1", token_id_down="DN1",
                      start_ts=0.0, end_ts=900.0, series="15m", neg_risk=neg_risk)


class FakeClobClient:
    instances = []

    def __init__(self, host, key=None, chain_id=None, signature_type=None, funder=None):
        self.init = dict(host=host, key=key, chain_id=chain_id,
                         signature_type=signature_type, funder=funder)
        self.posted = []
        self.creds_set = False
        FakeClobClient.instances.append(self)

    def create_or_derive_api_creds(self):
        return {"apiKey": "k"}

    def set_api_creds(self, creds):
        self.creds_set = True

    def create_order(self, order_args, options=None):
        return {"signed": order_args, "options": options}

    def post_order(self, signed, order_type):
        self.posted.append((signed, order_type))
        args = signed["signed"]
        if args.side == "BUY":  # taker gets tokens, maker pays USDC
            taking, making = args.size, args.size * args.price
        else:  # SELL: maker gives tokens, taker receives USDC
            taking, making = args.size * args.price, args.size
        return {"status": "matched", "success": True,
                "takingAmount": str(taking), "makingAmount": str(making)}


@pytest.fixture
def fake_py_clob(monkeypatch, tmp_path):
    """Install a fake py_clob_client package and yield the module handles."""
    FakeClobClient.instances = []

    class OrderArgs:
        def __init__(self, token_id, price, size, side):
            self.token_id, self.price, self.size, self.side = token_id, price, size, side

    class PartialCreateOrderOptions:
        def __init__(self, neg_risk=False):
            self.neg_risk = neg_risk

    class OrderType:
        FOK = "FOK"
        GTC = "GTC"

    root = types.ModuleType("py_clob_client")
    client_mod = types.ModuleType("py_clob_client.client")
    client_mod.ClobClient = FakeClobClient
    types_mod = types.ModuleType("py_clob_client.clob_types")
    types_mod.OrderArgs = OrderArgs
    types_mod.OrderType = OrderType
    types_mod.PartialCreateOrderOptions = PartialCreateOrderOptions
    ob_mod = types.ModuleType("py_clob_client.order_builder")
    const_mod = types.ModuleType("py_clob_client.order_builder.constants")
    const_mod.BUY = "BUY"
    const_mod.SELL = "SELL"

    for name, mod in [("py_clob_client", root),
                      ("py_clob_client.client", client_mod),
                      ("py_clob_client.clob_types", types_mod),
                      ("py_clob_client.order_builder", ob_mod),
                      ("py_clob_client.order_builder.constants", const_mod)]:
        monkeypatch.setitem(sys.modules, name, mod)
    monkeypatch.setenv("POLYMARKET_PRIVATE_KEY", "0xdeadbeef")
    return FakeClobClient


def make_cfg(tmp_path, **kw):
    defaults = dict(mode="live", cash=500.0, funder="0xfunder", signature_type=1,
                    order_type="FOK", state_dir=str(tmp_path / "state"))
    defaults.update(kw)
    return PolymarketConfig(**defaults)


def test_live_executor_init_and_buy(fake_py_clob, tmp_path):
    from wealth.polymarket.live import LiveExecutor

    ex = LiveExecutor(make_cfg(tmp_path))
    client = fake_py_clob.instances[-1]
    assert client.init["key"] == "0xdeadbeef"
    assert client.init["chain_id"] == 137
    assert client.init["funder"] == "0xfunder"
    assert client.creds_set

    fill = ex.buy(make_market(), "UP1", limit_price=0.52, size=10.0)
    assert fill.status == "filled"
    assert fill.size == pytest.approx(10.0)
    assert fill.avg_price == pytest.approx(0.52)
    signed, order_type = client.posted[0]
    assert order_type == "FOK"
    assert signed["signed"].token_id == "UP1"
    assert signed["signed"].side == "BUY"
    assert signed["options"] is None  # not a neg-risk market
    # local book tracks the fill
    assert ex.get_positions()["UP1"].size == pytest.approx(10.0)
    assert ex.get_cash() == pytest.approx(500.0 - 5.2)


def test_live_executor_neg_risk_passthrough(fake_py_clob, tmp_path):
    from wealth.polymarket.live import LiveExecutor

    ex = LiveExecutor(make_cfg(tmp_path))
    ex.buy(make_market(neg_risk=True), "UP1", 0.52, 10.0)
    signed, _ = fake_py_clob.instances[-1].posted[0]
    assert signed["options"].neg_risk is True


def test_live_executor_sell_and_settle(fake_py_clob, tmp_path):
    from wealth.polymarket.live import LiveExecutor

    ex = LiveExecutor(make_cfg(tmp_path))
    m = make_market()
    ex.buy(m, "UP1", 0.50, 10.0)
    fill = ex.sell(m, "UP1", 0.60, 4.0)
    assert fill.status == "filled"
    assert ex.get_positions()["UP1"].size == pytest.approx(6.0)
    pnl = ex.settle(m, winning_token_id="UP1")
    assert pnl == pytest.approx(6.0 * (1.0 - 0.50))
    assert ex.get_positions() == {}


def test_live_executor_requires_key(fake_py_clob, tmp_path, monkeypatch):
    from wealth.polymarket.live import LiveExecutor

    monkeypatch.delenv("POLYMARKET_PRIVATE_KEY", raising=False)
    with pytest.raises(ValueError, match="private key"):
        LiveExecutor(make_cfg(tmp_path))


def test_live_executor_rejected_order_no_local_change(fake_py_clob, tmp_path):
    from wealth.polymarket.live import LiveExecutor

    ex = LiveExecutor(make_cfg(tmp_path))
    client = fake_py_clob.instances[-1]
    client.post_order = lambda signed, order_type: {"errorMsg": "not enough balance"}
    fill = ex.buy(make_market(), "UP1", 0.52, 10.0)
    assert fill.status.startswith("rejected")
    assert ex.get_positions() == {}
    assert ex.get_cash() == pytest.approx(500.0)


# ------------------------------------------------------------------ safety gate


def test_confirm_live_paper_passes_without_prompt(tmp_path):
    from wealth.polymarket.cli import _confirm_live

    assert _confirm_live(PolymarketConfig(mode="paper"), assume_yes=False)


def test_confirm_live_flag_bypasses_prompt(tmp_path, capsys):
    from wealth.polymarket.cli import _confirm_live

    assert _confirm_live(PolymarketConfig(mode="live"), assume_yes=True)
    assert "real money" in capsys.readouterr().out


def test_confirm_live_noninteractive_refuses(tmp_path, monkeypatch):
    from wealth.polymarket.cli import _confirm_live

    monkeypatch.setattr("sys.stdin", types.SimpleNamespace(isatty=lambda: False))
    assert not _confirm_live(PolymarketConfig(mode="live"), assume_yes=False)


def test_confirm_live_requires_exact_word(tmp_path, monkeypatch):
    from wealth.polymarket.cli import _confirm_live

    monkeypatch.setattr("sys.stdin", types.SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr("builtins.input", lambda prompt="": "yes")
    assert not _confirm_live(PolymarketConfig(mode="live"), assume_yes=False)
    monkeypatch.setattr("builtins.input", lambda prompt="": "LIVE")
    assert _confirm_live(PolymarketConfig(mode="live"), assume_yes=False)