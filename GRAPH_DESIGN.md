# Supply Chain Graph — Design Specification

> **Purpose:** This document is the authoritative spec for the supply chain graph layer. It covers data model, schema, scoring formulas, signal propagation logic, and implementation notes. Read alongside `ARCHITECTURE.md`. Build graph/ directory components against this spec.

---

## 1. Design Principles

Every decision in this spec follows one rule: **pick the option that stays maintainable over the option that is theoretically richer.** A simple graph that is kept updated beats a sophisticated one that goes stale.

| Decision | Choice | Why |
|----------|--------|-----|
| Node unit | Companies, not products | Products change constantly; companies are the investable unit and file quarterly |
| Edge attributes | 4 core attributes only | More attributes aren't reliably extractable from public documents |
| Substitution ease | Manual input, AI-flagged | Too consequential to automate; wrong scores corrupt bottleneck signal |
| Bottleneck formula | Simple ratio | Interpretable when auditing a signal; complexity adds noise before signal is proven |
| End market nodes | Included as traversal anchors | Needed to propagate demand events downward through the graph |
| Score refresh | Weekly | Matches cadence of meaningful new information; keeps compute costs manageable |
| Sector scope | One sector at a time | Cross-sector graph loses interpretability fast; get schema right first |

---

## 2. Node Types

All nodes represent companies. Node type is determined by `tier` field.

```
tier = 3   →   Raw materials & inputs
tier = 2   →   Equipment & component manufacturers
tier = 1   →   System integrators & contractors
tier = 0   →   End market buyers (utilities, industrials, data centers)
```

### Node schema

```sql
CREATE TABLE companies (
  id                  SERIAL PRIMARY KEY,
  ticker              VARCHAR(10),              -- NULL for private companies
  name                VARCHAR(255) NOT NULL,
  sector              VARCHAR(100) NOT NULL,     -- e.g. 'grid_energy'
  tier                INTEGER NOT NULL,          -- 0, 1, 2, or 3
  is_public           BOOLEAN NOT NULL,
  country             VARCHAR(50) DEFAULT 'US',
  is_bottleneck       BOOLEAN DEFAULT FALSE,     -- manually flagged, updated by scoring
  lead_time_weeks     INTEGER,                   -- tier 2 only: current equipment lead time
  backlog_usd         BIGINT,                    -- tier 1 only: reported order backlog
  capex_guided_usd    BIGINT,                    -- tier 0 only: guided annual capex
  notes               TEXT,                      -- analyst notes, sourcing quirks
  created_at          TIMESTAMP DEFAULT NOW(),
  updated_at          TIMESTAMP DEFAULT NOW()
);
```

### Node attributes by tier

**Tier 3 — raw materials** (Cleveland-Cliffs, Southwire, DuPont, Weidmann)
- Track: order volumes, input cost trends, capacity utilization
- Key signal: procurement contract changes, factory permit filings
- `lead_time_weeks` not applicable — commodity inputs

**Tier 2 — equipment manufacturers** (Eaton, Hubbell, Prysmian, Nexans, NKT, SMA Solar)
- Track: order backlog, lead times, pricing announcements
- Key signal: lead time extensions = demand surge before it appears in revenue
- `lead_time_weeks` is the primary real-time signal field — update monthly

**Tier 1 — contractors** (Quanta Services, MYR Group, EMCOR, IES Holdings, Argan)
- Track: contract awards, backlog growth, headcount
- Key signal: contract award announcements from USASpending.gov and IR pages
- `backlog_usd` should be updated each quarter from earnings reports

**Tier 0 — end market** (NextEra, Duke, Dominion, Xcel, AEP, data center operators)
- These are traversal anchors — NOT investment candidates in the initial build
- Track: capex guidance, IRP filings, RFP announcements
- `capex_guided_usd` drives the demand propagation signal

---

## 3. Edge Types

Edges represent supply relationships between companies. Direction always flows **supplier → customer** (upstream to downstream).

### Edge schema

```sql
CREATE TABLE supply_chain_edges (
  id                  SERIAL PRIMARY KEY,
  supplier_id         INTEGER NOT NULL REFERENCES companies(id),
  customer_id         INTEGER NOT NULL REFERENCES companies(id),
  relationship_type   VARCHAR(50) NOT NULL,      -- see types below
  revenue_dependency  FLOAT,                     -- % of supplier revenue from this customer
  substitution_ease   INTEGER,                   -- 1 (impossible) to 5 (trivial) — SET MANUALLY
  lead_time_weeks     INTEGER,                   -- weeks from order to delivery for this edge
  confidence          FLOAT NOT NULL DEFAULT 0.5, -- 0.0 to 1.0, set by AI extraction
  source_doc          VARCHAR(512),              -- filing or transcript that revealed this edge
  source_date         DATE,                      -- date of source document
  extracted_by        VARCHAR(50) DEFAULT 'manual', -- 'manual' or 'ai_agent'
  valid_from          DATE,
  valid_to            DATE,                      -- NULL = currently active
  created_at          TIMESTAMP DEFAULT NOW(),
  updated_at          TIMESTAMP DEFAULT NOW()
);
```

### The four relationship types

**`sole_source`**
The supplier is the only qualified source for this component or service. Highest pricing power. Look for language in filings: "sole qualified supplier", "only manufacturer certified to", "no alternative source identified".
- `substitution_ease` = 1 always
- `revenue_dependency` often high (>30%)
- Contributes most to bottleneck score

**`preferred_supplier`**
Customer has multiple qualified suppliers but this one holds primary or majority share. Moderate pricing power.
- `substitution_ease` = 2–3
- Look for: "primary supplier", "long-term supply agreement", named in customer 10-K

**`commodity_supplier`**
Fungible input — customer could switch with moderate friction. Low pricing power on its own, but concentration still matters if a customer is large enough.
- `substitution_ease` = 4–5
- Relevant mainly for tier 3 nodes where volume signal matters more than relationship type

**`capacity_dependency`**
A contractor depends on an equipment manufacturer's delivery schedule to execute its projects. Not a purchasing relationship — a scheduling one. Lead time extensions at tier 2 directly constrain tier 1 revenue.
- `substitution_ease` = 2–4 depending on lead time and qualifications
- Most important edge type for generating forward revenue signals

### Substitution ease score — manual rating guide

Set this field manually. AI can flag supporting evidence but should not set the value.

| Score | Meaning | Example |
|-------|---------|---------|
| 1 | No substitute exists or is qualified | ASML EUV lithography — but in grid context: Weidmann transformer insulation for certain voltage classes |
| 2 | Substitution takes 12+ months and significant cost | Qualifying a new transformer supplier for utility-grade equipment |
| 3 | Substitution takes 3–12 months | Switching HV cable supplier requires testing and approval |
| 4 | Substitution takes weeks to months | Switching steel grade supplier with existing approved vendors |
| 5 | Trivial substitution | Commodity copper rod — spot market available |

---

## 4. Signal Table

Signals are derived events written to the database when the graph detects a meaningful change.

```sql
CREATE TABLE supply_chain_signals (
  id              SERIAL PRIMARY KEY,
  company_id      INTEGER NOT NULL REFERENCES companies(id),
  signal_type     VARCHAR(100) NOT NULL,    -- see types below
  signal_value    TEXT,                     -- quantified value where possible
  direction       VARCHAR(10),             -- 'positive', 'negative', 'neutral'
  estimated_lag   INTEGER,                 -- quarters until signal appears in earnings
  source_doc      VARCHAR(512),
  signal_date     DATE NOT NULL,
  created_at      TIMESTAMP DEFAULT NOW()
);
```

### Signal types

| Signal type | Source | Direction | Estimated lag |
|-------------|--------|-----------|---------------|
| `capex_increase` | Tier 0 earnings call | positive | 2–4 quarters |
| `lead_time_extension` | Tier 2 IR / channel checks | positive for supplier | 1–2 quarters |
| `lead_time_compression` | Tier 2 IR | negative | 1–2 quarters |
| `sole_source_confirmed` | Filing extraction | positive | immediate |
| `customer_concentration_increase` | Filing extraction | risk flag | immediate |
| `backlog_growth` | Tier 1 earnings | positive | 1–2 quarters |
| `procurement_contract_award` | USASpending.gov | positive | 2–3 quarters |
| `capacity_expansion_announced` | Press release | positive (long-term) | 4–8 quarters |
| `new_competitor_qualified` | Filing / news | negative | 2–4 quarters |

---

## 5. Derived Scores

These are computed weekly by `graph/metrics.py` and stored in a scores table.

```sql
CREATE TABLE company_scores (
  id                    SERIAL PRIMARY KEY,
  company_id            INTEGER NOT NULL REFERENCES companies(id),
  score_date            DATE NOT NULL,
  bottleneck_score      FLOAT,    -- 0 to 10
  concentration_risk    FLOAT,    -- 0 to 10 (higher = more risk)
  pricing_power_score   FLOAT,    -- 0 to 10
  upstream_demand_score FLOAT,    -- 0 to 10
  composite_sc_score    FLOAT,    -- 0 to 10, weighted average
  UNIQUE(company_id, score_date)
);
```

### Bottleneck score formula

Measures how critical a supplier is to the downstream graph.

```python
def bottleneck_score(company_id, G):
    """
    G is a NetworkX DiGraph.
    Score = (downstream_dependent_companies / max_possible) 
            * (1 / avg_substitution_ease)
    Normalized to 0–10.
    """
    downstream = nx.descendants(G, company_id)
    dependent_count = len([
        n for n in downstream
        if G[company_id][n].get('substitution_ease', 3) <= 2
    ])
    edges = G.out_edges(company_id, data=True)
    avg_sub_ease = mean([e[2].get('substitution_ease', 3) for e in edges]) or 3
    raw = dependent_count / avg_sub_ease
    return min(raw * 2, 10)  # normalize
```

### Concentration risk formula

Measures how exposed a supplier is to losing a key customer — risk flag, not opportunity signal.

```python
def concentration_risk(company_id, edges_df):
    """
    Higher score = more risk.
    Uses revenue_dependency field from edges where supplier = company_id.
    """
    deps = edges_df[edges_df.supplier_id == company_id]['revenue_dependency'].dropna()
    if deps.empty:
        return 5.0  # unknown = medium risk
    top3 = deps.nlargest(3).sum()           # % revenue from top 3 customers
    sole_count = len(edges_df[
        (edges_df.supplier_id == company_id) &
        (edges_df.relationship_type == 'sole_source')
    ])
    # High concentration in sole_source customers is a double risk
    return min((top3 / 10) + (sole_count * 0.5), 10)
```

### Pricing power score formula

Combines relationship type distribution with margin trend.

```python
def pricing_power_score(company_id, edges_df, financials_df):
    """
    sole_source edges get weight 3, preferred weight 2, commodity weight 0.5
    Multiplied by gross margin trend slope (positive slope = growing pricing power)
    """
    weights = {'sole_source': 3, 'preferred_supplier': 2,
               'capacity_dependency': 1.5, 'commodity_supplier': 0.5}
    edges = edges_df[edges_df.supplier_id == company_id]
    rel_score = sum(weights.get(r, 1) for r in edges.relationship_type) / max(len(edges), 1)

    margins = financials_df[financials_df.company_id == company_id]['gross_margin'].tail(8)
    slope = linregress(range(len(margins)), margins).slope if len(margins) >= 4 else 0

    raw = rel_score * (1 + slope * 10)
    return min(max(raw, 0), 10)
```

### Upstream demand score formula

Detects capex increases at tier 0 and propagates signal upstream through the graph.

```python
def upstream_demand_score(company_id, G, signals_df):
    """
    Look at all tier 0 nodes downstream of company_id.
    Sum their recent capex_increase signal values, weighted by path length.
    Shorter path = stronger signal.
    """
    score = 0
    for tier0_id in get_tier0_nodes(G):
        if nx.has_path(G, company_id, tier0_id):
            path_length = nx.shortest_path_length(G, company_id, tier0_id)
            recent_signals = signals_df[
                (signals_df.company_id == tier0_id) &
                (signals_df.signal_type == 'capex_increase') &
                (signals_df.signal_date >= today() - timedelta(days=180))
            ]
            for _, sig in recent_signals.iterrows():
                score += float(sig.signal_value or 1) / (path_length ** 1.5)
    return min(score, 10)
```

### Composite supply chain score

```python
def composite_sc_score(bottleneck, concentration_risk, pricing_power, upstream_demand):
    """
    Weights deliberately favour opportunity signals over risk flags.
    Concentration risk is subtracted — higher risk lowers the score.
    """
    return (
        bottleneck        * 0.35 +
        pricing_power     * 0.30 +
        upstream_demand   * 0.25 -
        concentration_risk * 0.10   # risk penalty
    )
```

---

## 6. Signal Propagation — How It Works

When a tier 0 event is detected (e.g. NextEra announces $7B capex increase):

```
1. AI agent extracts signal → writes to supply_chain_signals table
       company_id = NextEra, signal_type = 'capex_increase', signal_value = '7000000000'

2. graph/signals.py runs traverse_upstream(nextEra_id)
       → finds all companies with a directed path TO NextEra in the graph
       → returns list sorted by (path_length, bottleneck_score)

3. For each upstream company:
       → recompute upstream_demand_score
       → recompute composite_sc_score
       → if composite_sc_score crosses threshold (>= 7.0), write alert

4. Dashboard picks up alert:
       → Eaton (ETN): bottleneck 8.4, composite 7.8 → ADD TO WATCHLIST
       → Hubbell (HUBB): bottleneck 7.1, composite 7.2 → ADD TO WATCHLIST
       → Ametek (AME): bottleneck 4.2, composite 5.1 → no action
```

The market reprices Quanta and MYR immediately (obvious direct contractors). Eaton and Hubbell are slower because the connection is less visible. Tier 3 nodes (Cleveland-Cliffs, Southwire) fire earliest but are often private or have less clean signal — use as confirmation, not primary signal.

---

## 7. Graph Traversal Functions

Core functions to implement in `graph/build.py` and `graph/metrics.py`.

```python
# graph/build.py

def build_graph(conn) -> nx.DiGraph:
    """Load active edges from DB into a NetworkX DiGraph."""
    edges = pd.read_sql("""
        SELECT supplier_id, customer_id, relationship_type,
               substitution_ease, lead_time_weeks, revenue_dependency
        FROM supply_chain_edges
        WHERE valid_to IS NULL AND confidence >= 0.6
    """, conn)
    G = nx.DiGraph()
    for _, row in edges.iterrows():
        G.add_edge(row.supplier_id, row.customer_id, **row.to_dict())
    return G

def traverse_upstream(G, company_id, max_depth=3):
    """Return all upstream suppliers within max_depth hops."""
    ancestors = nx.ancestors(G, company_id)
    return {n: nx.shortest_path_length(G, n, company_id)
            for n in ancestors
            if nx.shortest_path_length(G, n, company_id) <= max_depth}

def find_bottlenecks(G, threshold=6.0):
    """Return all nodes whose bottleneck_score exceeds threshold."""
    scores = {n: bottleneck_score(n, G) for n in G.nodes()}
    return {n: s for n, s in scores.items() if s >= threshold}

def get_tier0_nodes(G, companies_df):
    """Return all end market (tier 0) node IDs."""
    return companies_df[companies_df.tier == 0].id.tolist()
```

---

## 8. Graph Seeding — Grid & Energy Starting State

Seed these edges manually before running the AI extraction pipeline. These are high-confidence relationships based on public knowledge. All `substitution_ease` values set manually.

```python
SEED_EDGES = [
    # Tier 3 → Tier 2
    {"supplier": "CLF",  "customer": "ETN",  "type": "preferred_supplier",  "sub_ease": 3, "rev_dep": 0.08},
    {"supplier": "CLF",  "customer": "HUBB", "type": "commodity_supplier",   "sub_ease": 4, "rev_dep": 0.05},
    {"supplier": "DD",   "customer": "ETN",  "type": "preferred_supplier",   "sub_ease": 2, "rev_dep": 0.12},
    # Weidmann (private) → transformer manufacturers
    {"supplier": "WEID", "customer": "ETN",  "type": "sole_source",          "sub_ease": 1, "rev_dep": 0.20},

    # Tier 2 → Tier 1
    {"supplier": "ETN",  "customer": "PWR",  "type": "capacity_dependency",  "sub_ease": 2, "lead_time": 80},
    {"supplier": "ETN",  "customer": "MYRG", "type": "capacity_dependency",  "sub_ease": 2, "lead_time": 80},
    {"supplier": "ETN",  "customer": "EME",  "type": "capacity_dependency",  "sub_ease": 3, "lead_time": 80},
    {"supplier": "HUBB", "customer": "PWR",  "type": "preferred_supplier",   "sub_ease": 3, "rev_dep": 0.15},
    {"supplier": "HUBB", "customer": "IESC", "type": "preferred_supplier",   "sub_ease": 3, "rev_dep": 0.20},
    # HV cable: Prysmian, Nexans, NKT → contractors
    {"supplier": "PRY",  "customer": "PWR",  "type": "preferred_supplier",   "sub_ease": 2, "lead_time": 60},
    {"supplier": "NEX",  "customer": "EME",  "type": "preferred_supplier",   "sub_ease": 2, "lead_time": 60},

    # Tier 1 → Tier 0
    {"supplier": "PWR",  "customer": "NEE",  "type": "preferred_supplier",   "sub_ease": 3, "rev_dep": 0.18},
    {"supplier": "PWR",  "customer": "DUK",  "type": "preferred_supplier",   "sub_ease": 3, "rev_dep": 0.10},
    {"supplier": "PWR",  "customer": "AEP",  "type": "preferred_supplier",   "sub_ease": 3, "rev_dep": 0.08},
    {"supplier": "MYRG", "customer": "NEE",  "type": "preferred_supplier",   "sub_ease": 3, "rev_dep": 0.22},
    {"supplier": "MYRG", "customer": "XEL",  "type": "preferred_supplier",   "sub_ease": 3, "rev_dep": 0.14},
    {"supplier": "EME",  "customer": "DUK",  "type": "preferred_supplier",   "sub_ease": 3, "rev_dep": 0.09},
    {"supplier": "IESC", "customer": "NEE",  "type": "preferred_supplier",   "sub_ease": 4, "rev_dep": 0.16},
    {"supplier": "AGX",  "customer": "NEE",  "type": "preferred_supplier",   "sub_ease": 3, "rev_dep": 0.35},
]
```

---

## 9. AI Extraction Prompt — Supply Chain Relationships

Store as `ai_agent/prompts/supply_chain_extraction.txt`. Used by the AI agent runner to parse filings and extract new edges.

```
You are a supply chain analyst reviewing a public company filing or earnings call transcript.

Extract all supplier-customer relationships mentioned in the text.

For each relationship found, return a JSON object with these fields:
- supplier_name: string (company name as mentioned in the text)
- customer_name: string
- relationship_type: one of ["sole_source", "preferred_supplier", "commodity_supplier", "capacity_dependency"]
- revenue_dependency_pct: float or null (% of supplier revenue from this customer, if stated)
- lead_time_weeks: integer or null (if mentioned)
- evidence_quote: string (exact quote from the document, max 100 words)
- confidence: float between 0.0 and 1.0

Rules:
- Only include relationships explicitly stated or strongly implied in the text.
- Do not infer relationships that are not mentioned.
- If a relationship type is ambiguous, choose the more conservative option (prefer "preferred_supplier" over "sole_source" unless explicitly stated).
- Set confidence below 0.7 if the relationship is implied rather than stated.
- Return a JSON array. If no relationships are found, return an empty array [].
- Return JSON only. No preamble, no explanation, no markdown.
```

---

## 10. Data Quality Rules

Enforced in the ingestion pipeline before any edge is written to the database.

- Minimum confidence of **0.6** for an AI-extracted edge to be written
- All edges with `substitution_ease` = null are treated as 3 (moderate) in scoring — never assume best or worst case
- `revenue_dependency` values above 0.60 should be flagged for manual review — likely an error or a very unusual relationship
- Edges are never deleted — set `valid_to = today()` when a relationship ends, preserving history
- `sole_source` edges with confidence < 0.8 are downgraded to `preferred_supplier` automatically
- Manual entries always override AI entries for the same supplier-customer pair

---

## 11. Files This Spec Governs

```
graph/
├── build.py        — load graph from DB, traversal functions
├── metrics.py      — all four scoring formulas + composite
├── signals.py      — signal detection, propagation, alert writing
└── seed.py         — seed SEED_EDGES into DB on first run

ai_agent/
├── prompts/
│   └── supply_chain_extraction.txt   — extraction prompt above

db/
└── schema.sql      — must include all tables defined in sections 2–5
```

---

*Last updated: April 2026 — supply chain graph design for grid & energy sector.*