"""
Supply chain scoring formulas — computed weekly.

Source: GRAPH_DESIGN.md section 5.

Scores:
    bottleneck_score        — how critical a supplier is to the downstream graph
    concentration_risk      — exposure to losing a key customer (risk flag)
    pricing_power_score     — relationship type distribution * margin trend
    upstream_demand_score   — capex increases at tier 0 propagated upstream
    composite_sc_score      — weighted combination of the four scores

    run_all_scores(conn)    — pull data, score every company, write to company_scores
"""

from datetime import date, timedelta
from statistics import mean

import networkx as nx
import pandas as pd
from scipy.stats import linregress
from dotenv import load_dotenv

from graph.build import build_graph

load_dotenv()


def _to_python(val):
    """Convert numpy scalar types to native Python types for psycopg2."""
    if hasattr(val, 'item'):
        return val.item()
    return val


# ── Individual scoring functions ─────────────────────────────────────────────

def bottleneck_score(company_id, G):
    """
    Measures how critical a supplier is to the downstream graph.

    Score = (downstream_dependent_companies / max_possible)
            * (1 / avg_substitution_ease)
    Normalized to 0–10.

    A downstream node n counts as "dependent" if ANY first-hop successor
    of company_id has substitution_ease <= 2 AND has a directed path to n.
    This captures multi-hop dependents behind hard-to-substitute edges.
    """
    downstream = nx.descendants(G, company_id)
    if not downstream:
        return 0.0

    edges = list(G.out_edges(company_id, data=True))
    if not edges:
        return 0.0

    # Identify first-hop successors that are hard to substitute
    hard_successors = [
        succ for _, succ, data in edges
        if data.get('substitution_ease', 3) <= 2
    ]

    # Count downstream nodes reachable through at least one hard-to-substitute
    # first-hop edge. A node n is dependent if any hard_successor has a path to n,
    # or if n is itself a hard_successor.
    dependent_count = 0
    for n in downstream:
        if n in hard_successors:
            dependent_count += 1
            continue
        for hs in hard_successors:
            if nx.has_path(G, hs, n):
                dependent_count += 1
                break

    avg_sub_ease = mean([e[2].get('substitution_ease', 3) for e in edges]) or 3

    raw = dependent_count / avg_sub_ease
    return min(raw * 2, 10)


def concentration_risk(company_id, edges_df):
    """
    Higher score = more risk.
    Uses revenue_dependency field from edges where supplier = company_id.
    """
    deps = edges_df[edges_df.supplier_id == company_id]['revenue_dependency'].dropna()
    if deps.empty:
        return 5.0  # unknown = medium risk

    top3 = deps.nlargest(3).sum()  # % revenue from top 3 customers
    sole_count = len(edges_df[
        (edges_df.supplier_id == company_id) &
        (edges_df.relationship_type == 'sole_source')
    ])
    # High concentration in sole_source customers is a double risk
    return min((top3 / 10) + (sole_count * 0.5), 10)


def pricing_power_score(company_id, edges_df, financials_df):
    """
    sole_source edges get weight 3, preferred weight 2, commodity weight 0.5.
    Multiplied by gross margin trend slope (positive slope = growing pricing power).
    """
    weights = {
        'sole_source': 3,
        'preferred_supplier': 2,
        'capacity_dependency': 1.5,
        'commodity_supplier': 0.5,
    }
    edges = edges_df[edges_df.supplier_id == company_id]
    rel_score = sum(
        weights.get(r, 1) for r in edges.relationship_type
    ) / max(len(edges), 1)

    margins = financials_df[
        financials_df.company_id == company_id
    ]['gross_margin'].tail(8)

    if len(margins) >= 4:
        slope = linregress(range(len(margins)), margins).slope
    else:
        slope = 0

    raw = rel_score * (1 + slope * 10)
    return min(max(raw, 0), 10)


def upstream_demand_score(company_id, G, signals_df):
    """
    Look at all tier 0 nodes downstream of company_id.
    Sum their recent capex_increase signal values, weighted by path length.
    Shorter path = stronger signal.
    """
    # Identify tier 0 nodes from graph node attributes
    tier0_ids = [
        n for n, data in G.nodes(data=True)
        if data.get('tier') == 0
    ]

    cutoff = date.today() - timedelta(days=180)
    score = 0.0

    for tier0_id in tier0_ids:
        if not nx.has_path(G, company_id, tier0_id):
            continue
        path_length = nx.shortest_path_length(G, company_id, tier0_id)
        recent_signals = signals_df[
            (signals_df.company_id == tier0_id) &
            (signals_df.signal_type == 'capex_increase') &
            (signals_df.signal_date >= cutoff)
        ]
        for _, sig in recent_signals.iterrows():
            score += float(sig.signal_value or 1) / (path_length ** 1.5)

    return min(score, 10)


def composite_sc_score(bottleneck, concentration_risk_val, pricing_power, upstream_demand):
    """
    Weights deliberately favour opportunity signals over risk flags.
    Concentration risk is subtracted — higher risk lowers the score.
    """
    return (
        bottleneck          * 0.35 +
        pricing_power       * 0.30 +
        upstream_demand     * 0.25 -
        concentration_risk_val * 0.10
    )


# ── Batch scoring ────────────────────────────────────────────────────────────

def run_all_scores(conn):
    """Pull data from DB, compute all four scores for every company,
    and write results to company_scores.

    Returns the number of rows written.
    """
    G = build_graph(conn)

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
        """
        SELECT company_id, signal_type, signal_value, signal_date
        FROM supply_chain_signals
        """,
        conn,
    )
    # Ensure signal_date is a Python date for comparison
    if not signals_df.empty:
        signals_df['signal_date'] = pd.to_datetime(signals_df['signal_date']).dt.date

    # financials_df: placeholder — Phase 1 has no financials table yet.
    # Create an empty frame with the expected columns so pricing_power_score
    # falls back to slope=0 gracefully.
    financials_df = pd.DataFrame(columns=['company_id', 'gross_margin'])

    companies_df = pd.read_sql("SELECT id, tier FROM companies", conn)
    today = date.today()
    rows = []

    for _, company in companies_df.iterrows():
        cid = _to_python(company.id)
        bn = bottleneck_score(cid, G)
        cr = concentration_risk(cid, edges_df)
        pp = pricing_power_score(cid, edges_df, financials_df)
        ud = upstream_demand_score(cid, G, signals_df)
        comp = composite_sc_score(bn, cr, pp, ud)

        rows.append(tuple(_to_python(v) for v in (cid, today, bn, cr, pp, ud, comp)))

    # Write to company_scores (upsert on unique company_id + score_date)
    cur = conn.cursor()
    for row in rows:
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
            row,
        )
    conn.commit()
    cur.close()
    print(f"Scored {len(rows)} companies for {today}.")
    return len(rows)


# ── CLI entry point ──────────────────────────────────────────────────────────

def main():
    """Run all supply chain scores from the command line.

    Usage:
        python -m graph.metrics
    """
    import os
    import psycopg2 as pg2
    conn = pg2.connect(os.environ["DATABASE_URL"])
    try:
        run_all_scores(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
