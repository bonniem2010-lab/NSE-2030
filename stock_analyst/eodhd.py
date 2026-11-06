"""EODHD (https://eodhd.com): end-of-day prices for 70+ exchanges, plus optional fundamentals.

This is the source that fills the gap the free Yahoo / US-only sources leave: London,
XETRA, Euronext, Tokyo, Hong Kong, NSE / BSE, Johannesburg and more, under one key.

    export EODHD_API_KEY=your-key
    export EODHD_FUNDAMENTALS=1   # optional: also fetch P/E, PEG, margins, growth (paid plan)

The free plan allows 20 calls a day and one year of history; fundamentals need a plan
that includes them, so they are off unless EODHD_FUNDAMENTALS=1. The key is read from the
environment only; it is never written to disk.
"""

from __future__ import annotations

import os
import urllib.parse
from datetime import date

import pandas as pd

from .http import HttpGet, SourceError, get_json
from .sources import PRICE_COLUMNS, empty_prices
from .symbols import currency_for, split_symbol

API_KEY_ENV = "EODHD_API_KEY"
FUNDAMENTALS_ENV = "EODHD_FUNDAMENTALS"
BASE_URL = "https://eodhd.com/api"

# Yahoo suffix -> EODHD exchange code.
EXCHANGE_BY_SUFFIX = {
    "": "US",
    "L": "LSE",
    "DE": "XETRA",
    "F": "F",
    "AS": "AS",
    "PA": "PA",
    "MI": "MI",
    "MC": "MC",
    "T": "TSE",
    "HK": "HK",
    "NS": "NSE",
    "BO": "BSE",
    "JO": "JSE",
    "TO": "TO",
    "V": "V",
    "AX": "AU",
    "SW": "SW",
    "KS": "KO",
    "SS": "SHG",
    "SZ": "SHE",
}

# Our fundamentals field (same names as financialdatasets.FUNDAMENTAL_FIELDS) ->
# (section, EODHD field). Ratios and growth rates are decimals (0.12 = 12%).
FUNDAMENTAL_MAP = {
    "market_cap": ("Highlights", "MarketCapitalization"),
    "price_to_earnings_ratio": ("Highlights", "PERatio"),
    "peg_ratio": ("Highlights", "PEGRatio"),
    "net_margin": ("Highlights", "ProfitMargin"),
    "operating_margin": ("Highlights", "OperatingMarginTTM"),
    "return_on_equity": ("Highlights", "ReturnOnEquityTTM"),
    "revenue_growth": ("Highlights", "QuarterlyRevenueGrowthYOY"),
    "earnings_growth": ("Highlights", "QuarterlyEarningsGrowthYOY"),
    "earnings_per_share": ("Highlights", "EarningsShare"),
    "price_to_book_ratio": ("Valuation", "PriceBookMRQ"),
    "price_to_sales_ratio": ("Valuation", "PriceSalesTTM"),
    "enterprise_value_to_ebitda_ratio": ("Valuation", "EnterpriseValueEbitda"),
}


def api_key_from_env() -> str | None:
    return os.environ.get(API_KEY_ENV, "").strip() or None


def fundamentals_enabled_from_env() -> bool:
    return os.environ.get(FUNDAMENTALS_ENV, "").strip().lower() in ("1", "true", "yes", "on")


def to_eodhd_symbol(symbol: str) -> str | None:
    """``SHEL.L`` -> ``SHEL.LSE``, ``7203.T`` -> ``7203.TSE``; None when the exchange is unmapped."""
    code, suffix = split_symbol(symbol)
    exchange = EXCHANGE_BY_SUFFIX.get(suffix)
    return f"{code}.{exchange}" if exchange else None


class EODHDSource:
    name = "eodhd"

    def __init__(self, api_key: str | None = None, fundamentals: bool | None = None,
                 http_get: HttpGet | None = None):
        self.api_key = api_key or api_key_from_env()
        if not self.api_key:
            raise SourceError(f"No EODHD API key. Set {API_KEY_ENV} in your environment (get one at eodhd.com).")
        self.with_fundamentals = fundamentals_enabled_from_env() if fundamentals is None else fundamentals
        self._get = http_get or (lambda url, headers: get_json(url, headers, "EODHD", API_KEY_ENV))

    def covers(self, symbol: str) -> bool:
        return to_eodhd_symbol(symbol) is not None

    def _request(self, path: str, params: dict | None = None):
        query = {"api_token": self.api_key, "fmt": "json", **(params or {})}
        return self._get(f"{BASE_URL}{path}?{urllib.parse.urlencode(query)}", {"Accept": "application/json"})

    def _ticker(self, symbol: str) -> str:
        ticker = to_eodhd_symbol(symbol)
        if ticker is None:
            raise SourceError(f"EODHD has no exchange mapping for {symbol}")
        return ticker

    def fetch(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        ticker = self._ticker(symbol)
        data = self._request(f"/eod/{urllib.parse.quote(ticker)}",
                             {"from": start.isoformat(), "to": end.isoformat(), "period": "d"})
        if isinstance(data, dict):  # errors come back as an object, bars as a list
            raise SourceError(f"EODHD: {data.get('message') or data.get('error') or data}")
        return normalize_eod(symbol, data or [])

    def currency(self, symbol: str) -> str | None:
        return currency_for(symbol)

    def fundamentals(self, symbol: str) -> dict | None:
        """Valuation, profitability and growth for any covered listing, or None when switched off."""
        if not self.with_fundamentals:
            return None
        data = self._request(f"/fundamentals/{urllib.parse.quote(self._ticker(symbol))}")
        if not isinstance(data, dict):
            raise SourceError(f"EODHD: unexpected fundamentals response for {symbol}")
        return parse_fundamentals(symbol, data)


def _number(value) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None  # drop NaN


def parse_fundamentals(symbol: str, data: dict) -> dict:
    out = {}
    for field, (section, key) in FUNDAMENTAL_MAP.items():
        value = _number((data.get(section) or {}).get(key))
        if value is not None:
            out[field] = value
    # EODHD reports P/E 0 when there is no meaningful ratio. With negative earnings that
    # means "unprofitable" (signals vote against); otherwise treat it as unknown.
    if out.get("price_to_earnings_ratio") == 0:
        if out.get("earnings_per_share", 0) < 0:
            out["price_to_earnings_ratio"] = 0.0
        else:
            del out["price_to_earnings_ratio"]
    if out.get("peg_ratio") == 0:
        del out["peg_ratio"]
    if not out:
        raise SourceError(f"EODHD returned no fundamentals for {symbol}")
    return out


def normalize_eod(symbol: str, bars: list[dict]) -> pd.DataFrame:
    if not bars:
        return empty_prices()
    df = pd.DataFrame(bars)
    df["date"] = pd.to_datetime(df["date"].astype(str).str[:10]).dt.date
    df["symbol"] = symbol
    for col in ("open", "high", "low", "close", "volume"):
        if col not in df:
            df[col] = None
    df["adj_close"] = df["adjusted_close"] if "adjusted_close" in df else df["close"]
    df = df.dropna(subset=["close"]).drop_duplicates(subset="date", keep="last")
    return df.sort_values("date")[PRICE_COLUMNS].reset_index(drop=True)
