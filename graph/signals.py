"""
Signal detection and upstream propagation.

Source: GRAPH_DESIGN.md section 6.

When a tier 0 event is detected (e.g. NextEra announces $7B capex increase):
1. AI agent extracts signal → writes to supply_chain_signals
2. This module traverses upstream from the tier 0 company
3. Recomputes scores for each upstream company
4. Writes alerts for any company whose composite_sc_score crosses 7.0
"""

import sys
from datetime import date

import pandas as pd
import psycopg2
from dotenv import load_dotenv

from graph.build import build_graph, traverse_upstream
from graph.metrics import (
    _to_python,
    bottleneck_score,
    concentration_risk,
    pricing_power_score,
    upstream_demand_score,
    composite_sc_score,
)

load_dotenv()


def traverse_upstream_from_event(conn, company_id):
    """Process a signal event for a tier 0 company.

    Steps (per GRAPH_DESIGN.md section 6):
    1. Build the current graph from DB
    2. Find all upstream suppliers of company_id
    3. Recompute all four scores + composite for each upstream company
    4. If composite_sc_score >= 7.0, write an alert signal to supply_chain_signals
    5. Upsert updated scores to company_scores

    Args:
        conn: psycopg2 connection
        company_id: the tier 0 company that triggered the event

    Returns:
        list of dicts: companies that crossed the 7.0 alert threshold,
                       each with {company_id, name, ticker, composite_sc_score}
    """
    G = build_graph(conn)

    # Verify the trigger company is tier 0
    node_data = G.nodes.get(company_id, {})
    if node_data.get('tier') != 0:
        print(f"Warning: company {company_id} is tier {node_data.get('tier')}, not tier 0.")

    # Get all upstream suppliers (no depth limit for signal propagation)
    upstream = traverse_upstream(G, company_id, max_depth=10)

    if not upstream:
        print(f"No upstream suppliers found for company {company_id}.")
        return []

    # Load data needed for scoring
    edges_df = pd.read_sql(
        """
        SELECT supplier_id, customer_id, relationship_type,
               substitution_ease, revenue_dependency
        FROM supply_chain_edges
        WHERE valid_to IS NULL AND confidence >= 0.6
        """,
        conn,
    )
    signals_df = pd.read_sql(
        "SELECT company_id, signal_type, signal_value, signal_date "
        "FROM supply_chain_signals",
        conn,
    )
    if not signals_df.empty:
        signals_df['signal_date'] = pd.to_datetime(signals_df['signal_date']).dt.date

    # No financials table yet — empty placeholder
    financials_df = pd.DataFrame(columns=['company_id', 'gross_margin'])

    companies_df = pd.read_sql("SELECT id, ticker, name, tier FROM companies", conn)
    company_lookup = companies_df.set_index('id')

    today = date.today()
    alerts = []
    cur = conn.cursor()

    for upstream_id, path_length in sorted(upstream.items(), key=lambda x: x[1]):
        bn = bottleneck_score(upstream_id, G)
        cr = concentration_risk(upstream_id, edges_df)
        pp = pricing_power_score(upstream_id, edges_df, financials_df)
        ud = upstream_demand_score(upstream_id, G, signals_df)
        comp = composite_sc_score(bn, cr, pp, ud)

        # Upsert score
        cur.execute(
            """
            INSERT INTO company_scores
                (company_id, score_date, bottleneck_score, concentration_risk,
                 pricing_power_score, upstream_demand_score, composite_sc_score)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (company_id, score_date)
            DO UPDATE SET
                bottleneck_score      = EXCLUDED.bottleneck_score,
                concentration_risk    = EXCLUDED.concentration_risk,
                pricing_power_score   = EXCLUDED.pricing_power_score,
                upstream_demand_score = EXCLUDED.upstream_demand_score,
                composite_sc_score    = EXCLUDED.composite_sc_score
            """,
            tuple(_to_python(v) for v in (upstream_id, today, bn, cr, pp, ud, comp)),
        )

        # Alert if composite crosses threshold
        if comp >= 7.0:
            info = company_lookup.loc[upstream_id] if upstream_id in company_lookup.index else {}
            ticker = info.get('ticker', '?')
            name = info.get('name', '?')

            # Write alert signal
            cur.execute(
                """
                INSERT INTO supply_chain_signals
                    (company_id, signal_type, signal_value, direction,
                     estimated_lag, source_doc, signal_date)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    upstream_id,
                    'composite_threshold_alert',
                    f"composite_sc_score={comp:.2f} (triggered by company {company_id})",
                    'positive',
                    None,
                    f"signal_propagation_from_{company_id}",
                    today,
                ),
            )

            alerts.append({
                'company_id': upstream_id,
                'ticker': ticker,
                'name': name,
                'composite_sc_score': round(comp, 2),
                'path_length': path_length,
            })
            print(f"ALERT: {ticker} ({name}) — composite {comp:.1f}, "
                  f"{path_length} hops from trigger")

    conn.commit()
    cur.close()

    if not alerts:
        print("No companies crossed the 7.0 composite threshold.")

    return alerts


# ── CLI entry point ──────────────────────────────────────────────────────────

def main():
    """Run signal propagation from command line.

    Usage:
        python -m graph.signals <company_id>
    """
    if len(sys.argv) != 2:
        print("Usage: python -m graph.signals <company_id>")
        sys.exit(1)

    try:
        company_id = int(sys.argv[1])
    except ValueError:
        print(f"Error: company_id must be an integer, got '{sys.argv[1]}'")
        sys.exit(1)

    import os
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        alerts = traverse_upstream_from_event(conn, company_id)
        print(f"\n{len(alerts)} alert(s) fired.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
