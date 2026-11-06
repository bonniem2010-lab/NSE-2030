"""Alpha Vantage (https://www.alphavantage.co): daily prices for US, London, XETRA, Toronto,
BSE India and Shanghai / Shenzhen listings.

    export ALPHA_VANTAGE_API_KEY=your-key

Used as a backup: when the main source for a symbol fails, ``fetch`` tries Alpha Vantage
next. The free key allows about 25 calls a day and the latest 100 trading days; with a
premium key the full history is loaded on the first fetch. The key is read from the
environment only.
"""

from __future__ import annotations

import os
import urllib.parse
from datetime import date, timedelta

import pandas as pd

from .http import HttpGet, SourceError, get_json
from .sources import PRICE_COLUMNS, empty_prices
from .symbols import currency_for, split_symbol

API_KEY_ENV = "ALPHA_VANTAGE_API_KEY"
BASE_URL = "https://www.alphavantage.co/query"

# Yahoo suffix -> Alpha Vantage suffix. Tokyo, Hong Kong, NSE and Johannesburg are not covered.
SUFFIX_MAP = {"": "", "L": "LON", "DE": "DEX", "TO": "TRT", "V": "TRV", "BO": "BSE", "SS": "SHH", "SZ": "SHZ"}

# "compact" returns about 100 trading days; ask for "full" when the request reaches further back.
COMPACT_CALENDAR_DAYS = 140


def api_key_from_env() -> str | None:
    return os.environ.get(API_KEY_ENV, "").strip() or None


def to_av_symbol(symbol: str) -> str | None:
    code, suffix = split_symbol(symbol)
    if suffix not in SUFFIX_MAP:
        return None
    return f"{code}.{SUFFIX_MAP[suffix]}" if SUFFIX_MAP[suffix] else code


class AlphaVantageSource:
    name = "alphavantage"

    def __init__(self, api_key: str | None = None, http_get: HttpGet | None = None):
        self.api_key = api_key or api_key_from_env()
        if not self.api_key:
            raise SourceError(
                f"No Alpha Vantage API key. Set {API_KEY_ENV} in your environment (free at alphavantage.co)."
            )
        self._get = http_get or (lambda url, headers: get_json(url, headers, "Alpha Vantage", API_KEY_ENV))

    def covers(self, symbol: str) -> bool:
        return to_av_symbol(symbol) is not None

    def _daily(self, ticker: str, outputsize: str) -> dict:
        query = {"function": "TIME_SERIES_DAILY", "symbol": ticker, "outputsize": outputsize,
                 "apikey": self.api_key}
        data = self._get(f"{BASE_URL}?{urllib.parse.urlencode(query)}", {"Accept": "application/json"})
        return data if isinstance(data, dict) else {}

    def fetch(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        ticker = to_av_symbol(symbol)
        if ticker is None:
            raise SourceError(f"Alpha Vantage does not cover {symbol}")
        wants_full = start < date.today() - timedelta(days=COMPACT_CALENDAR_DAYS)
        data = self._daily(ticker, "full" if wants_full else "compact")
        if wants_full and "premium" in str(data.get("Information", "")).lower():
            data = self._daily(ticker, "compact")  # free key: settle for the last 100 days
        series = data.get("Time Series (Daily)")
        if series is None:
            message = data.get("Error Message") or data.get("Note") or data.get("Information") or "no data"
            raise SourceError(f"Alpha Vantage: {message}")
        return normalize_daily(symbol, series, start, end)

    def currency(self, symbol: str) -> str | None:
        return currency_for(symbol)


def normalize_daily(symbol: str, series: dict, start: date, end: date) -> pd.DataFrame:
    rows = []
    for day, bar in series.items():
        d = date.fromisoformat(day[:10])
        if start <= d <= end:
            rows.append({
                "symbol": symbol, "date": d,
                "open": float(bar["1. open"]), "high": float(bar["2. high"]),
                "low": float(bar["3. low"]), "close": float(bar["4. close"]),
                "volume": float(bar.get("5. volume", 0)),
            })
    if not rows:
        return empty_prices()
    df = pd.DataFrame(rows)
    df["adj_close"] = df["close"]
    return df.sort_values("date")[PRICE_COLUMNS].reset_index(drop=True)
