# wealth — paper-first, self-tuning automated trading bot

A hands-off, systematic trading bot built on top of an **honest backtesting
engine**. You pick a robust, few-parameter strategy; the bot runs it on a
schedule, journals every decision, and can be re-tuned to its own performance
the *safe* way (walk-forward / out-of-sample) — without curve-fitting.

> **It starts on paper money.** Live prices, fake money, zero financial risk.
> Going live is a deliberate one-line config change *you* make after a real
> paper track record. There is no real money at stake until you opt in.

## What it is (and isn't)

- ✅ A measurement-first framework: no lookahead bias, real commission +
  slippage, comparable metrics across strategies.
- ✅ An automated runner that reuses the *exact same* strategy code in backtest
  and live, so live behaviour matches what you tested.
- ✅ A safe self-tuning loop (walk-forward) that rejects parameters which only
  look good in-sample.
- ❌ Not a money printer. Not a price predictor. Not get-rich-quick.

### Honest expectations

The built-in strategies (trend-following, dual momentum) historically returned
roughly **8–15%/yr with 20–30% drawdowns** — *when the edge held*. Live returns
are typically **below** backtest (regime change, costs, slippage, overfitting).
Most retail traders lose money. The value here is disciplined, emotion-free
execution and honest measurement, so you reject bad ideas cheaply.

## Install

```bash
pip install -r requirements.txt
pip install -e .        # exposes the `wealth` command
```

## Quickstart

```bash
# 1. Prove a strategy on history
wealth backtest --market crypto --symbols BTC/USDT,ETH/USDT \
  --strategy trend_breakout --start 2019-01-01 --end 2024-12-31 --out reports/tb

# 2. Run the bot hands-off (paper by default), from configs/bot.yaml
wealth run --config configs/bot.yaml --once   # single tick (cron/systemd)
wealth run --config configs/bot.yaml          # loop on schedule (Ctrl-C safe)

# 3. Re-tune to performance, safely (walk-forward, out-of-sample)
wealth tune --config configs/bot.yaml --metric calmar --out reports/tune

# 4. See how the paper bot is doing
wealth report --config configs/bot.yaml

# 5. Open the dashboard (beautiful, interactive)
wealth dashboard --config configs/bot.yaml
```

## Dashboard

A dark, interactive Streamlit + Plotly dashboard to monitor *and* drive the bot.

```bash
pip install -e '.[dashboard]'           # one-time: installs streamlit + plotly
wealth dashboard --config configs/bot.yaml
# or directly:  streamlit run wealth/dashboard/app.py
```

Five tabs:

- **Overview** — hero KPIs (equity, return, CAGR, Sharpe, max drawdown, win
  rate), equity curve with drawdown, live allocation, open positions.
- **Live bot** — equity from the journal, positions + unrealized PnL, allocation
  donut, recent orders, and a **Step one tick** button (paper by default).
- **Backtest** — pick market/symbols/strategy/params/dates/costs, then see
  strategy vs buy-&-hold, drawdown, a monthly-returns heatmap, and trades.
- **Tuning** — run walk-forward and view in- vs out-of-sample scores per fold,
  the ACCEPTED/REJECTED verdict, and recommended params.
- **Config** — view/edit the YAML with a paper-vs-live safety gate.

> **Demo data** toggle (sidebar, on by default until a journal exists) populates
> every panel with synthetic data so the dashboard is fully usable offline.
> Live data fetch and the *Step one tick* button need market access.

## Polymarket 15-minute crypto bot

An event-driven bot for Polymarket's recurring **"Bitcoin Up or Down"
15-minute markets** (`wealth/polymarket/`). Paper mode simulates fills against
the **live** order book and settles on the **real** market outcome — real
data, fake money.

```bash
wealth poly run --config configs/polymarket.yaml --windows 4   # paper, 4 windows
wealth poly report --config configs/polymarket.yaml            # PnL per window
```

What it does (the strategy archetype the consistently profitable accounts on
these markets use, distilled):

- **Fair value** — prices UP as a digital option: `P(settle >= strike)` from
  Binance spot distance-to-strike and EWMA short-horizon realized vol.
- **Maker quoting** — rests bids on *both* outcome tokens around fair value
  (makers pay no fee and earn rebates); leans quotes away from inventory.
- **Quote pulling** — cancels everything on a spot impulse before requoting
  (stale quotes against faster flow are how makers bleed).
- **Complete-set capture** — buys YES+NO when the asks sum to under $1:
  locked profit at settlement, regardless of outcome.
- **Selective taking** — crosses the spread only when model edge beats the
  `p·(1−p)` taker fee curve (post-Jan-2026 this mostly means deep favorites
  near expiry) plus a margin.
- **Hard risk limits** — per-side inventory caps, per-window notional budget,
  a daily-loss kill switch, a stale-feed circuit breaker, and no maker quotes
  in the final seconds of a window.

### Honest expectations (read this)

The "free money" era on these markets is **over**: Polymarket added dynamic
taker fees in Jan 2026 specifically to kill latency arbitrage, and rebuilt
its exchange (CLOB V2 + pUSD) in Apr 2026. The accounts still profiting run
24/7 low-latency infrastructure and months of tuning; a Python bot polling
over HTTP will be slower to pull quotes and **will** suffer adverse
selection. That is exactly why this bot is paper-first: run it for days,
read `wealth poly report`, and only consider real money if the *measured*
track record says so. Expect small numbers — possibly negative. Nothing here
is financial advice, and no profitability is promised.

Going live (much later, your call): `mode: live` in the config,
`pip install -e '.[polymarket-live]'`, `WEALTH_POLY_PK` in a local `.env`,
a funded account, and a jurisdiction Polymarket serves — the live adapter is
experimental and deliberately refuses to start from the CLI until wired in
by hand (see `wealth/polymarket/live.py`).

Network note: the bot needs outbound access to `gamma-api.polymarket.com`,
`clob.polymarket.com`, `api.binance.com` and (preferred, for lower latency)
the websocket hosts `ws-subscriptions-clob.polymarket.com`,
`ws-live-data.polymarket.com`, `stream.binance.com`.

## Strategies

| name             | idea                                                       |
|------------------|------------------------------------------------------------|
| `trend_breakout` | Donchian channel: long on N-day high breakout, exit on low |
| `dual_momentum`  | Antonacci: hold the top-momentum asset, else cash          |
| `mean_reversion` | z-score reversion: buy dips, exit at the mean              |
| `grid`           | crypto grid trading *(stub)*                                |
| `funding_arb`    | cash-and-carry funding arbitrage *(stub)*                  |

## Going live (your decision, later)

Edit `configs/bot.yaml`:

```yaml
broker: ccxt      # or alpaca for stocks
mode: live        # was: paper
```

Put API keys in a local `.env` (never committed). For crypto, test against the
exchange **testnet** first (`mode: paper` with `broker: ccxt` uses sandbox).

## Layout

```
wealth/
  data/        price data: yfinance (stocks), ccxt (crypto), parquet cache
  strategies/  Strategy ABC + registry + concrete strategies
  engine/      vectorized backtester, portfolio accounting, cost models
  metrics/     CAGR, Sharpe, Sortino, max drawdown, win rate, exposure
  broker/      execution: paper (default), ccxt, alpaca
  live/        runner (one tick), scheduler (cadence), journal (track record)
  tuning/      walk-forward optimizer (out-of-sample, anti-overfit)
  reporting/   metrics tables + equity/drawdown plots
  cli.py       backtest | run | tune | report
```

## Disclaimer

This software is for research and education. It is **not** financial advice.
Trading involves substantial risk of loss. You are solely responsible for any
money you choose to put at risk.
