from datetime import date, timedelta

import pandas as pd
import pytest

from stock_analyst.cli import main
from stock_analyst.sources import PRICE_COLUMNS, normalize_yfinance
from stock_analyst.store import PriceStore
from stock_analyst.update import OVERLAP_DAYS, update_prices
from stock_analyst.watchlist import Stock, Watchlist, WatchlistError, load_watchlist


class FakeSource:
    name = "fake"

    def __init__(self, fail=()):
        self.fail = set(fail)
        self.calls = []

    def fetch(self, symbol, start, end):
        self.calls.append((symbol, start, end))
        if symbol in self.fail:
            raise ConnectionError("boom")
        days = pd.bdate_range(start, end)
        return pd.DataFrame(
            {
                "symbol": symbol,
                "date": [d.date() for d in days],
                "open": 10.0,
                "high": 11.0,
                "low": 9.0,
                "close": [10.0 + i for i in range(len(days))],
                "adj_close": [10.0 + i for i in range(len(days))],
                "volume": 1000,
            }
        )[PRICE_COLUMNS]

    def currency(self, symbol):
        return "JPY" if symbol.endswith(".T") else "USD"


def watchlist(*symbols, history_days=30):
    return Watchlist(tuple(Stock(s, s) for s in symbols), history_days)


def test_load_watchlist(tmp_path):
    path = tmp_path / "w.toml"
    path.write_text(
        '[settings]\nhistory_days = 90\n[[stocks]]\nsymbol = "aapl"\nname = "Apple"\n'
        '[[stocks]]\nsymbol = "7203.T"\n'
    )
    wl = load_watchlist(path)
    assert wl.symbols == ["AAPL", "7203.T"]
    assert wl.history_days == 90
    assert wl.stocks[1].name == "7203.T"


@pytest.mark.parametrize(
    "body",
    ["", '[[stocks]]\nname = "x"\n', '[[stocks]]\nsymbol="A"\n[[stocks]]\nsymbol="a"\n'],
)
def test_load_watchlist_rejects_bad_files(tmp_path, body):
    path = tmp_path / "w.toml"
    path.write_text(body)
    with pytest.raises(WatchlistError):
        load_watchlist(path)


def test_repo_watchlist_is_valid():
    assert len(load_watchlist("watchlist.toml").stocks) >= 5


def test_first_fetch_loads_history_then_increments():
    store = PriceStore(":memory:")
    source = FakeSource()
    today = date(2026, 10, 5)

    first = update_prices(watchlist("AAPL"), store, source, today=today)
    assert first[0].ok and first[0].start == today - timedelta(days=30)
    assert store.last_date("AAPL") == today
    n = len(store.get_prices("AAPL"))

    later = today + timedelta(days=7)
    update_prices(watchlist("AAPL"), store, source, today=later)
    assert source.calls[-1][1] == today - timedelta(days=OVERLAP_DAYS)
    assert store.last_date("AAPL") == date(2026, 10, 12)
    assert len(store.get_prices("AAPL")) == n + 5  # overlap days replaced, not duplicated


def test_one_failure_does_not_stop_others():
    store = PriceStore(":memory:")
    results = update_prices(
        watchlist("AAPL", "BAD", "7203.T"), store, FakeSource(fail={"BAD"}), today=date(2026, 10, 5)
    )
    assert [r.ok for r in results] == [True, False, True]
    assert "ConnectionError" in results[1].error
    sec = store.securities().set_index("symbol")
    assert sec.loc["7203.T", "currency"] == "JPY"
    assert "BAD" not in sec.index


def test_empty_first_fetch_is_an_error():
    class Empty(FakeSource):
        def fetch(self, symbol, start, end):
            return pd.DataFrame(columns=PRICE_COLUMNS)

    store = PriceStore(":memory:")
    (r,) = update_prices(watchlist("NOPE"), store, Empty(), today=date(2026, 10, 5))
    assert not r.ok and "no price data" in r.error


def test_latest_closes():
    store = PriceStore(":memory:")
    update_prices(watchlist("AAPL"), store, FakeSource(), today=date(2026, 10, 5))
    row = store.latest_closes().iloc[0]
    assert row.close == row.prev_close + 1


def test_normalize_yfinance():
    idx = pd.DatetimeIndex(["2026-10-01", "2026-10-02"], tz="Asia/Tokyo")
    raw = pd.DataFrame(
        {"Open": [1, 2], "High": [1, 2], "Low": [1, 2], "Close": [1.5, None],
         "Adj Close": [1.4, None], "Volume": [10, 20]},
        index=idx,
    )
    df = normalize_yfinance("7203.T", raw)
    assert list(df.columns) == PRICE_COLUMNS
    assert len(df) == 1 and df.iloc[0].date == date(2026, 10, 1)


def test_cli_prices_and_show(tmp_path, capsys):
    db = tmp_path / "p.db"
    with PriceStore(db) as store:
        update_prices(watchlist("AAPL"), store, FakeSource(), today=date(2026, 10, 5))
    assert main(["--db", str(db), "prices"]) == 0
    assert "AAPL" in capsys.readouterr().out
    assert main(["--db", str(db), "show", "aapl", "--days", "3"]) == 0
    assert "2026-10-05" in capsys.readouterr().out
