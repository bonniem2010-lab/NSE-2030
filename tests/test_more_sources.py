from datetime import date, timedelta
from urllib.parse import parse_qs, urlparse

import pandas as pd
import pytest

from stock_analyst import cli
from stock_analyst.alphavantage import AlphaVantageSource, to_av_symbol
from stock_analyst.chain import SourceChain
from stock_analyst.eodhd import EODHDSource, parse_fundamentals, to_eodhd_symbol
from stock_analyst.http import SourceError
from stock_analyst.sources import PRICE_COLUMNS
from stock_analyst.store import PriceStore
from stock_analyst.symbols import currency_for
from stock_analyst.update import update_prices
from stock_analyst.watchlist import Stock, Watchlist

KEY_ENVS = ("FINANCIAL_DATASETS_API_KEY", "EODHD_API_KEY", "EODHD_FUNDAMENTALS", "ALPHA_VANTAGE_API_KEY")


@pytest.fixture(autouse=True)
def no_keys(monkeypatch):
    for env in KEY_ENVS:
        monkeypatch.delenv(env, raising=False)


def _query(url):
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


SHELL_FUNDAMENTALS = {
    "General": {"Code": "SHEL", "CurrencyCode": "GBX"},
    "Highlights": {"MarketCapitalization": 1.6e11, "PERatio": 12.5, "PEGRatio": 0.9,
                   "ProfitMargin": 0.07, "ReturnOnEquityTTM": "0.11",
                   "QuarterlyRevenueGrowthYOY": -0.04, "EarningsShare": 2.1, "OperatingMarginTTM": None},
    "Valuation": {"PriceBookMRQ": 1.2, "EnterpriseValueEbitda": 4.1},
}


class FakeEODHD:
    def __init__(self, fundamentals=SHELL_FUNDAMENTALS):
        self.fundamentals = fundamentals
        self.urls = []

    def __call__(self, url, headers):
        self.urls.append(url)
        path = urlparse(url).path
        if path.startswith("/api/eod/"):
            q = _query(url)
            days = pd.bdate_range(q["from"], q["to"])[:4]
            return [{"date": d.strftime("%Y-%m-%d"), "open": 10, "high": 11, "low": 9,
                     "close": 10 + i, "adjusted_close": 9.5 + i, "volume": 1000}
                    for i, d in enumerate(days)]
        if path.startswith("/api/fundamentals/"):
            return self.fundamentals
        raise AssertionError(url)


class FakeAV:
    def __init__(self, premium=False):
        self.premium = premium
        self.sizes = []

    def __call__(self, url, headers):
        q = _query(url)
        self.sizes.append(q["outputsize"])
        if q["outputsize"] == "full" and not self.premium:
            return {"Information": "The outputsize=full parameter value is a premium feature."}
        days = pd.bdate_range(end=date(2026, 2, 2), periods=5)
        return {"Meta Data": {}, "Time Series (Daily)": {
            d.strftime("%Y-%m-%d"): {"1. open": "1", "2. high": "2", "3. low": "0.5",
                                     "4. close": str(3 + i), "5. volume": "100"}
            for i, d in enumerate(days)}}


class Fake:
    """A keyless stand-in for Yahoo or any other source."""

    def __init__(self, name="yfinance", fail=False, covers=None):
        self.name, self.fail, self._covers, self.calls = name, fail, covers, []

    def covers(self, symbol):
        return True if self._covers is None else self._covers(symbol)

    def fetch(self, symbol, start, end):
        self.calls.append(symbol)
        if self.fail:
            raise ConnectionError(f"{self.name} down")
        days = pd.bdate_range(start, end)[:3]
        return pd.DataFrame({"symbol": symbol, "date": [d.date() for d in days], "open": 1.0,
                             "high": 1.0, "low": 1.0, "close": 1.0, "adj_close": 1.0,
                             "volume": 1})[PRICE_COLUMNS]

    def currency(self, symbol):
        return "XXX"


@pytest.mark.parametrize("symbol,expected", [
    ("AAPL", "AAPL.US"), ("SHEL.L", "SHEL.LSE"), ("SAP.DE", "SAP.XETRA"), ("ASML.AS", "ASML.AS"),
    ("7203.T", "7203.TSE"), ("0700.HK", "0700.HK"), ("RELIANCE.NS", "RELIANCE.NSE"),
    ("RELIANCE.BO", "RELIANCE.BSE"), ("NPN.JO", "NPN.JSE"), ("BRK-B", "BRK-B.US"), ("X.ZZ", None),
])
def test_eodhd_symbols(symbol, expected):
    assert to_eodhd_symbol(symbol) == expected


@pytest.mark.parametrize("symbol,expected", [
    ("AAPL", "AAPL"), ("SHEL.L", "SHEL.LON"), ("SAP.DE", "SAP.DEX"), ("RELIANCE.BO", "RELIANCE.BSE"),
    ("7203.T", None), ("0700.HK", None), ("RELIANCE.NS", None), ("NPN.JO", None),
])
def test_alpha_vantage_symbols(symbol, expected):
    assert to_av_symbol(symbol) == expected


def test_currencies_follow_yahoo_units():
    assert currency_for("SHEL.L") == "GBp" and currency_for("NPN.JO") == "ZAc"
    assert currency_for("7203.T") == "JPY" and currency_for("AAPL") == "USD"


def test_missing_keys_are_clear_errors():
    with pytest.raises(SourceError, match="EODHD_API_KEY"):
        EODHDSource()
    with pytest.raises(SourceError, match="ALPHA_VANTAGE_API_KEY"):
        AlphaVantageSource()


def test_eodhd_fetch_prices():
    http = FakeEODHD()
    df = EODHDSource("k", http_get=http).fetch("7203.T", date(2026, 1, 5), date(2026, 1, 30))
    assert list(df.columns) == PRICE_COLUMNS and len(df) == 4
    assert df["adj_close"].iloc[0] == 9.5 and df["symbol"].iloc[0] == "7203.T"
    url = urlparse(http.urls[0])
    assert url.path == "/api/eod/7203.TSE"
    assert _query(http.urls[0])["api_token"] == "k" and _query(http.urls[0])["from"] == "2026-01-05"


def test_eodhd_error_object_raises():
    src = EODHDSource("k", http_get=lambda url, h: {"message": "Ticker not found"})
    with pytest.raises(SourceError, match="Ticker not found"):
        src.fetch("NOPE.L", date(2026, 1, 1), date(2026, 1, 31))


def test_eodhd_fundamentals_off_by_default_and_mapped_when_on(monkeypatch):
    http = FakeEODHD()
    assert EODHDSource("k", http_get=http).fundamentals("SHEL.L") is None
    assert not any("/fundamentals/" in u for u in http.urls)  # no quota spent
    monkeypatch.setenv("EODHD_FUNDAMENTALS", "1")
    f = EODHDSource("k", http_get=http).fundamentals("SHEL.L")
    assert f["price_to_earnings_ratio"] == 12.5 and f["peg_ratio"] == 0.9
    assert f["return_on_equity"] == 0.11  # numeric strings are accepted
    assert f["revenue_growth"] == -0.04 and f["price_to_book_ratio"] == 1.2
    assert "operating_margin" not in f


def test_eodhd_zero_pe_means_loss_only_with_negative_eps():
    loss = parse_fundamentals("X", {"Highlights": {"PERatio": 0, "EarningsShare": -1.0, "PEGRatio": 0}})
    assert loss["price_to_earnings_ratio"] == 0.0 and "peg_ratio" not in loss
    unknown = parse_fundamentals("X", {"Highlights": {"PERatio": 0, "EarningsShare": 1.0}})
    assert "price_to_earnings_ratio" not in unknown
    with pytest.raises(SourceError):
        parse_fundamentals("X", {"Highlights": {}})


def test_alpha_vantage_free_key_falls_back_to_compact():
    http = FakeAV(premium=False)
    df = AlphaVantageSource("k", http_get=http).fetch("SHEL.L", date(2025, 1, 1), date(2026, 2, 2))
    assert http.sizes == ["full", "compact"]
    assert len(df) == 5 and df["date"].is_monotonic_increasing and df["close"].iloc[-1] == 7.0


def test_alpha_vantage_recent_window_uses_compact_and_filters_dates():
    http = FakeAV()
    start = date.today() - timedelta(days=10)
    AlphaVantageSource("k", http_get=http).fetch("AAPL", start, date.today())
    assert http.sizes == ["compact"]
    df = AlphaVantageSource("k", http_get=FakeAV(premium=True)).fetch(
        "AAPL", date(2026, 1, 29), date(2026, 1, 30))
    assert [d.isoformat() for d in df["date"]] == ["2026-01-29", "2026-01-30"]


def test_alpha_vantage_rate_limit_message_is_raised():
    src = AlphaVantageSource("k", http_get=lambda u, h: {"Note": "Thank you for using Alpha Vantage! limit"})
    with pytest.raises(SourceError, match="limit"):
        src.fetch("AAPL", date.today(), date.today())


def test_chain_routes_by_coverage_and_falls_through_on_failure():
    us_only = Fake("financialdatasets", covers=lambda s: "." not in s)
    broken = Fake("eodhd", fail=True)
    yahoo = Fake("yfinance")
    chain = SourceChain([us_only, broken, yahoo])
    wl = Watchlist((Stock("AAPL", "Apple"), Stock("SHEL.L", "Shell")), history_days=30)
    with PriceStore(":memory:") as store:
        results = update_prices(wl, store, chain, today=date(2026, 2, 1))
        assert all(r.ok for r in results)
        info = store.securities().set_index("symbol")
        assert info.loc["AAPL", "source"] == "financialdatasets"
        assert info.loc["SHEL.L", "source"] == "yfinance"  # EODHD failed, Yahoo took over
    assert broken.calls == ["SHEL.L"] and yahoo.calls == ["SHEL.L"]


def test_chain_reports_every_failure():
    chain = SourceChain([Fake("eodhd", fail=True), Fake("yfinance", fail=True)])
    with pytest.raises(RuntimeError, match="eodhd: eodhd down; yfinance: yfinance down"):
        chain.fetch("SHEL.L", date(2026, 1, 1), date(2026, 1, 31))


def test_non_us_fundamentals_come_from_eodhd(monkeypatch):
    monkeypatch.setenv("EODHD_FUNDAMENTALS", "1")
    chain = SourceChain([EODHDSource("k", http_get=FakeEODHD()), Fake("yfinance")])
    with PriceStore(":memory:") as store:
        (r,) = update_prices(Watchlist((Stock("SHEL.L", "Shell"),), history_days=30), store, chain,
                             today=date(2026, 2, 1))
        assert r.ok and r.fundamentals == "ok"
        assert store.get_fundamentals("SHEL.L")["peg_ratio"] == 0.9
        assert store.fundamentals_sources() == {"eodhd"}
        assert store.securities().set_index("symbol").loc["SHEL.L", "currency"] == "GBp"
        assert cli._note(store) == "Daily closes from EODHD; fundamentals from EODHD."


def test_cli_auto_uses_every_configured_key(monkeypatch):
    assert cli.make_source("auto").name == "yfinance"
    monkeypatch.setenv("EODHD_API_KEY", "e")
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", "a")
    assert cli.make_source("auto").name == "eodhd+yfinance+alphavantage"
    monkeypatch.setenv("FINANCIAL_DATASETS_API_KEY", "f")
    assert cli.make_source("auto").name == "financialdatasets+eodhd+yfinance+alphavantage"
    assert cli.make_source("eodhd").name == "eodhd+yfinance"


@pytest.mark.parametrize("choice,env", [("eodhd", "EODHD_API_KEY"), ("alphavantage", "ALPHA_VANTAGE_API_KEY")])
def test_cli_named_source_without_key_exits(choice, env, tmp_path, capsys):
    wl = tmp_path / "w.toml"
    wl.write_text('[[stocks]]\nsymbol = "SHEL.L"\n')
    code = cli.main(["--watchlist", str(wl), "--db", str(tmp_path / "p.db"), "fetch", "--source", choice])
    assert code == 2 and env in capsys.readouterr().err
