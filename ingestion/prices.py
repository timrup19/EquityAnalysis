"""
Daily price ingestion — pull OHLCV and market cap for all public companies.

Uses yfinance. Stores results in the prices table.

First run: pulls last 2 years of history.
Subsequent runs: pulls last 5 trading days and upserts (fills gaps, updates).

Usage:
    python -m ingestion.prices                # all public companies
    python -m ingestion.prices --ticker ETN   # single ticker
    python -m ingestion.prices --full         # force full 2-year reload
"""

import os
import argparse
from datetime import date, timedelta

import yfinance as yf
import psycopg2
from dotenv import load_dotenv

load_dotenv()


# ── Configuration ────────────────────────────────────────────────────────────

HISTORY_YEARS = 2
INCREMENTAL_DAYS = 10  # calendar days to cover ~5 trading days


# ── Database ─────────────────────────────────────────────────────────────────

def get_connection():
    return psycopg2.connect(os.environ["DATABASE_URL"])


def get_latest_price_date(cur, company_id):
    """Return the most recent price date in DB for a company, or None."""
    cur.execute(
        "SELECT MAX(date) FROM prices WHERE company_id = %s",
        (company_id,),
    )
    row = cur.fetchone()
    return row[0] if row and row[0] else None


def upsert_prices(cur, company_id, ticker, rows):
    """Insert price rows, updating on conflict (company_id, date)."""
    inserted = 0
    for row in rows:
        cur.execute(
            """
            INSERT INTO prices
                (company_id, ticker, date, open, high, low, close, volume, market_cap)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (company_id, date)
            DO UPDATE SET
                open       = EXCLUDED.open,
                high       = EXCLUDED.high,
                low        = EXCLUDED.low,
                close      = EXCLUDED.close,
                volume     = EXCLUDED.volume,
                market_cap = EXCLUDED.market_cap
            """,
            (company_id, ticker, *row),
        )
        inserted += 1
    return inserted


# ── yfinance Download ────────────────────────────────────────────────────────

def fetch_prices(ticker, start_date, end_date):
    """Download OHLCV from yfinance. Returns list of tuples:
    (date, open, high, low, close, volume, market_cap)
    """
    yf_ticker = yf.Ticker(ticker)

    # Get market cap (current snapshot — yfinance doesn't provide historical market cap)
    info = yf_ticker.info or {}
    market_cap = info.get("marketCap")

    hist = yf_ticker.history(start=start_date, end=end_date, auto_adjust=True)
    if hist.empty:
        return []

    rows = []
    for idx, r in hist.iterrows():
        rows.append((
            idx.date(),
            round(r["Open"], 4) if r["Open"] == r["Open"] else None,
            round(r["High"], 4) if r["High"] == r["High"] else None,
            round(r["Low"], 4) if r["Low"] == r["Low"] else None,
            round(r["Close"], 4) if r["Close"] == r["Close"] else None,
            int(r["Volume"]) if r["Volume"] == r["Volume"] else None,
            market_cap,
        ))
    return rows


# ── Orchestration ────────────────────────────────────────────────────────────

def download_for_company(conn, company_id, ticker, force_full=False):
    """Download prices for a single company.

    Returns number of rows upserted.
    """
    cur = conn.cursor()
    today = date.today()

    if force_full:
        start_date = today - timedelta(days=HISTORY_YEARS * 365)
    else:
        latest = get_latest_price_date(cur, company_id)
        if latest is None:
            # First run — pull 2 years
            start_date = today - timedelta(days=HISTORY_YEARS * 365)
        else:
            # Incremental — last 10 calendar days (~5 trading days)
            start_date = today - timedelta(days=INCREMENTAL_DAYS)

    end_date = today + timedelta(days=1)  # yfinance end is exclusive

    rows = fetch_prices(ticker, start_date.isoformat(), end_date.isoformat())
    if not rows:
        print(f"  {ticker}: no price data returned.")
        cur.close()
        return 0

    count = upsert_prices(cur, company_id, ticker, rows)
    conn.commit()
    cur.close()
    return count


def download_all(conn, ticker_filter=None, force_full=False):
    """Download prices for all public companies in the database."""
    cur = conn.cursor()
    if ticker_filter:
        cur.execute(
            "SELECT id, ticker FROM companies WHERE ticker = %s AND is_public = TRUE",
            (ticker_filter,),
        )
    else:
        cur.execute(
            "SELECT id, ticker FROM companies WHERE ticker IS NOT NULL AND is_public = TRUE"
        )
    companies = cur.fetchall()
    cur.close()

    total = 0
    for company_id, ticker in companies:
        count = download_for_company(conn, company_id, ticker, force_full=force_full)
        mode = "full" if force_full else "incremental"
        print(f"  {ticker}: {count} rows upserted ({mode}).")
        total += count

    return total


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Pull daily OHLCV + market cap via yfinance."
    )
    parser.add_argument(
        "--ticker", type=str, default=None,
        help="Download for a single ticker only.",
    )
    parser.add_argument(
        "--full", action="store_true",
        help="Force full 2-year history reload (ignores existing data).",
    )
    args = parser.parse_args()

    conn = get_connection()
    try:
        total = download_all(conn, ticker_filter=args.ticker, force_full=args.full)
        print(f"\nDone. {total} total price rows upserted.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
