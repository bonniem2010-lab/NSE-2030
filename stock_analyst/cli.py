"""Command line: stock-analyst fetch | prices | show SYMBOL"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .sources import YFinanceSource
from .store import DEFAULT_DB_PATH, PriceStore
from .update import update_prices
from .watchlist import WatchlistError, load_watchlist

DEFAULT_WATCHLIST = Path("watchlist.toml")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="stock-analyst")
    parser.add_argument("--watchlist", type=Path, default=DEFAULT_WATCHLIST)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    sub = parser.add_subparsers(dest="command", required=True)

    fetch = sub.add_parser("fetch", help="download new daily prices for the watchlist")
    fetch.add_argument("--full", action="store_true", help="re-download full history")
    sub.add_parser("prices", help="latest close and daily change for each stock")
    show = sub.add_parser("show", help="recent daily bars for one symbol")
    show.add_argument("symbol")
    show.add_argument("--days", type=int, default=10)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    with PriceStore(args.db) as store:
        if args.command == "fetch":
            return _fetch(args, store)
        if args.command == "prices":
            return _prices(store)
        return _show(args, store)


def _fetch(args, store: PriceStore) -> int:
    try:
        watchlist = load_watchlist(args.watchlist)
    except WatchlistError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    results = update_prices(watchlist, store, YFinanceSource(), full=args.full)
    for r in results:
        status = f"{r.rows} rows from {r.start}" if r.ok else f"FAILED ({r.error})"
        print(f"{r.symbol:<14} {status}")
    failed = [r for r in results if not r.ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} symbols updated -> {args.db}")
    return 1 if failed and len(failed) == len(results) else 0


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


if __name__ == "__main__":
    sys.exit(main())
