import json
from pathlib import Path

import numpy as np
import pandas as pd

from stock_analyst.cli import main
from stock_analyst.report import build_payload, from_snapshot, render_html
from stock_analyst.signals import analyze, rsi

SNAPSHOT = Path(__file__).resolve().parent.parent / "demo" / "snapshot.json"


def series(values):
    return pd.Series(values, index=pd.bdate_range("2025-01-01", periods=len(values)).date, dtype=float)


def test_steady_uptrend_is_buy_with_trend_reasons():
    rng = np.random.default_rng(1)
    values = 100 * np.cumprod(1 + 0.002 + rng.normal(0, 0.01, 260))
    rec = analyze(series(values))
    assert rec.action == "BUY"
    assert rec.score >= 2
    rules = {r.rule: r.vote for r in rec.reasons}
    assert rules["long_trend"] == 1 and rules["medium_trend"] == 1
    assert all(r.text for r in rec.reasons)


def test_steady_downtrend_is_sell():
    rng = np.random.default_rng(2)
    values = 100 * np.cumprod(1 - 0.003 + rng.normal(0, 0.01, 260))
    rec = analyze(series(values))
    assert rec.action == "SELL"
    assert rec.score <= -2


def test_short_history_skips_long_trend():
    rec = analyze(series(np.linspace(100, 90, 120)))
    long_trend = next(r for r in rec.reasons if r.rule == "long_trend")
    assert long_trend.vote == 0
    assert "200-day" in long_trend.text
    assert rec.metrics["sma200"] is None
    assert rec.max_score == 3


def test_too_little_history_is_hold():
    rec = analyze(series([10, 11, 12]))
    assert rec.action == "HOLD"
    assert "need at least" in rec.reasons[0].text


def test_rsi_extremes():
    assert rsi(series(np.linspace(10, 50, 40))).iloc[-1] == 100.0
    assert rsi(series(np.linspace(50, 10, 40))).iloc[-1] < 1


def test_report_from_snapshot_embeds_every_stock():
    stocks, meta = from_snapshot(SNAPSHOT)
    payload = build_payload(stocks, meta["as_of"], meta["note"])
    assert len(payload["stocks"]) == len(stocks) == 10
    html = render_html(payload)
    assert html.startswith("<!doctype html>")
    assert "/*__DATA__*/" not in html
    for s in payload["stocks"]:
        assert s["rec"]["action"] in {"BUY", "HOLD", "SELL"}
        assert len(s["dates"]) == len(s["close"]) == len(s["sma50"])
    # Payload must be strict JSON (no NaN).
    json.loads(json.dumps(payload, allow_nan=False))
    assert not render_html(payload, fragment=True).startswith("<!doctype")


def test_cli_report_from_snapshot(tmp_path, capsys):
    out = tmp_path / "r.html"
    assert main(["report", "--snapshot", str(SNAPSHOT), "-o", str(out)]) == 0
    assert out.read_text().count('"symbol"') == 10
    assert "10 stocks" in capsys.readouterr().out
