"""Financial Datasets API (https://financialdatasets.ai): US daily prices and fundamentals.

Set your key in the environment before running ``stock-analyst fetch``::

    export FINANCIAL_DATASETS_API_KEY=your-key

The key is read from the environment only; it is never written to disk.
Coverage is US-listed tickers. Symbols with an exchange suffix (``SHEL.L``,
``7203.T``) are not covered and keep using Yahoo Finance.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from typing import Callable

import pandas as pd

from .sources import PRICE_COLUMNS, empty_prices

API_KEY_ENV = "FINANCIAL_DATASETS_API_KEY"
BASE_URL = "https://api.financialdatasets.ai"
TIMEOUT_SECONDS = 30
MAX_PAGES = 100  # safety stop for pagination (100 bars per page)

# Metrics kept from /financial-metrics/snapshot. Ratios and growth rates are decimals (0.12 = 12%).
FUNDAMENTAL_FIELDS = (
    "market_cap",
    "price_to_earnings_ratio",
    "price_to_book_ratio",
    "price_to_sales_ratio",
    "enterprise_value_to_ebitda_ratio",
    "free_cash_flow_yield",
    "peg_ratio",
    "gross_margin",
    "operating_margin",
    "net_margin",
    "return_on_equity",
    "debt_to_equity",
    "revenue_growth",
    "earnings_growth",
    "earnings_per_share",
)

# (url, headers) -> parsed JSON. Swappable so tests never touch the network.
HttpGet = Callable[[str, dict], dict]


class FinancialDatasetsError(RuntimeError):
    """The API refused or failed a request, or no API key is configured."""


def api_key_from_env() -> str | None:
    key = os.environ.get(API_KEY_ENV, "").strip()
    return key or None


def is_us_symbol(symbol: str) -> bool:
    """Financial Datasets covers US tickers: no exchange suffix like ``.L`` or ``.T``.

    Class shares written with a dash (``BRK-B``) count as US.
    """
    return "." not in symbol


def _urllib_get(url: str, headers: dict) -> dict:
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:200]
        if exc.code in (401, 403):
            raise FinancialDatasetsError(
                f"HTTP {exc.code}: the API key was rejected. Check {API_KEY_ENV}."
            ) from exc
        raise FinancialDatasetsError(f"HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise FinancialDatasetsError(f"cannot reach {BASE_URL}: {exc.reason}") from exc


class FinancialDatasetsSource:
    """Daily prices and a fundamentals snapshot for US tickers."""

    name = "financialdatasets"

    def __init__(self, api_key: str | None = None, http_get: HttpGet | None = None):
        self.api_key = api_key or api_key_from_env()
        if not self.api_key:
            raise FinancialDatasetsError(
                f"No API key. Set {API_KEY_ENV} in your environment (get one at financialdatasets.ai)."
            )
        self._get = http_get or _urllib_get

    def _request(self, path_or_url: str, params: dict | None = None) -> dict:
        url = path_or_url if path_or_url.startswith("http") else BASE_URL + path_or_url
        if params:
            url += "?" + urllib.parse.urlencode(params)
        return self._get(url, {"X-API-KEY": self.api_key, "Accept": "application/json"})

    def fetch(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        data = self._request(
            "/prices/",
            {
                "ticker": symbol,
                "interval": "day",
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
            },
        )
        bars = list(data.get("prices") or [])
        pages = 1
        while data.get("next_page_url") and pages < MAX_PAGES:
            data = self._request(data["next_page_url"])
            bars.extend(data.get("prices") or [])
            pages += 1
        return normalize_prices(symbol, bars)

    def currency(self, symbol: str) -> str | None:
        return "USD"

    def fundamentals(self, symbol: str) -> dict:
        """Latest valuation, profitability and growth metrics for ``symbol``."""
        data = self._request("/financial-metrics/snapshot/", {"ticker": symbol})
        snap = data.get("snapshot", data) if isinstance(data, dict) else {}
        out = {}
        for field in FUNDAMENTAL_FIELDS:
            value = snap.get(field)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                out[field] = float(value)
        if not out:
            raise FinancialDatasetsError(f"no financial metrics returned for {symbol}")
        return out


def normalize_prices(symbol: str, bars: list[dict]) -> pd.DataFrame:
    if not bars:
        return empty_prices()
    df = pd.DataFrame(bars)
    # ``time`` is a UTC date or timestamp; keep the calendar date.
    df["date"] = pd.to_datetime(df["time"].astype(str).str[:10]).dt.date
    df["symbol"] = symbol
    for col in ("open", "high", "low", "close"):
        if col not in df:
            df[col] = None
    df["adj_close"] = df["close"]
    if "volume" not in df:
        df["volume"] = None
    df = df.dropna(subset=["close"]).drop_duplicates(subset="date", keep="last")
    return df.sort_values("date")[PRICE_COLUMNS].reset_index(drop=True)


class RoutedSource:
    """US symbols from Financial Datasets (prices + fundamentals); everything else from a fallback."""

    def __init__(self, primary: FinancialDatasetsSource, fallback):
        self.primary = primary
        self.fallback = fallback
        self.name = f"{primary.name}+{fallback.name}"

    def pick(self, symbol: str):
        return self.primary if is_us_symbol(symbol) else self.fallback

    def source_name(self, symbol: str) -> str:
        return self.pick(symbol).name

    def fetch(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        return self.pick(symbol).fetch(symbol, start, end)

    def currency(self, symbol: str) -> str | None:
        return self.pick(symbol).currency(symbol)

    def fundamentals(self, symbol: str) -> dict | None:
        if not is_us_symbol(symbol):
            return None
        return self.primary.fundamentals(symbol)
