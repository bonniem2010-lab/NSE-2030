"""Fetch new daily prices for every stock on the watchlist."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta

from .sources import PriceSource
from .store import PriceStore
from .watchlist import Watchlist

log = logging.getLogger(__name__)

# Re-fetch a few recent days so late corrections and partial bars get overwritten.
OVERLAP_DAYS = 5


@dataclass
class FetchResult:
    symbol: str
    rows: int
    start: date
    error: str | None = None
    fundamentals: str | None = None  # "ok", an error message, or None when not fetched

    @property
    def ok(self) -> bool:
        return self.error is None


def update_prices(
    watchlist: Watchlist,
    store: PriceStore,
    source: PriceSource,
    today: date | None = None,
    full: bool = False,
) -> list[FetchResult]:
    """Fetch prices for each watchlist stock. One failing symbol never stops the others."""
    today = today or date.today()
    results: list[FetchResult] = []
    for stock in watchlist.stocks:
        last = None if full else store.last_date(stock.symbol)
        if last is None:
            start = today - timedelta(days=watchlist.history_days)
        else:
            start = last - timedelta(days=OVERLAP_DAYS)

        try:
            df = source.fetch(stock.symbol, start, today)
            rows = store.upsert_prices(df)
            if rows == 0 and last is None:
                raise ValueError("no price data returned; check the symbol")
        except Exception as exc:  # network errors, bad symbols, source outages
            error = f"{type(exc).__name__}: {exc}"
            log.warning("%s failed: %s", stock.symbol, error)
            store.log_fetch(stock.symbol, 0, error)
            results.append(FetchResult(stock.symbol, 0, start, error))
            continue

        source_name = getattr(source, "source_name", lambda _s: source.name)(stock.symbol)
        store.upsert_security(
            stock.symbol, stock.name, source.currency(stock.symbol), source_name
        )
        store.log_fetch(stock.symbol, rows)
        results.append(
            FetchResult(stock.symbol, rows, start, fundamentals=_update_fundamentals(stock.symbol, store, source))
        )
    return results


def _update_fundamentals(symbol: str, store: PriceStore, source) -> str | None:
    """Refresh the fundamentals snapshot when the source offers one. Never fails the price update."""
    getter = getattr(source, "fundamentals", None)
    if getter is None:
        return None
    try:
        metrics = getter(symbol)
    except Exception as exc:
        log.warning("%s fundamentals failed: %s", symbol, exc)
        return f"{type(exc).__name__}: {exc}"
    if not metrics:
        return None
    origin = getattr(source, "fundamentals_source", lambda _s: None)(symbol)
    origin = origin or getattr(source, "source_name", lambda _s: source.name)(symbol)
    store.upsert_fundamentals(symbol, metrics, origin)
    return "ok"
