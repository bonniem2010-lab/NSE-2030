"""Combine several price sources: each symbol goes to the first source that covers it,
and if that source fails, the next one that covers it is tried."""

from __future__ import annotations

import logging
from datetime import date

import pandas as pd

log = logging.getLogger(__name__)


def covers(source, symbol: str) -> bool:
    check = getattr(source, "covers", None)
    return True if check is None else bool(check(symbol))


class SourceChain:
    def __init__(self, sources: list):
        if not sources:
            raise ValueError("SourceChain needs at least one source")
        self.sources = list(sources)
        self.name = "+".join(s.name for s in self.sources)
        self._used: dict[str, object] = {}
        self._fundamentals_from: dict[str, str] = {}

    def candidates(self, symbol: str) -> list:
        return [s for s in self.sources if covers(s, symbol)]

    def fetch(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        errors = []
        for source in self.candidates(symbol):
            try:
                df = source.fetch(symbol, start, end)
            except Exception as exc:
                log.info("%s from %s failed, trying the next source: %s", symbol, source.name, exc)
                errors.append(f"{source.name}: {exc}")
                continue
            self._used[symbol] = source
            return df
        if not errors:
            raise ValueError(f"no configured source covers {symbol}")
        raise RuntimeError("; ".join(errors))

    def _source_for(self, symbol: str):
        return self._used.get(symbol) or (self.candidates(symbol) or self.sources)[0]

    def source_name(self, symbol: str) -> str:
        return self._source_for(symbol).name

    def currency(self, symbol: str) -> str | None:
        return self._source_for(symbol).currency(symbol)

    def fundamentals(self, symbol: str) -> dict | None:
        """The first covering source that returns fundamentals wins; errors only surface if none does."""
        error = None
        for source in self.candidates(symbol):
            getter = getattr(source, "fundamentals", None)
            if getter is None:
                continue
            try:
                metrics = getter(symbol)
            except Exception as exc:
                error = exc
                continue
            if metrics:
                self._fundamentals_from[symbol] = source.name
                return metrics
        if error is not None:
            raise error
        return None

    def fundamentals_source(self, symbol: str) -> str | None:
        return self._fundamentals_from.get(symbol)
