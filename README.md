# Stock Analyst

A personal tool that tracks global stock prices and recommends buy, hold, or sell
with plain-English reasons.

Planned steps:

1. **Price data and watchlist**: a configurable watchlist of global
   tickers, daily prices from Yahoo Finance via `yfinance`, stored in SQLite.
2. **Buy/hold/sell signals with reasons** (trend, momentum, and valuation/growth for US stocks).
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

### Financial Datasets API (US prices and fundamentals)

The app can pull data straight from [Financial Datasets](https://financialdatasets.ai)
("Financial Data API for AI Agents"). Create an API key on their site, then set it in your
shell before fetching. The key is only read from the environment, never saved:

```bash
export FINANCIAL_DATASETS_API_KEY=your-key      # Windows PowerShell: $env:FINANCIAL_DATASETS_API_KEY="your-key"
stock-analyst fetch                             # auto: Financial Datasets for US tickers, Yahoo for the rest
stock-analyst fetch --source financialdatasets  # same routing, but stop with an error if the key is missing
stock-analyst fetch --source yfinance           # Yahoo Finance only
```

Financial Datasets covers US-listed tickers, so symbols with an exchange suffix (`SHEL.L`,
`7203.T`, `RELIANCE.NS`) keep coming from Yahoo Finance. For each US ticker, `fetch` also
saves a fundamentals snapshot (P/E, PEG, P/B, EV/EBITDA, margins, return on equity,
debt-to-equity, revenue and earnings growth) in the `fundamentals` table. A failed fundamentals
request is reported but never blocks the price update.

### More data sources: EODHD and Alpha Vantage (non-US markets)

Yahoo Finance is free but unofficial and sometimes breaks. Two keyed sources cover the
exchanges it is most needed for. Set whichever keys you have; `stock-analyst fetch` (auto)
uses every source with a key, and each symbol goes to the first source that covers it. If
that source fails, the next one is tried, so one outage never empties a watchlist row.

Order in auto mode: Financial Datasets (US only) → EODHD → Yahoo Finance → Alpha Vantage.

| Source | Key variable | Covers | Free plan |
|---|---|---|---|
| [EODHD](https://eodhd.com) | `EODHD_API_KEY` | US, London, XETRA, Frankfurt, Amsterdam, Paris, Milan, Madrid, Tokyo, Hong Kong, NSE, BSE, Johannesburg, Toronto, ASX, SIX, Korea, Shanghai, Shenzhen | 20 calls/day, last year of prices |
| [Alpha Vantage](https://www.alphavantage.co) | `ALPHA_VANTAGE_API_KEY` | US, London, XETRA, Toronto, BSE, Shanghai, Shenzhen (not Tokyo, Hong Kong, NSE or Johannesburg) | ~25 calls/day, last 100 days |

```bash
export EODHD_API_KEY=your-key
export EODHD_FUNDAMENTALS=1                # optional, needs an EODHD plan with fundamentals
stock-analyst fetch                        # auto: every configured source, with fallback
stock-analyst fetch --source eodhd         # EODHD first, then Yahoo; error if the key is missing
stock-analyst fetch --source alphavantage  # Alpha Vantage first, then Yahoo
```

Keep writing symbols in Yahoo format; they are translated per source (`SHEL.L` becomes
`SHEL.LSE` for EODHD and `SHEL.LON` for Alpha Vantage). London prices are in pence (`GBp`)
and Johannesburg prices in cents (`ZAc`), as on Yahoo.

With `EODHD_FUNDAMENTALS=1`, non-US stocks get the same valuation and growth rules as US
stocks (P/E, PEG, revenue growth, margins, return on equity). It is off by default because
each fundamentals request spends quota and the free plan does not include them. The report
footer names the providers that supplied the stored data.

### How the call is made

`stock_analyst/signals.py` runs four rules on daily closes. Each votes +1 (buy), 0, or -1 (sell):

| Rule | +1 | -1 |
|---|---|---|
| Long-term trend | price above its 200-day average | below it |
| Medium-term trend | 50-day average above the 200-day | below it |
| Momentum | up 10%+ over three months | down 10%+ |
| RSI (14 days) | below 30 (oversold) | above 70 (overbought) |

When a fundamentals snapshot is stored (US stocks fetched from Financial Datasets), two more
rules vote:

| Rule | +1 | -1 |
|---|---|---|
| Valuation | PEG at or below 1 (or, without a PEG, P/E at or below 15) | PEG 2.5+ (or P/E 40+), or negative earnings |
| Growth | revenue up 10%+ on a year ago | revenue shrinking |

Margins, return on equity, debt and free-cash-flow yield are listed as context without a vote.

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
`securities(symbol, name, currency, source, updated_at)`, `fundamentals(symbol, metrics JSON, source, updated_at)`, and `fetch_log`. Read fundamentals with `PriceStore.get_fundamentals(symbol)`.
Prices are in each stock's own trading currency; dates are exchange-local trading days.

## Tests

```bash
pytest
```
