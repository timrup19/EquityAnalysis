"""
Composite scoring model — combines quality, momentum, valuation, and
supply chain position into a single 0–100 investment score.

Source: ARCHITECTURE.md section 3, Layer 4 spec.

Weights:
    Quality           30%
    Momentum          20%
    Valuation         20%
    Supply chain      30%

Usage:
    python -m scoring.composite
"""

import os
import argparse
from datetime import date, timedelta

import numpy as np
import pandas as pd
import psycopg2
from dotenv import load_dotenv

load_dotenv()


def _to_python(val):
    """Convert numpy scalar types to native Python types for psycopg2."""
    if hasattr(val, 'item'):
        return val.item()
    return val


# ── Configuration ────────────────────────────────────────────────────────────

WEIGHTS = {
    "quality": 0.30,
    "momentum": 0.20,
    "valuation": 0.20,
    "supply_chain": 0.30,
}

ROIC_HURDLE = 0.10  # 10% ROIC hurdle rate


# ── Database ─────────────────────────────────────────────────────────────────

def get_connection():
    return psycopg2.connect(os.environ["DATABASE_URL"])


# ── Quality Score (0–10) ─────────────────────────────────────────────────────

def quality_score(company_id, financials_df, prices_df):
    """Score based on FCF yield, ROIC vs hurdle, gross margin trend, debt proxy.

    prices_df is required because FCF yield = FCF / market_cap, and
    market_cap comes from the most recent price row.

    Components (each 0–2.5, summed to 0–10):
    1. FCF yield: FCF / market_cap. Higher = better.
    2. ROIC vs 10% hurdle: points for exceeding hurdle.
    3. Gross margin trend: slope over last 4+ periods.
    4. Balance sheet proxy: positive FCF streak as quality signal.
       (Full debt/equity requires balance sheet data not yet extracted.)
    """
    fin = financials_df[financials_df.company_id == company_id].sort_values("period")
    if fin.empty:
        return 5.0  # no data = neutral

    latest = fin.iloc[-1]

    # 1. FCF yield (0–2.5)
    fcf = latest.get("free_cash_flow")
    # Get most recent market cap from prices
    price_rows = prices_df[prices_df.company_id == company_id].sort_values("date")
    if not price_rows.empty and fcf is not None:
        mcap = price_rows.iloc[-1].get("market_cap")
        if mcap and mcap > 0:
            fcf_yield = fcf / mcap
            # 5%+ yield = full marks, 0% = 0
            fcf_score = min(max(fcf_yield / 0.05, 0), 1) * 2.5
        else:
            fcf_score = 1.25  # unknown
    else:
        fcf_score = 1.25

    # 2. ROIC vs hurdle (0–2.5)
    roic = latest.get("roic")
    if roic is not None and not pd.isna(roic):
        # ROIC of 20%+ = full marks, 10% hurdle = half marks, below = scaled down
        roic_ratio = roic / ROIC_HURDLE
        roic_score = min(max(roic_ratio - 0.5, 0), 1.5) / 1.5 * 2.5
    else:
        roic_score = 1.25  # unknown

    # 3. Gross margin trend (0–2.5)
    margins = fin["gross_margin"].dropna()
    if len(margins) >= 4:
        x = np.arange(len(margins))
        slope = np.polyfit(x, margins.values, 1)[0]
        # Positive slope = expanding margins
        if slope > 0.01:
            margin_score = 2.5
        elif slope > 0:
            margin_score = 1.75
        elif slope > -0.01:
            margin_score = 1.0
        else:
            margin_score = 0.25
    elif len(margins) >= 1:
        # Single margin value — score on absolute level
        m = margins.iloc[-1]
        margin_score = min(m / 0.40, 1) * 2.5 if m is not None else 1.25
    else:
        margin_score = 1.25

    # 4. Balance sheet proxy: consecutive positive FCF periods (0–2.5)
    fcf_series = fin["free_cash_flow"].dropna()
    if len(fcf_series) >= 2:
        positive_streak = 0
        for v in reversed(fcf_series.values):
            if v > 0:
                positive_streak += 1
            else:
                break
        # 4+ consecutive positive = full marks
        bs_score = min(positive_streak / 4, 1) * 2.5
    else:
        bs_score = 1.25

    return min(fcf_score + roic_score + margin_score + bs_score, 10)


# ── Momentum Score (0–10) ────────────────────────────────────────────────────

def momentum_score(company_id, prices_df):
    """Score based on price vs 52-week high and rate of change.

    Components (each 0–5, summed to 0–10):
    1. Price vs 52-week high: closer to high = stronger momentum.
    2. Rate of change: 3-month vs 12-month — accelerating = bonus.
    """
    prices = prices_df[prices_df.company_id == company_id].sort_values("date")
    if prices.empty:
        return 5.0

    today = prices["date"].max()
    year_ago = today - timedelta(days=365)
    three_months_ago = today - timedelta(days=90)

    year_prices = prices[prices["date"] >= year_ago]
    if year_prices.empty:
        return 5.0

    current_close = year_prices.iloc[-1]["close"]
    if current_close is None or pd.isna(current_close):
        return 5.0

    # 1. Price vs 52-week high (0–5)
    high_52w = year_prices["high"].max()
    if high_52w and high_52w > 0:
        pct_of_high = current_close / high_52w
        # 95%+ of high = full marks, 70% = 0
        high_score = min(max((pct_of_high - 0.70) / 0.25, 0), 1) * 5
    else:
        high_score = 2.5

    # 2. Rate of change: 3mo vs 12mo (0–5)
    three_mo_prices = prices[prices["date"] >= three_months_ago]
    twelve_mo_prices = prices[prices["date"] >= year_ago]

    roc_3m = None
    roc_12m = None

    if len(three_mo_prices) >= 2:
        first_3m = three_mo_prices.iloc[0]["close"]
        if first_3m and first_3m > 0:
            roc_3m = (current_close - first_3m) / first_3m

    if len(twelve_mo_prices) >= 2:
        first_12m = twelve_mo_prices.iloc[0]["close"]
        if first_12m and first_12m > 0:
            roc_12m = (current_close - first_12m) / first_12m

    if roc_3m is not None and roc_12m is not None:
        # Measures whether recent momentum is accelerating or decelerating:
        # compares actual 3-month return to what 3 months "should" produce
        # if the 12-month trend were evenly distributed. Positive = accelerating.
        acceleration = roc_3m - (roc_12m / 4)
        # Positive acceleration = good
        roc_score = min(max((acceleration + 0.05) / 0.15, 0), 1) * 5
    elif roc_3m is not None:
        roc_score = min(max((roc_3m + 0.05) / 0.20, 0), 1) * 5
    else:
        roc_score = 2.5

    return min(high_score + roc_score, 10)


# ── Valuation Score (0–10) ───────────────────────────────────────────────────

def valuation_score(company_id, financials_df, prices_df, sector_median_ev_fcf=None):
    """Score based on EV/FCF vs sector median.

    Lower EV/FCF relative to sector = higher score (cheaper = better).
    """
    fin = financials_df[financials_df.company_id == company_id].sort_values("period")
    prices = prices_df[prices_df.company_id == company_id].sort_values("date")

    if fin.empty or prices.empty:
        return 5.0

    latest_fin = fin.iloc[-1]
    fcf = latest_fin.get("free_cash_flow")
    if fcf is None or pd.isna(fcf) or fcf <= 0:
        return 3.0  # negative or zero FCF = poor valuation score

    # Use most recent market cap as EV proxy
    # (Full EV = market_cap + debt - cash; debt/cash not extracted yet)
    mcap = prices.iloc[-1].get("market_cap")
    if mcap is None or pd.isna(mcap) or mcap <= 0:
        return 5.0

    ev_fcf = mcap / fcf

    # Compute sector median if not provided
    if sector_median_ev_fcf is None:
        sector_median_ev_fcf = _compute_sector_median_ev_fcf(financials_df, prices_df)

    if sector_median_ev_fcf is None or sector_median_ev_fcf <= 0:
        # No sector context — score on absolute basis
        # EV/FCF < 15 = good, > 30 = expensive
        return min(max((30 - ev_fcf) / 15, 0), 1) * 10

    # Relative to sector median
    ratio = ev_fcf / sector_median_ev_fcf
    # ratio < 0.7 = cheap (10), ratio = 1.0 = fair (5), ratio > 1.5 = expensive (0)
    return min(max((1.5 - ratio) / 0.8, 0), 1) * 10


def _compute_sector_median_ev_fcf(financials_df, prices_df):
    """Compute sector median EV/FCF from all companies with data."""
    ev_fcfs = []
    for cid in financials_df["company_id"].unique():
        fin = financials_df[financials_df.company_id == cid].sort_values("period")
        prices = prices_df[prices_df.company_id == cid].sort_values("date")
        if fin.empty or prices.empty:
            continue
        fcf = fin.iloc[-1].get("free_cash_flow")
        mcap = prices.iloc[-1].get("market_cap")
        if (fcf is not None and not pd.isna(fcf) and fcf > 0
                and mcap is not None and not pd.isna(mcap) and mcap > 0):
            ev_fcfs.append(mcap / fcf)

    if len(ev_fcfs) >= 3:
        return float(np.median(ev_fcfs))
    print(f"  Sector median EV/FCF: only {len(ev_fcfs)} company(ies) "
          f"with data (need >= 3). Falling back to absolute valuation.")
    return None


# ── Composite Score (0–100) ──────────────────────────────────────────────────

def composite_score(quality, momentum, valuation, supply_chain):
    """Weighted combination per ARCHITECTURE.md Layer 4 spec.

    Each input is 0–10. Output is 0–100.
    Quality 30%, Momentum 20%, Valuation 20%, Supply chain 30%.
    """
    raw = (
        quality      * WEIGHTS["quality"] +
        momentum     * WEIGHTS["momentum"] +
        valuation    * WEIGHTS["valuation"] +
        supply_chain * WEIGHTS["supply_chain"]
    )
    # raw is 0–10, scale to 0–100
    return min(max(raw * 10, 0), 100)


# ── Batch scoring ────────────────────────────────────────────────────────────

def run_all_composite_scores(conn):
    """Pull data, compute all four dimension scores for every public company,
    and write results to composite_scores.

    Returns the number of rows written.
    """
    financials_df = pd.read_sql(
        "SELECT * FROM financials ORDER BY period", conn
    )
    prices_df = pd.read_sql(
        "SELECT company_id, date, close, high, market_cap FROM prices", conn
    )
    companies_df = pd.read_sql(
        "SELECT id, ticker, name FROM companies WHERE is_public = TRUE AND ticker IS NOT NULL",
        conn,
    )

    # Get latest supply chain scores
    sc_scores_df = pd.read_sql(
        """
        SELECT DISTINCT ON (company_id)
            company_id, composite_sc_score
        FROM company_scores
        ORDER BY company_id, score_date DESC
        """,
        conn,
    )
    sc_lookup = dict(zip(sc_scores_df.company_id, sc_scores_df.composite_sc_score))

    # Pre-compute sector median for valuation
    sector_median = _compute_sector_median_ev_fcf(financials_df, prices_df)

    today = date.today()
    rows = []

    for _, company in companies_df.iterrows():
        cid = _to_python(company.id)
        q = quality_score(cid, financials_df, prices_df)
        m = momentum_score(cid, prices_df)
        v = valuation_score(cid, financials_df, prices_df, sector_median)
        sc = sc_lookup.get(cid, 5.0)  # default 5.0 if no SC score yet
        comp = composite_score(q, m, v, sc)

        rows.append(tuple(_to_python(x) for x in (cid, today, q, m, v, sc, comp)))

    # Write to composite_scores
    cur = conn.cursor()
    for row in rows:
        cur.execute(
            """
            INSERT INTO composite_scores
                (company_id, score_date, quality_score, momentum_score,
                 valuation_score, supply_chain_score, composite_score)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (company_id, score_date)
            DO UPDATE SET
                quality_score      = EXCLUDED.quality_score,
                momentum_score     = EXCLUDED.momentum_score,
                valuation_score    = EXCLUDED.valuation_score,
                supply_chain_score = EXCLUDED.supply_chain_score,
                composite_score    = EXCLUDED.composite_score
            """,
            row,
        )
    conn.commit()
    cur.close()

    print(f"Scored {len(rows)} companies for {today}.")
    return len(rows)


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Run composite scoring model on all public companies."
    )
    parser.parse_args()

    conn = get_connection()
    try:
        count = run_all_composite_scores(conn)
        print(f"\nDone. {count} composite scores written.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
