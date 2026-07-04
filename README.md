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

## Run it on Windows — dashboard on your PC and phone

Everything runs on your PC (the bot needs open internet access to Polymarket
and Binance). One-time setup, then two terminals.

**1. Install (once).** Install Python 3.11+ from [python.org](https://www.python.org/downloads/)
— tick **"Add python.exe to PATH"** in the installer. Then in PowerShell:

```powershell
git clone https://github.com/alannaruto03/wealth.git
cd wealth
git checkout claude/polymarket-quant-bot-58mk6c   # until merged to main
pip install -e ".[dashboard]"
```

**2. Sanity check** — can the bot see the markets from your network?

```powershell
wealth polymarket discover --config configs/polymarket.yaml
```

You should see the current hourly + 15m BTC markets with books and fair
values. If discovery finds nothing, the market slug format may have changed —
this command is the debugging tool for that.

**3. Terminal 1 — run the bot** (paper money, real order books):

```powershell
wealth polymarket run --config configs/polymarket.yaml
```

**4. Terminal 2 — run the dashboard, reachable from your phone:**

```powershell
wealth dashboard --config configs/polymarket.yaml --address 0.0.0.0
```

It prints two URLs: `http://localhost:8501` for the PC and
`http://<your-LAN-IP>:8501` for the phone. When Windows Firewall pops up,
click **Allow** (private networks). On your phone — **same Wi-Fi as the PC** —
open the phone URL in the browser. If you missed the firewall prompt:

```powershell
netsh advfirewall firewall add rule name="wealth dashboard" dir=in action=allow protocol=TCP localport=8501
```

Notes:
- Both terminals must stay open; closing them stops the bot / dashboard.
- If the PC sleeps, the bot pauses. Keep it awake while trading:
  `powercfg /change standby-timeout-ac 0`
- All state lives in `state\polymarket\` (journal + positions). Delete that
  folder to start a fresh paper run.
- The dashboard's Polymarket tab shows equity, win rate, resolved markets, and
  risk blocks; hit **Refresh** in the sidebar to pull the latest journal.
- Phone access is LAN-only by design (nothing is exposed to the internet). If
  you later want access from anywhere, run [Tailscale](https://tailscale.com)
  on PC + phone and use the PC's Tailscale IP instead — don't put the
  dashboard on a public tunnel; it can edit the bot's config.

## View it from anywhere (Vercel)

The bot itself must keep running on your PC — Vercel can't host long-running
processes. What Vercel hosts (free) is `web/index.html`: a fast, mobile-first
**read-only live view**. The bot pushes a JSON snapshot (equity, win rate,
positions, resolutions) to a secret GitHub gist every minute; the page reads
it. Works even when the PC dashboard is closed — only the bot must be running.

**One-time setup:**

1. **GitHub token** (lets the bot update its gist): github.com → Settings →
   Developer settings → Fine-grained tokens → Generate; give it **only**
   Account permissions → Gists → Read and write. Then in PowerShell:

   ```powershell
   setx WEALTH_PUBLISH_TOKEN "github_pat_XXXX"
   ```
   (open a new terminal afterwards so the variable is picked up)

2. **Turn publishing on** in `configs/polymarket.yaml`:

   ```yaml
   publish: true
   ```

3. **Start the bot** — on the first publish it creates the gist and prints:
   `live view feed created: gist <id> — open your static page with ?gist=<id>`.
   (Or run `wealth polymarket publish --config configs/polymarket.yaml` for a
   one-shot test.)

4. **Deploy the page to Vercel** (~3 minutes): [vercel.com](https://vercel.com)
   → sign in with GitHub → **Add New… → Project** → Import this repo → set
   **Root Directory** to `web` → Deploy. No build settings needed.

5. On your phone, open and bookmark:

   ```
   https://<your-project>.vercel.app/?gist=<gist-id>
   ```
   The page remembers the gist id, auto-refreshes every 60s, and shows a STALE
   badge if the bot stops publishing.

Privacy: the gist is unlisted but anyone with the link can *view* the stats
(read-only — no keys, no controls). Delete the gist to rotate access; the bot
creates a fresh one on the next run. GitHub Pages can host `web/` identically
if you prefer it over Vercel.

## Polymarket BTC Up/Down bot

A second bot lives in `wealth/polymarket/`: it trades Polymarket's short-term
Bitcoin **"Up or Down"** prediction markets (hourly and 15-minute series). The
strategy is the classic short-window mispricing loop:

1. Estimate a **fair probability** that BTC finishes the period up — a
   digital-option model driven by live spot (via ccxt) and EWMA realized vol.
2. When the CLOB ask deviates from fair value by more than an edge threshold,
   **cross the spread** before the book readjusts.
3. Size with **fractional Kelly**, capped per trade / per market / total.
4. Hold to resolution (or take profit), settle at $1/$0, repeat.

```bash
# what would the bot see right now? (read-only; also verifies market discovery)
wealth polymarket discover --config configs/polymarket.yaml

# paper-trade against REAL live order books (no wallet, no keys)
wealth polymarket run --config configs/polymarket.yaml --once
wealth polymarket run --config configs/polymarket.yaml

# record books while running (or standalone), then replay = honest backtest
wealth polymarket record --config configs/polymarket.yaml
wealth polymarket replay --config configs/polymarket.yaml \
  --file state/polymarket/recordings/books.jsonl

# track record
wealth polymarket report --config configs/polymarket.yaml
wealth dashboard --config configs/polymarket.yaml   # same dashboard, same journal format
```

Risk controls built in: per-trade/per-market/total exposure caps, a **daily
loss kill-switch** (entries blocked for the rest of the UTC day), spread/depth/
staleness guards, and a no-entry window just before expiry.

### Going live on Polymarket

Paper first — collect a real multi-week track record. Then:

```yaml
# configs/polymarket.yaml
mode: live
funder: 0xYourPolymarketProxyWallet
```

```bash
pip install -e '.[polymarket]'                 # py-clob-client
export POLYMARKET_PRIVATE_KEY=0x...            # never in a config file
wealth polymarket run --config configs/polymarket.yaml   # asks you to type LIVE
```

v1 live caveats: winning shares must be redeemed on-chain via the Polymarket
UI (the bot books the expected payout locally), and positions are tracked
locally — reconcile against your Polymarket account.

### Honest expectations (please read)

This bot exists because of viral posts claiming huge PnL from this exact
setup. Those claims are **unverifiable marketing**; treat them as such.
Structurally, the odds are against you: paper fills against book snapshots are
an **optimistic upper bound** (no queue, no latency, no adverse selection —
the fastest players you're racing do this with colocated infrastructure), the
fair-value model is deliberately simple, and Polymarket resolves against its
own price source which can differ from your feed by a few dollars right at
the boundary. Expect the paper edge to shrink or vanish live. That is the
point of paper-first: reject the idea cheaply if the track record says no.

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
  polymarket/  BTC up/down bot: gamma/clob clients, fair value, Kelly sizing,
               paper/live executors, runner, recorder/replay
  cli.py       backtest | run | tune | report | polymarket ...
```

## Disclaimer

This software is for research and education. It is **not** financial advice.
Trading involves substantial risk of loss. You are solely responsible for any
money you choose to put at risk.
