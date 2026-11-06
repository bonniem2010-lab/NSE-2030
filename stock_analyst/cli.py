"""Command line: stock-analyst fetch | prices | show SYMBOL | signals | report"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import report
from .signals import analyze
from .financialdatasets import (
    API_KEY_ENV,
    FinancialDatasetsError,
    FinancialDatasetsSource,
    RoutedSource,
    api_key_from_env,
)
from . import alphavantage, eodhd
from .alphavantage import AlphaVantageSource
from .chain import SourceChain
from .eodhd import EODHDSource
from .http import SourceError
from .sources import YFinanceSource
from .store import DEFAULT_DB_PATH, PriceStore
from .update import update_prices
from .watchlist import WatchlistError, load_watchlist

DEFAULT_WATCHLIST = Path("watchlist.toml")
SOURCE_CHOICES = ("auto", "yfinance", "financialdatasets", "eodhd", "alphavantage")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="stock-analyst")
    parser.add_argument("--watchlist", type=Path, default=DEFAULT_WATCHLIST)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    sub = parser.add_subparsers(dest="command", required=True)

    fetch = sub.add_parser("fetch", help="download new daily prices for the watchlist")
    fetch.add_argument("--full", action="store_true", help="re-download full history")
    fetch.add_argument(
        "--source",
        choices=SOURCE_CHOICES,
        default="auto",
        help="auto (default): every source whose API key is set, in the order Financial Datasets "
        "(US), EODHD, Yahoo Finance, Alpha Vantage; a failed symbol falls through to the next. "
        "yfinance: Yahoo only. financialdatasets / eodhd / alphavantage: that source first, "
        "then Yahoo, and stop with an error if its key is missing",
    )
    sub.add_parser("prices", help="latest close and daily change for each stock")
    show = sub.add_parser("show", help="recent daily bars for one symbol")
    show.add_argument("symbol")
    show.add_argument("--days", type=int, default=10)
    sub.add_parser("signals", help="buy / hold / sell call with reasons for each stock")
    rep = sub.add_parser("report", help="write the one-page HTML dashboard")
    rep.add_argument("-o", "--output", type=Path, default=Path("report.html"))
    rep.add_argument("--snapshot", type=Path, help="build from a saved price snapshot JSON instead of the database")
    rep.add_argument("--fragment", action="store_true", help="omit the <html> wrapper")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    if args.command == "report" and args.snapshot:
        stocks, meta = report.from_snapshot(args.snapshot)
        return _write_report(args, report.build_payload(stocks, meta.get("as_of"), meta.get("note", "")))

    with PriceStore(args.db) as store:
        if args.command == "fetch":
            return _fetch(args, store)
        if args.command == "prices":
            return _prices(store)
        if args.command == "signals":
            return _signals(args, store)
        if args.command == "report":
            return _report(args, store)
        return _show(args, store)


def _fetch(args, store: PriceStore) -> int:
    try:
        watchlist = load_watchlist(args.watchlist)
    except WatchlistError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        source = make_source(args.source)
    except (FinancialDatasetsError, SourceError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"Source: {source.name}")
    results = update_prices(watchlist, store, source, full=args.full)
    for r in results:
        status = f"{r.rows} rows from {r.start}" if r.ok else f"FAILED ({r.error})"
        if r.fundamentals == "ok":
            status += ", fundamentals updated"
        elif r.fundamentals:
            status += f", fundamentals FAILED ({r.fundamentals})"
        print(f"{r.symbol:<14} {status}")
    failed = [r for r in results if not r.ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} symbols updated -> {args.db}")
    return 1 if failed and len(failed) == len(results) else 0


def make_source(choice: str, api_key: str | None = None):
    """Build the price source for ``fetch``.

    ``api_key`` overrides the Financial Datasets key (kept for callers of the first version).
    Keyed sources run before Yahoo Finance; Alpha Vantage, with its small free quota, is the
    last resort. Each symbol uses the first source that covers it and falls through on errors.
    """
    if choice == "yfinance":
        return YFinanceSource()
    if choice == "financialdatasets":
        return SourceChain([FinancialDatasetsSource(api_key or api_key_from_env()), YFinanceSource()])
    if choice == "eodhd":
        return SourceChain([EODHDSource(), YFinanceSource()])
    if choice == "alphavantage":
        return SourceChain([AlphaVantageSource(), YFinanceSource()])

    first, last = [], []
    fd_key = api_key or api_key_from_env()
    if fd_key:
        first.append(FinancialDatasetsSource(fd_key))
    if eodhd.api_key_from_env():
        first.append(EODHDSource())
    if alphavantage.api_key_from_env():
        last.append(AlphaVantageSource())
    sources = first + [YFinanceSource()] + last
    return sources[0] if len(sources) == 1 else SourceChain(sources)


def _prices(store: PriceStore) -> int:
    latest = store.latest_closes()
    if latest.empty:
        print("No prices stored yet. Run: stock-analyst fetch")
        return 1
    info = store.securities().set_index("symbol")
    for r in latest.itertuples(index=False):
        currency = info.currency.get(r.symbol) or ""
        change = ""
        if r.prev_close:
            change = f"{(r.close / r.prev_close - 1) * 100:+.2f}%"
        print(f"{r.symbol:<14} {r.date}  {r.close:>12,.2f} {currency:<4} {change}")
    return 0


def _show(args, store: PriceStore) -> int:
    df = store.get_prices(args.symbol.upper())
    if df.empty:
        print(f"No prices stored for {args.symbol.upper()}")
        return 1
    print(df.tail(args.days).drop(columns="symbol").to_string())
    return 0


def _stored_series(args, store: PriceStore) -> list[report.StockSeries] | None:
    try:
        watchlist = load_watchlist(args.watchlist)
    except WatchlistError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return None
    stocks = report.from_store(store, watchlist)
    if not stocks:
        print("No prices stored yet. Run: stock-analyst fetch")
        return None
    return stocks


def _signals(args, store: PriceStore) -> int:
    stocks = _stored_series(args, store)
    if stocks is None:
        return 1
    for s in stocks:
        rec = analyze(s.closes, s.fundamentals)
        print(f"{s.symbol:<14} {rec.action:<5} score {rec.score:+d}  {s.name}")
        for r in rec.reasons:
            mark = {1: "+", -1: "-", 0: " "}[r.vote]
            print(f"    {mark} {r.text}")
    return 0


def _report(args, store: PriceStore) -> int:
    stocks = _stored_series(args, store)
    if stocks is None:
        return 1
    return _write_report(args, report.build_payload(stocks, note=_note(store)))


SOURCE_LABELS = {
    "financialdatasets": "Financial Datasets",
    "eodhd": "EODHD",
    "alphavantage": "Alpha Vantage",
    "yfinance": "Yahoo Finance",
}


def _note(store: PriceStore) -> str:
    """Name the providers behind the stored data, e.g. "Prices from EODHD and Yahoo Finance."."""
    prices = _labels(set(store.securities()["source"].dropna())) or ["Yahoo Finance"]
    text = "Daily closes from " + _join(prices)
    fundamentals = _labels(store.fundamentals_sources())
    if fundamentals:
        text += "; fundamentals from " + _join(fundamentals)
    return text + "."


def _labels(names: set[str]) -> list[str]:
    return [label for name, label in SOURCE_LABELS.items() if name in names]


def _join(labels: list[str]) -> str:
    return labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + " and " + labels[-1]


def _write_report(args, payload: dict) -> int:
    args.output.write_text(report.render_html(payload, fragment=args.fragment), encoding="utf-8")
    counts = {a: sum(s["rec"]["action"] == a for s in payload["stocks"]) for a in ("BUY", "HOLD", "SELL")}
    print(f"Wrote {args.output}: {len(payload['stocks'])} stocks, "
          f"{counts['BUY']} buy / {counts['HOLD']} hold / {counts['SELL']} sell")
    return 0


if __name__ == "__main__":
    sys.exit(main())
