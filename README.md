# Stock Analyst

A personal tool that tracks global stock prices and recommends buy, hold, or sell
with plain-English reasons.

Planned steps:

1. **Price data and watchlist**: a configurable watchlist of global
   tickers, daily prices from Yahoo Finance via `yfinance`, stored in SQLite.
2. **Buy/hold/sell signals with reasons** (trend and momentum; valuation later).
3. **A one-page dashboard** (`stock-analyst report`).
4. A scheduled daily refresh.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Usage

Edit `watchlist.toml` to choose your stocks. Symbols use Yahoo Finance format,
with an exchange suffix for non-US listings (`SHEL.L`, `7203.T`, `0700.HK`,
`RELIANCE.NS`, `NPN.JO`).

```bash
stock-analyst fetch          # first run loads ~2 years; later runs fetch only new days
stock-analyst prices         # latest close and daily change per stock
stock-analyst show AAPL      # recent daily bars for one symbol
stock-analyst fetch --full   # re-download full history
stock-analyst signals        # buy / hold / sell call with reasons per stock
stock-analyst report         # write report.html: watchlist, charts, calls and reasons
```

### How the call is made

`stock_analyst/signals.py` runs four rules on daily closes. Each votes +1 (buy), 0, or -1 (sell):

| Rule | +1 | -1 |
|---|---|---|
| Long-term trend | price above its 200-day average | below it |
| Medium-term trend | 50-day average above the 200-day | below it |
| Momentum | up 10%+ over three months | down 10%+ |
| RSI (14 days) | below 30 (oversold) | above 70 (overbought) |

A total of +2 or more is **BUY**, -2 or less is **SELL**, anything else is **HOLD**. Rules
without enough history are skipped (the 200-day rules need 200 trading days). Every call
lists the reasons behind it. This is a technical screen for personal research, not advice.

### Demo

`demo/snapshot.json` holds one year of daily closes for the watchlist, fetched on
2026-10-06 from Twelve Data and Alpha Vantage (Yahoo Finance was unreachable from the build
environment). Non-US names use their US listings (ADRs, USD) where the free data plan did not
cover the home exchange; Reliance uses its BSE listing (INR, 100 days). Rebuild the demo page with:

```bash
stock-analyst report --snapshot demo/snapshot.json -o demo/stock-analyst-demo.html
```

Prices are stored in `data/prices.db` (git-ignored). A symbol that fails to download
is reported and skipped; the rest still update.

## Data layout (for later steps)

Read prices through `stock_analyst.store.PriceStore`:

- `get_prices(symbol, start=None, end=None)`: daily bars indexed by date, oldest first,
  with `open, high, low, close, adj_close, volume`.
- `latest_closes()`: latest and previous close for every symbol.
- `securities()`: name, trading currency, and coverage per symbol.

Tables: `prices(symbol, date, open, high, low, close, adj_close, volume)`,
`securities(symbol, name, currency, source, updated_at)`, and `fetch_log`.
Prices are in each stock's own trading currency; dates are exchange-local trading days.

## Tests

```bash
pytest
```
