"""
Portfolio Intelligence System — REST API

Endpoints:
    GET  /companies          — all companies with latest scores
    GET  /companies/{id}     — single company detail
    GET  /signals            — latest 50 supply chain signals
    GET  /watchlist          — top 10 public companies by composite_score
    GET  /graph/{id}         — Cytoscape.js graph neighbourhood (1–3 hops)
    POST /portfolio          — log a trade

Usage:
    uvicorn api.main:app --reload
"""

import os
from contextlib import contextmanager
from datetime import date
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator

load_dotenv()

app = FastAPI(title="EquityPro API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

MAX_GRAPH_NODES = 50


# ── Database ─────────────────────────────────────────────────────────────────

@contextmanager
def get_cursor():
    """Yield a RealDictCursor and close the connection when done."""
    conn = None
    try:
        conn = psycopg2.connect(os.environ["DATABASE_URL"])
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            yield cur
            conn.commit()
    finally:
        if conn is not None:
            conn.close()


# ── GET /companies ───────────────────────────────────────────────────────────

@app.get("/companies")
def list_companies():
    """All companies with latest composite_score and supply chain score,
    sorted by composite_score desc."""
    with get_cursor() as cur:
        cur.execute("""
            SELECT
                c.id,
                c.ticker,
                c.name,
                c.sector,
                c.tier,
                c.is_public,
                c.country,
                cs.composite_score,
                cs.quality_score,
                cs.momentum_score,
                cs.valuation_score,
                cs.supply_chain_score,
                cs.score_date AS composite_score_date,
                sc.composite_sc_score AS sc_score,
                sc.bottleneck_score,
                sc.concentration_risk,
                sc.pricing_power_score,
                sc.upstream_demand_score,
                sc.score_date AS sc_score_date
            FROM companies c
            LEFT JOIN LATERAL (
                SELECT * FROM composite_scores
                WHERE company_id = c.id
                ORDER BY score_date DESC LIMIT 1
            ) cs ON TRUE
            LEFT JOIN LATERAL (
                SELECT * FROM company_scores
                WHERE company_id = c.id
                ORDER BY score_date DESC LIMIT 1
            ) sc ON TRUE
            ORDER BY cs.composite_score DESC NULLS LAST, c.name
        """)
        return cur.fetchall()


# ── GET /companies/{id} ─────────────────────────────────────────────────────

@app.get("/companies/{company_id}")
def get_company(company_id: int):
    """Single company with all latest scores, recent signals, and
    supply chain neighbours (upstream + downstream)."""
    with get_cursor() as cur:
        # Company + scores
        cur.execute("""
            SELECT
                c.*,
                cs.composite_score,
                cs.quality_score,
                cs.momentum_score,
                cs.valuation_score,
                cs.supply_chain_score,
                cs.score_date AS composite_score_date,
                sc.composite_sc_score AS sc_score,
                sc.bottleneck_score,
                sc.concentration_risk,
                sc.pricing_power_score,
                sc.upstream_demand_score,
                sc.score_date AS sc_score_date
            FROM companies c
            LEFT JOIN LATERAL (
                SELECT * FROM composite_scores
                WHERE company_id = c.id
                ORDER BY score_date DESC LIMIT 1
            ) cs ON TRUE
            LEFT JOIN LATERAL (
                SELECT * FROM company_scores
                WHERE company_id = c.id
                ORDER BY score_date DESC LIMIT 1
            ) sc ON TRUE
            WHERE c.id = %s
        """, (company_id,))
        company = cur.fetchone()
        if not company:
            raise HTTPException(status_code=404, detail="Company not found")

        # Recent signals
        cur.execute("""
            SELECT id, signal_type, signal_value, direction,
                   estimated_lag, source_doc, signal_date
            FROM supply_chain_signals
            WHERE company_id = %s
            ORDER BY signal_date DESC
            LIMIT 20
        """, (company_id,))
        signals = cur.fetchall()

        # Upstream suppliers (companies that supply TO this company)
        cur.execute("""
            SELECT
                c.id, c.ticker, c.name, c.tier,
                e.relationship_type, e.substitution_ease, e.revenue_dependency
            FROM supply_chain_edges e
            JOIN companies c ON c.id = e.supplier_id
            WHERE e.customer_id = %s AND e.valid_to IS NULL
            ORDER BY e.substitution_ease ASC
        """, (company_id,))
        upstream = cur.fetchall()

        # Downstream customers (companies that this company supplies)
        cur.execute("""
            SELECT
                c.id, c.ticker, c.name, c.tier,
                e.relationship_type, e.substitution_ease, e.revenue_dependency
            FROM supply_chain_edges e
            JOIN companies c ON c.id = e.customer_id
            WHERE e.supplier_id = %s AND e.valid_to IS NULL
            ORDER BY e.revenue_dependency DESC NULLS LAST
        """, (company_id,))
        downstream = cur.fetchall()

        return {
            "company": company,
            "signals": signals,
            "upstream": upstream,
            "downstream": downstream,
        }


# ── GET /signals ─────────────────────────────────────────────────────────────

@app.get("/signals")
def list_signals():
    """Latest 50 supply chain signals, most recent first,
    with company ticker and name."""
    with get_cursor() as cur:
        cur.execute("""
            SELECT
                s.id,
                s.company_id,
                c.ticker,
                c.name AS company_name,
                s.signal_type,
                s.signal_value,
                s.direction,
                s.estimated_lag,
                s.source_doc,
                s.signal_date
            FROM supply_chain_signals s
            JOIN companies c ON c.id = s.company_id
            ORDER BY s.signal_date DESC, s.id DESC
            LIMIT 50
        """)
        return cur.fetchall()


# ── GET /watchlist ───────────────────────────────────────────────────────────

@app.get("/watchlist")
def watchlist():
    """Top 10 public companies by composite_score not currently in portfolio.
    (Portfolio table exists but may be empty — returns top 10 overall in that case.)
    """
    with get_cursor() as cur:
        cur.execute("""
            SELECT
                c.id,
                c.ticker,
                c.name,
                c.tier,
                cs.composite_score,
                cs.quality_score,
                cs.momentum_score,
                cs.valuation_score,
                cs.supply_chain_score,
                cs.score_date
            FROM companies c
            JOIN LATERAL (
                SELECT * FROM composite_scores
                WHERE company_id = c.id
                ORDER BY score_date DESC LIMIT 1
            ) cs ON TRUE
            WHERE c.is_public = TRUE
              AND c.ticker IS NOT NULL
              AND c.id NOT IN (
                  SELECT company_id FROM portfolio WHERE is_open = TRUE
              )
            ORDER BY cs.composite_score DESC
            LIMIT 10
        """)
        return cur.fetchall()


# ── GET /graph/{id} ──────────────────────────────────────────────────────────

@app.get("/graph/{company_id}")
def graph_neighbourhood(
    company_id: int,
    hops: int = Query(default=2, ge=1, le=3),
):
    """Supply chain graph neighbourhood for a company: nodes and edges
    within `hops` hops, formatted for Cytoscape.js.

    Returns:
        {
            nodes: [{id, label, tier, score}],
            edges: [{source, target, type, sub_ease}]
        }
    """
    with get_cursor() as cur:
        # Verify company exists
        cur.execute("SELECT id, ticker, name, tier FROM companies WHERE id = %s",
                    (company_id,))
        root = cur.fetchone()
        if not root:
            raise HTTPException(status_code=404, detail="Company not found")

        # Iteratively expand neighbourhood hop by hop
        visited_nodes = {company_id}
        frontier = {company_id}
        all_edge_rows = []

        for _ in range(hops):
            if not frontier:
                break
            cur.execute("""
                SELECT DISTINCT supplier_id, customer_id,
                       relationship_type, substitution_ease
                FROM supply_chain_edges
                WHERE valid_to IS NULL
                  AND (supplier_id = ANY(%(ids)s) OR customer_id = ANY(%(ids)s))
            """, {"ids": list(frontier)})
            hop_edges = cur.fetchall()
            all_edge_rows.extend(hop_edges)

            new_nodes = set()
            for e in hop_edges:
                new_nodes.add(e["supplier_id"])
                new_nodes.add(e["customer_id"])
            frontier = new_nodes - visited_nodes
            visited_nodes |= new_nodes

        # Guard: too many nodes
        if len(visited_nodes) > MAX_GRAPH_NODES:
            return {
                "nodes": [],
                "edges": [],
                "warning": (
                    f"Neighbourhood has {len(visited_nodes)} nodes "
                    f"(limit {MAX_GRAPH_NODES}). Try hops=1 for a smaller graph."
                ),
            }

        # Deduplicate edges
        seen_edges = set()
        unique_edges = []
        for e in all_edge_rows:
            key = (e["supplier_id"], e["customer_id"])
            if key not in seen_edges:
                seen_edges.add(key)
                unique_edges.append(e)

        if not visited_nodes:
            return {"nodes": [], "edges": []}

        # Fetch node info + latest SC score
        cur.execute("""
            SELECT
                c.id, c.ticker, c.name, c.tier,
                sc.composite_sc_score AS score
            FROM companies c
            LEFT JOIN LATERAL (
                SELECT composite_sc_score FROM company_scores
                WHERE company_id = c.id
                ORDER BY score_date DESC LIMIT 1
            ) sc ON TRUE
            WHERE c.id = ANY(%(ids)s)
        """, {"ids": list(visited_nodes)})
        nodes_raw = cur.fetchall()

        nodes = [
            {
                "id": n["id"],
                "label": n["ticker"] or n["name"],
                "tier": n["tier"],
                "score": float(n["score"]) if n["score"] is not None else None,
            }
            for n in nodes_raw
        ]

        edges = [
            {
                "source": e["supplier_id"],
                "target": e["customer_id"],
                "type": e["relationship_type"],
                "sub_ease": e["substitution_ease"],
            }
            for e in unique_edges
        ]

        return {"nodes": nodes, "edges": edges}


# ── POST /portfolio ──────────────────────────────────────────────────────────

VALID_ACTIONS = {"BUY", "SELL", "ADD", "TRIM"}


class TradeInput(BaseModel):
    company_id: int
    action: str
    shares: float
    cost_basis: float
    thesis: str
    key_risks: str
    exit_conditions: str

    @field_validator("action")
    @classmethod
    def action_must_be_valid(cls, v):
        upper = v.upper()
        if upper not in VALID_ACTIONS:
            raise ValueError(f"action must be one of {VALID_ACTIONS}")
        return upper


@app.post("/portfolio")
def create_trade(trade: TradeInput):
    """Log a trade into the portfolio table.
    Automatically looks up composite_score_at_entry and latest SC signal."""
    with get_cursor() as cur:
        # Verify company exists and get ticker
        cur.execute("SELECT id, ticker FROM companies WHERE id = %s",
                    (trade.company_id,))
        company = cur.fetchone()
        if not company:
            raise HTTPException(status_code=404, detail="Company not found")

        # Look up latest composite score
        cur.execute("""
            SELECT composite_score FROM composite_scores
            WHERE company_id = %s
            ORDER BY score_date DESC LIMIT 1
        """, (trade.company_id,))
        cs_row = cur.fetchone()
        composite_at_entry = cs_row["composite_score"] if cs_row else None

        # Look up latest SC signal
        cur.execute("""
            SELECT signal_type || ': ' || COALESCE(signal_value, '') AS summary
            FROM supply_chain_signals
            WHERE company_id = %s
            ORDER BY signal_date DESC LIMIT 1
        """, (trade.company_id,))
        sig_row = cur.fetchone()
        sc_signal_at_entry = sig_row["summary"] if sig_row else None

        cur.execute("""
            INSERT INTO portfolio
                (company_id, ticker, action, shares, cost_basis, entry_date,
                 thesis, key_risks, exit_conditions,
                 composite_score_at_entry, sc_signal_at_entry)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            trade.company_id,
            company["ticker"],
            trade.action,
            trade.shares,
            trade.cost_basis,
            date.today(),
            trade.thesis,
            trade.key_risks,
            trade.exit_conditions,
            composite_at_entry,
            sc_signal_at_entry,
        ))
        new_id = cur.fetchone()["id"]

        return {"id": new_id, "status": "created"}
