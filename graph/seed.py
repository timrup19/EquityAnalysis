"""
Seed the database with the 30 starting companies (ARCHITECTURE.md section 6)
and the SEED_EDGES (GRAPH_DESIGN.md section 8).

Usage:
    python -m graph.seed
"""

import os
from datetime import date

import psycopg2
from dotenv import load_dotenv

load_dotenv()

# ── 30 Starting Companies (ARCHITECTURE.md section 6) ───────────────────────
# (ticker, name, sector, tier, is_public, country)
SEED_COMPANIES = [
    # Tier 3 — Materials & components
    ("NUE",    "Nucor",                  "grid_energy", 3, True,  "US"),
    ("CLF",    "Cleveland-Cliffs",       "grid_energy", 3, True,  "US"),
    (None,     "Southwire",              "grid_energy", 3, False, "US"),
    ("DD",     "DuPont",                 "grid_energy", 3, True,  "US"),
    (None,     "Weidmann",               "grid_energy", 3, False, "CH"),

    # Tier 2 — Equipment manufacturers
    ("ETN",    "Eaton",                  "grid_energy", 2, True,  "US"),
    ("HUBB",   "Hubbell",               "grid_energy", 2, True,  "US"),
    ("AME",    "Ametek",                 "grid_energy", 2, True,  "US"),
    ("ST",     "Sensata Technologies",   "grid_energy", 2, True,  "US"),
    ("ROP",    "Roper Technologies",     "grid_energy", 2, True,  "US"),
    ("PRY.MI", "Prysmian",              "grid_energy", 2, True,  "IT"),
    ("NEX.PA", "Nexans",                "grid_energy", 2, True,  "FR"),
    ("NKT.CO", "NKT",                   "grid_energy", 2, True,  "DK"),
    ("S92.DE", "SMA Solar",             "grid_energy", 2, True,  "DE"),

    # Tier 1 — Grid infrastructure & contractors
    ("PWR",    "Quanta Services",        "grid_energy", 1, True,  "US"),
    ("MYRG",   "MYR Group",             "grid_energy", 1, True,  "US"),
    ("EME",    "EMCOR Group",           "grid_energy", 1, True,  "US"),
    ("IESC",   "IES Holdings",          "grid_energy", 1, True,  "US"),
    ("AGX",    "Argan",                  "grid_energy", 1, True,  "US"),

    # Tier 0 — End market (utilities & large buyers)
    ("NEE",    "NextEra Energy",         "grid_energy", 0, True,  "US"),
    ("DUK",    "Duke Energy",            "grid_energy", 0, True,  "US"),
    ("D",      "Dominion Energy",        "grid_energy", 0, True,  "US"),
    ("XEL",    "Xcel Energy",            "grid_energy", 0, True,  "US"),
    ("ETR",    "Entergy",                "grid_energy", 0, True,  "US"),
    ("AEP",    "American Electric Power", "grid_energy", 0, True, "US"),
    ("ED",     "Consolidated Edison",    "grid_energy", 0, True,  "US"),
    ("EVRG",   "Evergy",                "grid_energy", 0, True,  "US"),
    ("POR",    "Portland General Electric", "grid_energy", 0, True, "US"),
    ("AGR",    "Avangrid",              "grid_energy", 0, True,  "US"),
    # Note: Schneider Electric mentioned in ARCHITECTURE.md tier map but not
    # in the 30-company list. Not included per the explicit list.
]

# ── SEED_EDGES (GRAPH_DESIGN.md section 8) ──────────────────────────────────
# Uses short ticker keys for lookup. Private companies use: WEID for Weidmann,
# SWIRE for Southwire. PRY/NEX/NKT are shortened from exchange-suffixed tickers.
SEED_EDGES = [
    # Tier 3 → Tier 2
    {"supplier": "CLF",  "customer": "ETN",  "type": "preferred_supplier",  "sub_ease": 3, "rev_dep": 0.08},
    {"supplier": "CLF",  "customer": "HUBB", "type": "commodity_supplier",  "sub_ease": 4, "rev_dep": 0.05},
    {"supplier": "DD",   "customer": "ETN",  "type": "preferred_supplier",  "sub_ease": 2, "rev_dep": 0.12},
    {"supplier": "WEID", "customer": "ETN",  "type": "sole_source",         "sub_ease": 1, "rev_dep": 0.20},

    # Tier 2 → Tier 1
    {"supplier": "ETN",  "customer": "PWR",  "type": "capacity_dependency", "sub_ease": 2, "lead_time": 80},
    {"supplier": "ETN",  "customer": "MYRG", "type": "capacity_dependency", "sub_ease": 2, "lead_time": 80},
    {"supplier": "ETN",  "customer": "EME",  "type": "capacity_dependency", "sub_ease": 3, "lead_time": 80},
    {"supplier": "HUBB", "customer": "PWR",  "type": "preferred_supplier",  "sub_ease": 3, "rev_dep": 0.15},
    {"supplier": "HUBB", "customer": "IESC", "type": "preferred_supplier",  "sub_ease": 3, "rev_dep": 0.20},
    {"supplier": "PRY",  "customer": "PWR",  "type": "preferred_supplier",  "sub_ease": 2, "lead_time": 60},
    {"supplier": "NEX",  "customer": "EME",  "type": "preferred_supplier",  "sub_ease": 2, "lead_time": 60},

    # Tier 1 → Tier 0
    {"supplier": "PWR",  "customer": "NEE",  "type": "preferred_supplier",  "sub_ease": 3, "rev_dep": 0.18},
    {"supplier": "PWR",  "customer": "DUK",  "type": "preferred_supplier",  "sub_ease": 3, "rev_dep": 0.10},
    {"supplier": "PWR",  "customer": "AEP",  "type": "preferred_supplier",  "sub_ease": 3, "rev_dep": 0.08},
    {"supplier": "MYRG", "customer": "NEE",  "type": "preferred_supplier",  "sub_ease": 3, "rev_dep": 0.22},
    {"supplier": "MYRG", "customer": "XEL",  "type": "preferred_supplier",  "sub_ease": 3, "rev_dep": 0.14},
    {"supplier": "EME",  "customer": "DUK",  "type": "preferred_supplier",  "sub_ease": 3, "rev_dep": 0.09},
    {"supplier": "IESC", "customer": "NEE",  "type": "preferred_supplier",  "sub_ease": 4, "rev_dep": 0.16},
    {"supplier": "AGX",  "customer": "NEE",  "type": "preferred_supplier",  "sub_ease": 3, "rev_dep": 0.35},
]

# Map short keys used in SEED_EDGES to the actual ticker/name identifier.
# Private companies and non-US tickers need special handling.
_TICKER_ALIAS = {
    "CLF": "CLF", "DD": "DD", "WEID": None, "NUE": "NUE",
    "ETN": "ETN", "HUBB": "HUBB", "AME": "AME", "ST": "ST", "ROP": "ROP",
    "PRY": "PRY.MI", "NEX": "NEX.PA", "NKT": "NKT.CO", "SMA": "S92.DE",
    "PWR": "PWR", "MYRG": "MYRG", "EME": "EME", "IESC": "IESC", "AGX": "AGX",
    "NEE": "NEE", "DUK": "DUK", "D": "D", "XEL": "XEL", "ETR": "ETR",
    "AEP": "AEP", "ED": "ED", "EVRG": "EVRG", "POR": "POR", "AGR": "AGR",
}

# For private companies, map short key to company name
_NAME_ALIAS = {
    "WEID": "Weidmann",
    "SWIRE": "Southwire",
}


def get_connection():
    """Return a psycopg2 connection using DATABASE_URL from .env."""
    return psycopg2.connect(os.environ["DATABASE_URL"])


def _resolve_company_id(cur, short_key):
    """Resolve a short ticker key to a companies.id in the database."""
    ticker = _TICKER_ALIAS.get(short_key)
    if ticker:
        cur.execute("SELECT id FROM companies WHERE ticker = %s", (ticker,))
    else:
        name = _NAME_ALIAS.get(short_key)
        if not name:
            raise ValueError(f"Unknown company key: {short_key}")
        cur.execute("SELECT id FROM companies WHERE name = %s", (name,))
    row = cur.fetchone()
    if not row:
        raise ValueError(f"Company not found in DB for key: {short_key}")
    return row[0]


def seed_companies(cur):
    """Insert the 30 starting companies if they don't already exist."""
    inserted = 0
    for ticker, name, sector, tier, is_public, country in SEED_COMPANIES:
        # Skip if already exists (by name to handle private companies)
        cur.execute("SELECT id FROM companies WHERE name = %s", (name,))
        if cur.fetchone():
            continue
        cur.execute(
            """
            INSERT INTO companies (ticker, name, sector, tier, is_public, country)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (ticker, name, sector, tier, is_public, country),
        )
        inserted += 1
    return inserted


def seed_edges(cur):
    """Insert SEED_EDGES. Skips duplicates (same supplier_id + customer_id)."""
    inserted = 0
    today = date.today()
    for edge in SEED_EDGES:
        supplier_id = _resolve_company_id(cur, edge["supplier"])
        customer_id = _resolve_company_id(cur, edge["customer"])

        # Skip if this edge already exists and is active
        cur.execute(
            """
            SELECT id FROM supply_chain_edges
            WHERE supplier_id = %s AND customer_id = %s AND valid_to IS NULL
            """,
            (supplier_id, customer_id),
        )
        if cur.fetchone():
            continue

        cur.execute(
            """
            INSERT INTO supply_chain_edges
                (supplier_id, customer_id, relationship_type, substitution_ease,
                 revenue_dependency, lead_time_weeks, confidence,
                 source_doc, extracted_by, valid_from)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                supplier_id,
                customer_id,
                edge["type"],
                edge["sub_ease"],
                edge.get("rev_dep"),
                edge.get("lead_time"),
                1.0,  # manual seed edges get full confidence
                "manual_seed",
                "manual",
                today,
            ),
        )
        inserted += 1
    return inserted


def main():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            n_companies = seed_companies(cur)
            print(f"Inserted {n_companies} companies.")

            n_edges = seed_edges(cur)
            print(f"Inserted {n_edges} edges.")

        conn.commit()
        print("Seed complete.")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
