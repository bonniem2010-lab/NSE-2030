"""Buy / hold / sell calls from daily closes, each backed by plain-English reasons.

Every rule looks at one thing (long-term trend, medium-term trend, momentum,
overbought/oversold, and, when fundamentals are available, valuation and
growth) and votes +1, 0 or -1. The votes add up to a score:
+2 or more is a BUY, -2 or less is a SELL, anything between is a HOLD.
Rules that need more history than is available are skipped and say so.

This is a rules-of-thumb screen, not financial advice.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import pandas as pd

BUY_THRESHOLD = 2
SELL_THRESHOLD = -2

LONG_WINDOW = 200
MEDIUM_WINDOW = 50
MOMENTUM_DAYS = 63  # about three months of trading days
YEAR_DAYS = 252
RSI_DAYS = 14
MOMENTUM_BAND = 0.10  # +/-10% over three months counts as strong momentum
RSI_HIGH = 70
RSI_LOW = 30
# Valuation: PEG (P/E divided by earnings growth) when available, otherwise plain P/E.
PEG_CHEAP = 1.0
PEG_RICH = 2.5
PE_CHEAP = 15.0
PE_RICH = 40.0
# Growth: year-on-year revenue growth.
GROWTH_STRONG = 0.10
SCORED_RULES = {"long_trend", "medium_trend", "momentum", "rsi", "valuation", "growth"}


@dataclass(frozen=True)
class Reason:
    rule: str
    vote: int  # +1 supports buying, -1 supports selling, 0 is context only
    text: str


@dataclass(frozen=True)
class Recommendation:
    action: str  # BUY, HOLD or SELL
    score: int
    max_score: int
    reasons: tuple[Reason, ...]
    metrics: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["reasons"] = [asdict(r) for r in self.reasons]
        return d


def sma(closes: pd.Series, window: int) -> pd.Series:
    return closes.rolling(window, min_periods=window).mean()


def rsi(closes: pd.Series, days: int = RSI_DAYS) -> pd.Series:
    """Wilder's relative strength index, 0-100."""
    delta = closes.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / days, min_periods=days, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / days, min_periods=days, adjust=False).mean()
    rs = gain / loss
    out = 100 - 100 / (1 + rs)
    return out.where(loss != 0, 100.0)


def fundamental_reasons(f: dict | None) -> list[Reason]:
    """Valuation and growth votes from a fundamentals snapshot (decimals: 0.12 = 12%)."""
    if not f:
        return []
    reasons: list[Reason] = []
    pe = f.get("price_to_earnings_ratio")
    peg = f.get("peg_ratio")

    # 5. Valuation.
    if pe is not None and pe <= 0:
        reasons.append(Reason("valuation", -1,
            "Unprofitable: earnings over the last year are negative, so there is no "
            "P/E to support the price."))
    elif peg is not None and peg > 0 and pe is not None:
        if peg <= PEG_CHEAP:
            reasons.append(Reason("valuation", 1,
                f"Cheap for its growth: P/E of {pe:.0f} with a PEG of {peg:.1f} "
                f"(at or below {PEG_CHEAP:.0f})."))
        elif peg >= PEG_RICH:
            reasons.append(Reason("valuation", -1,
                f"Expensive for its growth: P/E of {pe:.0f} with a PEG of {peg:.1f} "
                f"(above {PEG_RICH})."))
        else:
            reasons.append(Reason("valuation", 0,
                f"Fairly valued for its growth: P/E of {pe:.0f}, PEG {peg:.1f}."))
    elif pe is not None:
        if pe <= PE_CHEAP:
            reasons.append(Reason("valuation", 1,
                f"Cheap on earnings: P/E of {pe:.0f} (at or below {PE_CHEAP:.0f})."))
        elif pe >= PE_RICH:
            reasons.append(Reason("valuation", -1,
                f"Expensive on earnings: P/E of {pe:.0f} (above {PE_RICH:.0f})."))
        else:
            reasons.append(Reason("valuation", 0, f"P/E of {pe:.0f}, neither cheap nor expensive."))

    # 6. Growth.
    growth = f.get("revenue_growth")
    if growth is not None:
        if growth >= GROWTH_STRONG:
            reasons.append(Reason("growth", 1, f"Growing: revenue up {growth:.0%} on a year ago."))
        elif growth < 0:
            reasons.append(Reason("growth", -1, f"Shrinking: revenue down {-growth:.0%} on a year ago."))
        else:
            reasons.append(Reason("growth", 0, f"Slow growth: revenue up {growth:.0%} on a year ago."))

    # Context only: profitability and debt.
    context = []
    if f.get("net_margin") is not None:
        context.append(f"net margin {f['net_margin']:.0%}")
    if f.get("return_on_equity") is not None:
        context.append(f"return on equity {f['return_on_equity']:.0%}")
    if f.get("debt_to_equity") is not None:
        context.append(f"debt-to-equity {f['debt_to_equity']:.1f}")
    if f.get("free_cash_flow_yield") is not None:
        context.append(f"free-cash-flow yield {f['free_cash_flow_yield']:.1%}")
    if context:
        reasons.append(Reason("quality", 0, "Fundamentals: " + ", ".join(context) + "."))
    return reasons


def analyze(closes: pd.Series, fundamentals: dict | None = None) -> Recommendation:
    """Recommend BUY, HOLD or SELL for one stock from its daily closes, oldest first.

    ``fundamentals`` is an optional snapshot (see ``financialdatasets.FUNDAMENTAL_FIELDS``)
    that adds valuation and growth votes.
    """
    closes = closes.dropna().astype(float)
    if len(closes) < MEDIUM_WINDOW:
        # Too little history for any price rule; fundamentals are shown but the call stays HOLD.
        return Recommendation(
            "HOLD",
            0,
            0,
            (Reason("history", 0, f"Only {len(closes)} days of prices; need at least "
                    f"{MEDIUM_WINDOW} to judge the trend, so no call is made."),
             *(Reason(r.rule, 0, r.text)
               for r in fundamental_reasons(fundamentals))),
            {"price": float(closes.iloc[-1]) if len(closes) else None},
        )

    price = float(closes.iloc[-1])
    sma50 = float(sma(closes, MEDIUM_WINDOW).iloc[-1])
    sma200_series = sma(closes, LONG_WINDOW)
    sma200 = float(sma200_series.iloc[-1]) if len(closes) >= LONG_WINDOW else None
    rsi14 = float(rsi(closes).iloc[-1])
    year = closes.iloc[-YEAR_DAYS:]
    high, low = float(year.max()), float(year.min())

    reasons: list[Reason] = []

    # 1. Long-term trend: price against its 200-day average.
    if sma200 is not None:
        gap = price / sma200 - 1
        if gap >= 0:
            reasons.append(Reason("long_trend", 1,
                f"Long-term uptrend: the price is {gap:.0%} above its 200-day average."))
        else:
            reasons.append(Reason("long_trend", -1,
                f"Long-term downtrend: the price is {-gap:.0%} below its 200-day average."))
    else:
        reasons.append(Reason("long_trend", 0,
            f"Not enough history for a 200-day average ({len(closes)} days available), "
            "so the long-term trend is not scored."))

    # 2. Medium-term trend: 50-day average against the 200-day (golden / death cross),
    #    or against the price when there is no 200-day average yet.
    if sma200 is not None:
        if sma50 >= sma200:
            reasons.append(Reason("medium_trend", 1,
                "The 50-day average is above the 200-day average (a \"golden cross\" "
                "setup), so the medium-term trend is pointing up."))
        else:
            reasons.append(Reason("medium_trend", -1,
                "The 50-day average is below the 200-day average (a \"death cross\" "
                "setup), so the medium-term trend is pointing down."))
    else:
        gap = price / sma50 - 1
        vote = 1 if gap >= 0 else -1
        side = "above" if gap >= 0 else "below"
        reasons.append(Reason("medium_trend", vote,
            f"The price is {abs(gap):.0%} {side} its 50-day average."))

    # 3. Momentum: three-month return.
    if len(closes) > MOMENTUM_DAYS:
        ret = price / float(closes.iloc[-MOMENTUM_DAYS - 1]) - 1
        if ret >= MOMENTUM_BAND:
            reasons.append(Reason("momentum", 1,
                f"Strong momentum: up {ret:.0%} over the last three months."))
        elif ret <= -MOMENTUM_BAND:
            reasons.append(Reason("momentum", -1,
                f"Weak momentum: down {-ret:.0%} over the last three months."))
        else:
            reasons.append(Reason("momentum", 0,
                f"Momentum is flat: {ret:+.0%} over the last three months."))
    else:
        ret = None

    # 4. Overbought / oversold.
    if rsi14 >= RSI_HIGH:
        reasons.append(Reason("rsi", -1,
            f"Overbought: 14-day RSI is {rsi14:.0f} (above {RSI_HIGH}), so the recent run "
            "looks stretched and a pullback is more likely."))
    elif rsi14 <= RSI_LOW:
        reasons.append(Reason("rsi", 1,
            f"Oversold: 14-day RSI is {rsi14:.0f} (below {RSI_LOW}), so selling looks "
            "overdone and a rebound is more likely."))
    else:
        reasons.append(Reason("rsi", 0,
            f"RSI is {rsi14:.0f}, neither overbought nor oversold."))

    # Context only: where the price sits in its one-year range.
    span = "52-week" if len(closes) >= YEAR_DAYS else f"{len(year)}-day"
    from_high = price / high - 1
    if from_high > -0.02:
        reasons.append(Reason("range", 0, f"Trading at or near its {span} high."))
    elif price / low - 1 < 0.05:
        reasons.append(Reason("range", 0, f"Trading near its {span} low."))
    else:
        reasons.append(Reason("range", 0, f"{-from_high:.0%} below its {span} high."))

    reasons.extend(fundamental_reasons(fundamentals))

    score = sum(r.vote for r in reasons)
    max_score = sum(1 for r in reasons if r.rule in SCORED_RULES
                    and not (r.rule == "long_trend" and sma200 is None))
    if score >= BUY_THRESHOLD:
        action = "BUY"
    elif score <= SELL_THRESHOLD:
        action = "SELL"
    else:
        action = "HOLD"

    metrics = {
        "price": price,
        "sma50": sma50,
        "sma200": sma200,
        "rsi14": rsi14,
        "return_3m": ret,
        "high_52w": high,
        "low_52w": low,
        "range_label": span,
        "days": len(closes),
        "pe": (fundamentals or {}).get("price_to_earnings_ratio"),
        "peg": (fundamentals or {}).get("peg_ratio"),
        "revenue_growth": (fundamentals or {}).get("revenue_growth"),
    }
    return Recommendation(action, score, max_score, tuple(reasons), metrics)
