# Stock Analyst

A personal tool that tracks global stock prices and recommends buy, hold, or sell
with plain-English reasons.

Planned steps:

1. **Price data and watchlist** (this step): a configurable watchlist of global
   tickers, daily prices from Yahoo Finance via `yfinance`, stored in SQLite.
2. Buy/hold/sell signals with reasons (trend, momentum, valuation).
3. A one-page dashboard.
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
