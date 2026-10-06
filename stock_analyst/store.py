"""SQLite storage for daily prices. Later steps (signals, dashboard) read from here."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from .sources import PRICE_COLUMNS

DEFAULT_DB_PATH = Path("data/prices.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS prices (
    symbol    TEXT NOT NULL,
    date      TEXT NOT NULL,          -- YYYY-MM-DD, exchange-local trading day
    open      REAL,
    high      REAL,
    low       REAL,
    close     REAL NOT NULL,
    adj_close REAL,
    volume    INTEGER,
    PRIMARY KEY (symbol, date)
);
CREATE TABLE IF NOT EXISTS securities (
    symbol      TEXT PRIMARY KEY,
    name        TEXT,
    currency    TEXT,
    source      TEXT,
    updated_at  TEXT                  -- UTC ISO timestamp of last successful fetch
);
CREATE TABLE IF NOT EXISTS fetch_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol      TEXT NOT NULL,
    fetched_at  TEXT NOT NULL,
    rows        INTEGER NOT NULL,
    error       TEXT
);
"""


class PriceStore:
    def __init__(self, path: str | Path = DEFAULT_DB_PATH):
        self.path = Path(path)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "PriceStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def upsert_prices(self, df: pd.DataFrame) -> int:
        if df.empty:
            return 0
        rows = [
            (
                r.symbol,
                _iso(r.date),
                _num(r.open),
                _num(r.high),
                _num(r.low),
                float(r.close),
                _num(r.adj_close),
                None if pd.isna(r.volume) else int(r.volume),
            )
            for r in df[PRICE_COLUMNS].itertuples(index=False)
        ]
        with self.conn:
            self.conn.executemany(
                "INSERT OR REPLACE INTO prices VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows
            )
        return len(rows)

    def upsert_security(
        self, symbol: str, name: str, currency: str | None, source: str
    ) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO securities (symbol, name, currency, source, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    name = excluded.name,
                    currency = COALESCE(excluded.currency, securities.currency),
                    source = excluded.source,
                    updated_at = excluded.updated_at
                """,
                (symbol, name, currency, source, _now()),
            )

    def log_fetch(self, symbol: str, rows: int, error: str | None = None) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO fetch_log (symbol, fetched_at, rows, error) VALUES (?, ?, ?, ?)",
                (symbol, _now(), rows, error),
            )

    def last_date(self, symbol: str) -> date | None:
        (value,) = self.conn.execute(
            "SELECT MAX(date) FROM prices WHERE symbol = ?", (symbol,)
        ).fetchone()
        return date.fromisoformat(value) if value else None

    def get_prices(
        self, symbol: str, start: date | None = None, end: date | None = None
    ) -> pd.DataFrame:
        """Daily bars for one symbol, oldest first, indexed by date."""
        query = "SELECT * FROM prices WHERE symbol = ?"
        params: list = [symbol]
        if start:
            query += " AND date >= ?"
            params.append(start.isoformat())
        if end:
            query += " AND date <= ?"
            params.append(end.isoformat())
        df = pd.read_sql_query(query + " ORDER BY date", self.conn, params=params)
        df["date"] = pd.to_datetime(df["date"])
        return df.set_index("date")

    def securities(self) -> pd.DataFrame:
        return pd.read_sql_query(
            """
            SELECT s.symbol, s.name, s.currency, s.source, s.updated_at,
                   COUNT(p.date) AS days, MIN(p.date) AS first_date, MAX(p.date) AS last_date
            FROM securities s LEFT JOIN prices p ON p.symbol = s.symbol
            GROUP BY s.symbol ORDER BY s.symbol
            """,
            self.conn,
        )

    def latest_closes(self) -> pd.DataFrame:
        """Most recent close and the one before it for every stored symbol."""
        return pd.read_sql_query(
            """
            WITH ranked AS (
                SELECT symbol, date, close,
                       ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY date DESC) AS rn
                FROM prices
            )
            SELECT cur.symbol, cur.date, cur.close, prev.close AS prev_close
            FROM ranked cur LEFT JOIN ranked prev
              ON prev.symbol = cur.symbol AND prev.rn = 2
            WHERE cur.rn = 1 ORDER BY cur.symbol
            """,
            self.conn,
        )


def _iso(value) -> str:
    if isinstance(value, str):
        return date.fromisoformat(value[:10]).isoformat()
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, pd.Timestamp):
        return value.date().isoformat()
    return value.isoformat()


def _num(value) -> float | None:
    return None if pd.isna(value) else float(value)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
