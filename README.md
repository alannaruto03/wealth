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
