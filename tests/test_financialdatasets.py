from datetime import date
from urllib.parse import parse_qs, urlparse

import numpy as np
import pandas as pd
import pytest

from stock_analyst import cli
from stock_analyst.financialdatasets import (
    API_KEY_ENV,
    FinancialDatasetsError,
    FinancialDatasetsSource,
    RoutedSource,
    is_us_symbol,
)
from stock_analyst.report import build_payload, from_store
from stock_analyst.signals import analyze
from stock_analyst.sources import PRICE_COLUMNS
from stock_analyst.store import PriceStore
from stock_analyst.update import update_prices
from stock_analyst.watchlist import Stock, Watchlist

AAPL_METRICS = {
    "ticker": "AAPL",
    "market_cap": 3.4e12,
    "price_to_earnings_ratio": 30.0,
    "peg_ratio": 2.8,
    "revenue_growth": 0.02,
    "net_margin": 0.24,
    "return_on_equity": 1.5,
    "debt_to_equity": 1.9,
    "free_cash_flow_yield": 0.03,
    "gross_margin": None,
}


class FakeHttp:
    """Stands in for the Financial Datasets API. Records every request."""

    def __init__(self, metrics=None, pages=2):
        self.metrics = metrics if metrics is not None else {"snapshot": AAPL_METRICS}
        self.pages = pages
        self.requests = []

    def __call__(self, url, headers):
        self.requests.append((url, headers))
        parsed = urlparse(url)
        q = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        if parsed.path.startswith("/prices"):
            page = int(q.get("page", 1))
            days = pd.bdate_range(q.get("start_date", "2026-01-01"), periods=3)
            if page > 1:
                days = days + pd.offsets.BDay(3 * (page - 1))
            body = {
                "ticker": q.get("ticker", "AAPL"),
                "prices": [
                    {"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5 + i, "volume": 100,
                     "time": d.strftime("%Y-%m-%dT00:00:00Z")}
                    for i, d in enumerate(days)
                ],
            }
            if page < self.pages:
                body["next_page_url"] = f"https://api.financialdatasets.ai/prices/?cursor=x&page={page + 1}"
            return body
        if parsed.path.startswith("/financial-metrics/snapshot"):
            return self.metrics
        raise AssertionError(f"unexpected url {url}")


class FakeYahoo:
    name = "yfinance"

    def __init__(self):
        self.calls = []

    def fetch(self, symbol, start, end):
        self.calls.append(symbol)
        days = pd.bdate_range(start, end)[:5]
        return pd.DataFrame({
            "symbol": symbol, "date": [d.date() for d in days], "open": 1.0, "high": 1.0,
            "low": 1.0, "close": 1.0, "adj_close": 1.0, "volume": 1,
        })[PRICE_COLUMNS]

    def currency(self, symbol):
        return "GBP"


def test_missing_key_is_a_clear_error(monkeypatch):
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    with pytest.raises(FinancialDatasetsError, match=API_KEY_ENV):
        FinancialDatasetsSource()


def test_fetch_follows_pages_and_sends_key():
    http = FakeHttp(pages=2)
    src = FinancialDatasetsSource("test-key", http_get=http)
    df = src.fetch("AAPL", date(2026, 1, 1), date(2026, 1, 31))
    assert list(df.columns) == PRICE_COLUMNS
    assert len(df) == 6
    assert df["date"].is_monotonic_increasing
    assert df["adj_close"].equals(df["close"])
    assert all(h["X-API-KEY"] == "test-key" for _, h in http.requests)
    first = parse_qs(urlparse(http.requests[0][0]).query)
    assert first["ticker"] == ["AAPL"] and first["interval"] == ["day"]
    assert first["start_date"] == ["2026-01-01"] and first["end_date"] == ["2026-01-31"]


@pytest.mark.parametrize("body", [{"snapshot": AAPL_METRICS}, AAPL_METRICS])
def test_fundamentals_accepts_wrapped_or_flat_snapshot(body):
    src = FinancialDatasetsSource("k", http_get=FakeHttp(metrics=body))
    f = src.fundamentals("AAPL")
    assert f["price_to_earnings_ratio"] == 30.0
    assert "ticker" not in f and "gross_margin" not in f  # strings and nulls dropped


def test_empty_fundamentals_is_an_error():
    src = FinancialDatasetsSource("k", http_get=FakeHttp(metrics={"snapshot": {}}))
    with pytest.raises(FinancialDatasetsError):
        src.fundamentals("AAPL")


def test_routing_us_vs_international():
    assert is_us_symbol("AAPL") and is_us_symbol("BRK-B")
    assert not is_us_symbol("SHEL.L") and not is_us_symbol("7203.T")


def test_update_routes_and_stores_fundamentals():
    yahoo = FakeYahoo()
    source = RoutedSource(FinancialDatasetsSource("k", http_get=FakeHttp(pages=1)), yahoo)
    wl = Watchlist((Stock("AAPL", "Apple"), Stock("SHEL.L", "Shell")), history_days=30)
    with PriceStore(":memory:") as store:
        results = update_prices(wl, store, source, today=date(2026, 2, 1))
        assert all(r.ok for r in results)
        assert results[0].fundamentals == "ok" and results[1].fundamentals is None
        assert yahoo.calls == ["SHEL.L"]
        info = store.securities().set_index("symbol")
        assert info.loc["AAPL", "source"] == "financialdatasets"
        assert info.loc["SHEL.L", "source"] == "yfinance"
        assert info.loc["AAPL", "currency"] == "USD"
        assert store.get_fundamentals("AAPL")["peg_ratio"] == 2.8
        assert store.get_fundamentals("SHEL.L") is None
        stocks = from_store(store, wl)
        assert stocks[0].fundamentals["price_to_earnings_ratio"] == 30.0
        payload = build_payload(stocks)
        rules = [r["rule"] for r in payload["stocks"][0]["rec"]["reasons"]]
        assert rules[0] == "history"  # five days of prices: no call yet
    assert any(r.startswith("valuation") for r in rules)


def test_fundamentals_failure_does_not_fail_prices():
    http = FakeHttp(pages=1)

    def flaky(url, headers):
        if "financial-metrics" in url:
            raise FinancialDatasetsError("HTTP 500: oops")
        return http(url, headers)

    source = RoutedSource(FinancialDatasetsSource("k", http_get=flaky), FakeYahoo())
    with PriceStore(":memory:") as store:
        (r,) = update_prices(Watchlist((Stock("AAPL", "Apple"),)), store, source, today=date(2026, 2, 1))
        assert r.ok and r.rows > 0
        assert "HTTP 500" in r.fundamentals
        assert store.get_fundamentals("AAPL") is None


def _flat(n=260):
    rng = np.random.default_rng(3)
    values = 100 * np.cumprod(1 + rng.normal(0, 0.004, n))
    return pd.Series(values, index=pd.bdate_range("2025-01-01", periods=n).date)


def test_valuation_and_growth_votes():
    closes = _flat()
    base = analyze(closes)
    cheap = analyze(closes, {"price_to_earnings_ratio": 12.0, "peg_ratio": 0.8, "revenue_growth": 0.2})
    rich = analyze(closes, {"price_to_earnings_ratio": 60.0, "peg_ratio": 4.0, "revenue_growth": -0.05})
    assert cheap.score == base.score + 2
    assert rich.score == base.score - 2
    assert cheap.max_score == base.max_score + 2
    texts = " ".join(r.text for r in cheap.reasons)
    assert "PEG of 0.8" in texts and "revenue up 20%" in texts


def test_valuation_without_peg_uses_pe_and_losses_count_against():
    closes = _flat()
    v = lambda f: next(r for r in analyze(closes, f).reasons if r.rule == "valuation")
    assert v({"price_to_earnings_ratio": 10.0}).vote == 1
    assert v({"price_to_earnings_ratio": 50.0}).vote == -1
    assert v({"price_to_earnings_ratio": 25.0}).vote == 0
    assert v({"price_to_earnings_ratio": -5.0}).vote == -1


def test_cli_source_selection(monkeypatch, capsys, tmp_path):
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    assert cli.make_source("auto").name == "yfinance"
    assert cli.make_source("auto", api_key="k").name == "financialdatasets+yfinance"
    wl = tmp_path / "w.toml"
    wl.write_text('[[stocks]]\nsymbol = "AAPL"\n')
    code = cli.main(["--watchlist", str(wl), "--db", str(tmp_path / "p.db"),
                     "fetch", "--source", "financialdatasets"])
    assert code == 2
    assert API_KEY_ENV in capsys.readouterr().err
