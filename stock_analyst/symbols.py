"""Translate watchlist symbols (Yahoo Finance format) into other providers' formats."""

from __future__ import annotations


def split_symbol(symbol: str) -> tuple[str, str]:
    """``SHEL.L`` -> (``SHEL``, ``L``); ``AAPL`` -> (``AAPL``, ``""``). ``BRK-B`` stays one code."""
    code, dot, suffix = symbol.rpartition(".")
    if not dot:
        return symbol, ""
    return code, suffix.upper()


# Trading currency per Yahoo suffix. London and Johannesburg quote in pence / cents,
# written the way Yahoo Finance writes them.
CURRENCY_BY_SUFFIX = {
    "": "USD",
    "L": "GBp",
    "DE": "EUR",
    "F": "EUR",
    "AS": "EUR",
    "PA": "EUR",
    "MI": "EUR",
    "MC": "EUR",
    "T": "JPY",
    "HK": "HKD",
    "NS": "INR",
    "BO": "INR",
    "JO": "ZAc",
    "TO": "CAD",
    "V": "CAD",
    "AX": "AUD",
    "SW": "CHF",
    "KS": "KRW",
    "SS": "CNY",
    "SZ": "CNY",
}


def currency_for(symbol: str) -> str | None:
    return CURRENCY_BY_SUFFIX.get(split_symbol(symbol)[1])
