"""Value bot: Kelly sizing, open/settle cycle, caps, persistence (no network)."""
import json
from datetime import datetime, timedelta, timezone

from wealth.live.journal import Journal
from wealth.polymarket.config import ValueBotConfig
from wealth.polymarket.scanner import parse_candidate
from wealth.polymarket.value_bot import ValueBot, kelly_stake

NOW = datetime(2026, 7, 3, tzinfo=timezone.utc)


def _raw(slug="will-x-happen", **overrides):
    m = {
        "slug": slug,
        "question": "Will X happen?",
        "outcomes": json.dumps(["Yes", "No"]),
        "clobTokenIds": json.dumps(["101", "202"]),
        "bestBid": 0.94,
        "bestAsk": 0.95,
        "volumeNum": 50_000,
        "liquidityNum": 5_000,
        "endDate": (NOW + timedelta(days=5)).isoformat(),
        "closed": False,
        "active": True,
    }
    m.update(overrides)
    return m


class StubScanner:
    """Serves canned candidates and market states without any network."""

    def __init__(self, cfg, raws):
        self.cfg = cfg
        self.raws = {r["slug"]: r for r in raws}

    def scan(self):
        return [c for c in (parse_candidate(r, self.cfg, now=NOW)
                            for r in self.raws.values()) if c]

    def market_by_slug(self, slug):
        return self.raws.get(slug)

    def resolve(self, slug, winner_index):
        prices = ["0", "0"]
        prices[winner_index] = "1"
        self.raws[slug]["closed"] = True
        self.raws[slug]["outcomePrices"] = json.dumps(prices)

    def close(self):
        pass


def _bot(tmp_path, raws, **cfg_kw):
    cfg = ValueBotConfig(state_dir=str(tmp_path), **cfg_kw)
    scanner = StubScanner(cfg, raws)
    bot = ValueBot(cfg, Journal(cfg.journal_path), scanner=scanner, verbose=False)
    return bot, scanner


def test_kelly_stake_caps_and_zero_edge():
    cfg = ValueBotConfig(kelly_fraction=0.25, max_stake_per_market=25, min_stake=2)
    # p_true == ask -> no edge -> no stake
    assert kelly_stake(1000, 0.95, 0.95, cfg) == 0.0
    # negative edge -> zero
    assert kelly_stake(1000, 0.90, 0.95, cfg) == 0.0
    # real edge: f* = (0.97-0.95)/0.05 = 0.4 -> 0.25*0.4*1000 = 100 -> capped at 25
    assert kelly_stake(1000, 0.97, 0.95, cfg) == 25.0
    # tiny bankroll -> below min stake -> skip
    assert kelly_stake(10, 0.97, 0.95, cfg) == 0.0


def test_open_then_win_settles_with_profit(tmp_path):
    bot, scanner = _bot(tmp_path, [_raw()])
    bot.run_once()
    assert len(bot.positions) == 1
    pos = next(iter(bot.positions.values()))
    assert pos.entry_price == 0.95 and pos.stake > 0

    scanner.resolve("will-x-happen", winner_index=0)
    bot.run_once()
    assert not bot.positions
    settles = [r for r in bot.journal.records() if r["event"] == "value_settle"]
    assert len(settles) == 1 and settles[0]["won"]
    assert settles[0]["pnl"] > 0
    # payout = shares = stake / 0.95 > stake
    assert abs(bot.cash - (bot.cfg.cash - pos.stake + pos.shares)) < 1e-9


def test_open_then_loss_pays_zero(tmp_path):
    bot, scanner = _bot(tmp_path, [_raw()])
    bot.run_once()
    stake = next(iter(bot.positions.values())).stake
    scanner.resolve("will-x-happen", winner_index=1)
    bot.run_once()
    settles = [r for r in bot.journal.records() if r["event"] == "value_settle"]
    assert not settles[0]["won"]
    assert abs(settles[0]["pnl"] + stake) < 1e-9
    assert abs(bot.cash - (bot.cfg.cash - stake)) < 1e-9


def test_no_rebuy_of_held_slug(tmp_path):
    bot, _ = _bot(tmp_path, [_raw()])
    bot.run_once()
    bot.run_once()  # same candidate still in scan results
    assert len(bot.positions) == 1
    opens = [r for r in bot.journal.records() if r["event"] == "value_open"]
    assert len(opens) == 1


def test_exposure_and_position_caps(tmp_path):
    raws = [_raw(slug=f"market-{i}") for i in range(10)]
    bot, _ = _bot(tmp_path, raws, max_positions=3,
                  max_total_exposure=40.0, max_stake_per_market=25.0)
    bot.run_once()
    assert len(bot.positions) <= 3
    assert bot.exposure <= 40.0 + 1e-9


def test_state_round_trips(tmp_path):
    bot, _ = _bot(tmp_path, [_raw()])
    bot.run_once()
    cash, positions = bot.cash, dict(bot.positions)

    # a fresh bot instance reloads the same state from disk
    cfg = bot.cfg
    bot2 = ValueBot(cfg, Journal(cfg.journal_path),
                    scanner=StubScanner(cfg, [_raw()]), verbose=False)
    assert bot2.cash == cash
    assert set(bot2.positions) == set(positions)
    assert bot2.positions["will-x-happen"].stake == positions["will-x-happen"].stake


def test_equity_tick_journaled(tmp_path):
    bot, _ = _bot(tmp_path, [_raw()])
    bot.run_once()
    ticks = [r for r in bot.journal.records() if r["event"] == "tick"]
    assert ticks and abs(float(ticks[-1]["equity"]) - bot.equity) < 1e-6
