"""Price data sources. Every source returns the same long-format DataFrame."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Protocol

import pandas as pd

PRICE_COLUMNS = ["symbol", "date", "open", "high", "low", "close", "adj_close", "volume"]


class PriceSource(Protocol):
    name: str

    def fetch(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        """Daily bars for ``symbol`` from ``start`` to ``end`` inclusive, as PRICE_COLUMNS."""
        ...

    def currency(self, symbol: str) -> str | None:
        """Trading currency of ``symbol``, if the source knows it."""
        ...


def empty_prices() -> pd.DataFrame:
    return pd.DataFrame(columns=PRICE_COLUMNS)


class YFinanceSource:
    """Free daily prices from Yahoo Finance via the yfinance package."""

    name = "yfinance"

    def fetch(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        import yfinance as yf

        # yfinance treats ``end`` as exclusive.
        raw = yf.Ticker(symbol).history(
            start=start.isoformat(),
            end=(end + timedelta(days=1)).isoformat(),
            interval="1d",
            auto_adjust=False,
            actions=False,
            raise_errors=True,
        )
        return normalize_yfinance(symbol, raw)

    def currency(self, symbol: str) -> str | None:
        import yfinance as yf

        try:
            return yf.Ticker(symbol).fast_info.get("currency")
        except Exception:
            return None


def normalize_yfinance(symbol: str, raw: pd.DataFrame) -> pd.DataFrame:
    if raw is None or raw.empty:
        return empty_prices()
    df = raw.rename(
        columns={
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Adj Close": "adj_close",
            "Volume": "volume",
        }
    )
    if "adj_close" not in df:
        df["adj_close"] = df["close"]
    # Exchange-local trading date; drop timezone so dates line up across markets.
    df["date"] = pd.to_datetime(df.index).date
    df["symbol"] = symbol
    df = df.dropna(subset=["close"])
    return df[PRICE_COLUMNS].reset_index(drop=True)
