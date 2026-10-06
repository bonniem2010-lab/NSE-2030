"""Load and validate the watchlist file."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_HISTORY_DAYS = 730


class WatchlistError(ValueError):
    """Raised when the watchlist file is missing or malformed."""


@dataclass(frozen=True)
class Stock:
    symbol: str
    name: str


@dataclass(frozen=True)
class Watchlist:
    stocks: tuple[Stock, ...]
    history_days: int = DEFAULT_HISTORY_DAYS
    source_path: Path | None = field(default=None, compare=False)

    @property
    def symbols(self) -> list[str]:
        return [s.symbol for s in self.stocks]


def load_watchlist(path: str | Path) -> Watchlist:
    path = Path(path)
    if not path.exists():
        raise WatchlistError(f"Watchlist file not found: {path}")
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise WatchlistError(f"{path} is not valid TOML: {exc}") from exc

    history_days = raw.get("settings", {}).get("history_days", DEFAULT_HISTORY_DAYS)
    if not isinstance(history_days, int) or history_days <= 0:
        raise WatchlistError("settings.history_days must be a positive whole number")

    entries = raw.get("stocks", [])
    if not entries:
        raise WatchlistError(f"{path} has no [[stocks]] entries")

    stocks: list[Stock] = []
    seen: set[str] = set()
    for i, entry in enumerate(entries, start=1):
        symbol = str(entry.get("symbol", "")).strip().upper()
        if not symbol:
            raise WatchlistError(f"Stock #{i} in {path} has no symbol")
        if symbol in seen:
            raise WatchlistError(f"{symbol} is listed more than once in {path}")
        seen.add(symbol)
        name = str(entry.get("name") or symbol).strip()
        stocks.append(Stock(symbol=symbol, name=name))

    return Watchlist(stocks=tuple(stocks), history_days=history_days, source_path=path)
