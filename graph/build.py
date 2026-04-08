"""
Build the supply chain graph from the database and provide traversal functions.

Source: GRAPH_DESIGN.md section 7.

Usage:
    from graph.build import build_graph, traverse_upstream, find_bottlenecks
"""

import os

import networkx as nx
import pandas as pd
import psycopg2
from dotenv import load_dotenv

load_dotenv()


def get_connection():
    """Return a psycopg2 connection using DATABASE_URL from .env."""
    return psycopg2.connect(os.environ["DATABASE_URL"])


def build_graph(conn) -> nx.DiGraph:
    """Load active edges from DB into a NetworkX DiGraph.

    Only includes edges where:
    - valid_to IS NULL (currently active)
    - confidence >= 0.6 (meets data quality threshold from GRAPH_DESIGN.md section 10)
    """
    edges = pd.read_sql(
        """
        SELECT supplier_id, customer_id, relationship_type,
               substitution_ease, lead_time_weeks, revenue_dependency
        FROM supply_chain_edges
        WHERE valid_to IS NULL AND confidence >= 0.6
        """,
        conn,
    )
    G = nx.DiGraph()

    # Add all companies as nodes with their attributes
    companies = pd.read_sql(
        "SELECT id, ticker, name, sector, tier, is_public FROM companies", conn
    )
    for _, row in companies.iterrows():
        G.add_node(row.id, **row.to_dict())

    # Add edges
    for _, row in edges.iterrows():
        G.add_edge(row.supplier_id, row.customer_id, **row.to_dict())

    return G


def traverse_upstream(G, company_id, max_depth=3):
    """Return all upstream suppliers within max_depth hops.

    Returns:
        dict: {node_id: path_length} for all ancestors within max_depth.
    """
    ancestors = nx.ancestors(G, company_id)
    return {
        n: nx.shortest_path_length(G, n, company_id)
        for n in ancestors
        if nx.shortest_path_length(G, n, company_id) <= max_depth
    }


def find_bottlenecks(G, threshold=6.0):
    """Return all nodes whose bottleneck_score exceeds threshold.

    Imports bottleneck_score from graph.metrics to avoid circular dependency
    at module level.
    """
    from graph.metrics import bottleneck_score

    scores = {n: bottleneck_score(n, G) for n in G.nodes()}
    return {n: s for n, s in scores.items() if s >= threshold}


def get_tier0_nodes(G, companies_df):
    """Return all end market (tier 0) node IDs."""
    return companies_df[companies_df.tier == 0].id.tolist()
