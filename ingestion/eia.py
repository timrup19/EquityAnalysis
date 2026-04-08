"""
EIA data ingestion — pull grid & energy macro indicators.

Uses EIA API v2 (free, requires EIA_API_KEY from .env).

Two series:
  1. Electricity retail sales (all sectors, US total, annual)
     Route: /v2/electricity/retail-sales/data/
     Proxy for end market demand volume.

  2. Electricity generation (all sectors, US total, annual)
     Route: /v2/electricity/electric-power-operational-data/data/
     Proxy for grid capacity utilization & investment cycle.
     NOTE: EIA API v2 has no direct CAPEX route. The v1 ELEC.CAPEX series
     is not available as a native v2 endpoint. Generation growth is the
     best available API proxy for grid capex cycle. For actual utility
     capex figures, use earnings reports or FERC Form 1 data.

When a new generation datapoint is >10% above the prior year, writes a
capex_increase signal to supply_chain_signals for all tier 0 companies.

Usage:
    python -m ingestion.eia
    python -m ingestion.eia --signal-only   # skip fetch, just check for signals

Storage: eia_series table.
"""

import os
import argparse
from datetime import date

import requests
import psycopg2
from dotenv import load_dotenv

load_dotenv()

# ── Configuration ────────────────────────────────────────────────────────────

EIA_BASE = "https://api.eia.gov/v2"

# Series definitions: (internal_series_id, api_route, data_column, facets, description, units)
SERIES = [
    {
        "series_id": "ELEC.SALES.US-ALL.A",
        "series_name": "Electricity Retail Sales, US Total, All Sectors, Annual",
        "route": "/electricity/retail-sales/data/",
        "data_col": "sales",
        "facets": {"stateid": ["US"], "sectorid": ["ALL"]},
        "units": "million kilowatt-hours",
    },
    {
        "series_id": "ELEC.GEN.US-ALL.A",
        "series_name": "Electricity Net Generation, US Total, All Sectors, Annual",
        "route": "/electricity/electric-power-operational-data/data/",
        "data_col": "generation",
        "facets": {"location": ["US"], "sectorid": ["99"]},
        "units": "thousand megawatt-hours",
    },
]

# Threshold for capex_increase signal: >10% YoY increase in generation
SIGNAL_THRESHOLD_PCT = 0.10


# ── EIA API ──────────────────────────────────────────────────────────────────

def get_api_key():
    key = os.environ.get("EIA_API_KEY", "")
    if not key:
        raise RuntimeError(
            "EIA_API_KEY is not set. Get a free key at https://www.eia.gov/opendata/"
        )
    return key


def fetch_series(series_def):
    """Fetch data for a single series definition from EIA API v2.

    Returns list of dicts: [{period, value}, ...]
    """
    api_key = get_api_key()
    url = EIA_BASE + series_def["route"]

    params = {
        "api_key": api_key,
        "frequency": "annual",
        "sort[0][column]": "period",
        "sort[0][direction]": "asc",
        "length": "100",
    }
    # Add data column
    params["data[]"] = series_def["data_col"]
    # Add facets
    for facet_name, facet_values in series_def["facets"].items():
        for val in facet_values:
            params[f"facets[{facet_name}][]"] = val

    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    rows = []
    for record in data.get("response", {}).get("data", []):
        val = record.get(series_def["data_col"])
        if val is None:
            continue
        rows.append({
            "period": record["period"],
            "value": float(val),
        })

    return rows


# ── Database ─────────────────────────────────────────────────────────────────

def get_connection():
    return psycopg2.connect(os.environ["DATABASE_URL"])


def upsert_eia_rows(cur, series_def, rows):
    """Upsert fetched EIA data into eia_series table."""
    inserted = 0
    for row in rows:
        cur.execute(
            """
            INSERT INTO eia_series (series_id, series_name, period, value, units)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (series_id, period)
            DO UPDATE SET
                value      = EXCLUDED.value,
                updated_at = NOW()
            """,
            (
                series_def["series_id"],
                series_def["series_name"],
                row["period"],
                row["value"],
                series_def["units"],
            ),
        )
        inserted += 1
    return inserted


def get_last_two_values(cur, series_id):
    """Return the two most recent (period, value) rows for a series, ordered asc."""
    cur.execute(
        """
        SELECT period, value FROM eia_series
        WHERE series_id = %s
        ORDER BY period DESC
        LIMIT 2
        """,
        (series_id,),
    )
    rows = cur.fetchall()
    # Return in chronological order: [older, newer]
    return list(reversed(rows))


def get_tier0_companies(cur):
    """Return list of (id, ticker, name) tuples for all tier 0 companies."""
    cur.execute("SELECT id, ticker, name FROM companies WHERE tier = 0")
    return cur.fetchall()


# ── Signal Detection ─────────────────────────────────────────────────────────

def check_and_write_signals(conn):
    """Check the generation series for >10% YoY increase.

    If detected, write a capex_increase signal for every tier 0 company.
    Returns number of signals written.
    """
    cur = conn.cursor()

    # Use the generation series as capex proxy
    gen_series_id = "ELEC.GEN.US-ALL.A"
    last_two = get_last_two_values(cur, gen_series_id)

    if len(last_two) < 2:
        print("  Not enough generation data for YoY comparison.")
        cur.close()
        return 0

    older_period, older_val = last_two[0]
    newer_period, newer_val = last_two[1]

    if older_val == 0:
        cur.close()
        return 0

    # Staleness check: if the newest datapoint is >1 year behind current year,
    # the data is too old to generate actionable signals.
    current_year = date.today().year
    try:
        newer_year = int(newer_period[:4])
    except (ValueError, IndexError):
        newer_year = 0
    if newer_year < current_year - 1:
        print(f"  WARNING: newest EIA data is from {newer_period}, "
              f"current year is {current_year}. Data is stale — skipping signal.")
        cur.close()
        return 0

    yoy_change = (newer_val - older_val) / older_val
    print(f"  Generation YoY: {older_period}={older_val:,.0f} → "
          f"{newer_period}={newer_val:,.0f} ({yoy_change:+.1%})")

    if yoy_change <= SIGNAL_THRESHOLD_PCT:
        print(f"  YoY change {yoy_change:+.1%} below {SIGNAL_THRESHOLD_PCT:.0%} threshold. No signal.")
        cur.close()
        return 0

    # Check if we already wrote signals for this period
    cur.execute(
        """
        SELECT id FROM supply_chain_signals
        WHERE signal_type = 'capex_increase'
          AND source_doc = %s
        LIMIT 1
        """,
        (f"eia_{gen_series_id}_{newer_period}",),
    )
    if cur.fetchone():
        print(f"  Signal for {newer_period} already exists. Skipping.")
        cur.close()
        return 0

    # Write capex_increase signal for all tier 0 companies
    tier0 = get_tier0_companies(cur)
    today = date.today()
    signals_written = 0

    for company_id, ticker, name in tier0:
        cur.execute(
            """
            INSERT INTO supply_chain_signals
                (company_id, signal_type, signal_value, direction,
                 estimated_lag, source_doc, signal_date)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                company_id,
                "capex_increase",
                str(newer_val),
                "positive",
                3,  # estimated 2-4 quarters lag per GRAPH_DESIGN.md section 4
                f"eia_{gen_series_id}_{newer_period}",
                today,
            ),
        )
        signals_written += 1
        print(f"  SIGNAL: capex_increase for {ticker} ({name}), "
              f"generation +{yoy_change:.1%} in {newer_period}")

    conn.commit()
    cur.close()
    return signals_written


# ── Orchestration ────────────────────────────────────────────────────────────

def fetch_all(conn):
    """Fetch all EIA series and store in DB. Returns total rows upserted."""
    cur = conn.cursor()
    total = 0

    for series_def in SERIES:
        print(f"  Fetching {series_def['series_id']}...")
        rows = fetch_series(series_def)
        count = upsert_eia_rows(cur, series_def, rows)
        print(f"  {series_def['series_id']}: {count} rows upserted.")
        total += count

    conn.commit()
    cur.close()
    return total


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Pull EIA grid & energy data and check for capex signals."
    )
    parser.add_argument(
        "--signal-only", action="store_true",
        help="Skip data fetch, only check existing data for signals.",
    )
    args = parser.parse_args()

    conn = get_connection()
    try:
        if not args.signal_only:
            total = fetch_all(conn)
            print(f"  Total: {total} EIA rows upserted.")

        signals = check_and_write_signals(conn)
        print(f"\nDone. {signals} capex_increase signal(s) written.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
