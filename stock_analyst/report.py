"""One-page HTML dashboard: watchlist, price charts, and a buy/hold/sell call with reasons."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

import pandas as pd

from .signals import LONG_WINDOW, MEDIUM_WINDOW, analyze, sma
from .store import PriceStore
from .watchlist import Watchlist

DATA_MARKER = "/*__DATA__*/null"


@dataclass(frozen=True)
class StockSeries:
    symbol: str
    name: str
    currency: str
    closes: pd.Series  # indexed by date, oldest first
    listing: str = ""
    quoted_as: str = ""


def from_store(store: PriceStore, watchlist: Watchlist) -> list[StockSeries]:
    info = store.securities().set_index("symbol")
    out = []
    for stock in watchlist.stocks:
        df = store.get_prices(stock.symbol)
        if df.empty:
            continue
        currency = info.currency.get(stock.symbol) if stock.symbol in info.index else None
        out.append(StockSeries(stock.symbol, stock.name, currency or "", df["close"]))
    return out


def from_snapshot(path: str | Path) -> tuple[list[StockSeries], dict]:
    """Load a saved price snapshot (see demo/snapshot.json)."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    stocks = [
        StockSeries(
            s["symbol"],
            s["name"],
            s["currency"],
            pd.Series(s["close"], index=pd.to_datetime(s["dates"]).date, dtype=float),
            listing=s.get("listing", ""),
            quoted_as=s.get("quoted_as", ""),
        )
        for s in raw["stocks"]
    ]
    meta = {k: v for k, v in raw.items() if k != "stocks"}
    return stocks, meta


def build_payload(stocks: list[StockSeries], as_of: str | None = None, note: str = "") -> dict:
    rows = []
    for s in stocks:
        closes = s.closes.dropna().astype(float)
        rec = analyze(closes)
        days = len(closes)
        days_note = f"Based on {days} trading days of closing prices."
        if days < LONG_WINDOW:
            days_note += f" The 200-day average needs {LONG_WINDOW} days, so it is not shown."
        rows.append(
            {
                "symbol": s.symbol,
                "name": s.name,
                "currency": s.currency,
                "listing": s.listing,
                "quoted_as": s.quoted_as,
                "dates": [str(d) for d in closes.index],
                "close": _round(closes),
                "sma50": _round(sma(closes, MEDIUM_WINDOW)),
                "sma200": _round(sma(closes, LONG_WINDOW)),
                "rec": _clean(rec.to_dict()),
                "days_note": days_note,
            }
        )
    if as_of is None:
        as_of = max((r["dates"][-1] for r in rows if r["dates"]), default="")
    return {"as_of": as_of, "note": note, "stocks": rows}


def render_html(payload: dict, fragment: bool = False) -> str:
    """Return the dashboard. ``fragment`` omits the doctype/html wrapper (for hosts that add one)."""
    template = resources.files("stock_analyst").joinpath("report_template.html").read_text("utf-8")
    data = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")
    body = template.replace(DATA_MARKER, data)
    if fragment:
        return body
    return f'<!doctype html>\n<html lang="en">\n<meta charset="utf-8">\n{body}\n</html>\n'


def _round(series: pd.Series) -> list[float | None]:
    return [None if pd.isna(v) else round(float(v), 4) for v in series]


def _clean(value):
    """Make a value JSON-safe (NaN/inf become null)."""
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value
