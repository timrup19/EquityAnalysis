# Portfolio Intelligence System — Architecture Brief

> **Purpose:** This document is a handoff brief for Claude Code. It describes the full system architecture for a tech-lead equity portfolio manager with a proprietary supply chain intelligence layer, starting with the grid & energy infrastructure sector. Build in the order specified in the Phased Build Plan.

---

## 1. Vision & Edge Thesis

This is not a generic AI stock screener. The system's core edge is **supply chain graph intelligence** — maintaining a proprietary, continuously updated dependency map of companies across sectors, with a starting focus on grid & energy infrastructure. Most equity funds analyze companies in isolation. This system analyzes *relationships between companies*, surfacing pricing power, bottleneck positions, and demand signals that appear 2–3 quarters before they reach consensus estimates.

**Target performance:** 15–20% annualized returns over a full market cycle.  
**Investment style:** Concentrated, long-biased, fundamental quality with supply chain signal overlay.  
**Starting sector:** Grid & energy infrastructure (transformers, high-voltage cable, grid inverters, switchgear).

---

## 2. System Overview

The system has five layers. Each layer feeds the next. The supply chain graph is a cross-cutting module that enhances layers 1, 2, 3, and 5.

```
┌─────────────────────────────────────────────────────┐
│  Layer 1: Data Ingestion                            │
│  Market data + filings + news + supply chain data   │
└────────────────────┬────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────┐
│  Layer 2: AI Research Engine                        │
│  Claude API — fundamental analysis + graph extraction│
└────────────────────┬────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────┐
│  Layer 3: Supply Chain Graph  ← PROPRIETARY MOAT    │
│  Dependency map · bottleneck scores · lead signals  │
└────────────────────┬────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────┐
│  Layer 4: Scoring & Portfolio Construction          │
│  Quality · momentum · supply chain position score   │
└────────────────────┬────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────┐
│  Layer 5: Dashboard & Decision Log                  │
│  Positions · PnL · upstream alerts · thesis tracker │
└─────────────────────────────────────────────────────┘
```

---

## 3. Layer Specifications

### Layer 1 — Data Ingestion

**Purpose:** Pull, normalize, and store all raw data the system needs.

**Data sources:**

| Source | Data type | Update frequency | API / method |
|--------|-----------|-----------------|--------------|
| Polygon.io or yfinance | Price, volume, market cap | Daily | REST API |
| SEC EDGAR | 10-K, 10-Q, 8-K filings | On filing | EDGAR full-text search API |
| Earnings call transcripts | Management language, guidance | Quarterly | Motley Fool / Seeking Alpha scrape or Refinitiv |
| NewsAPI or GDELT | News headlines, sentiment | Daily | REST API |
| USASpending.gov | Government procurement contracts | Weekly | REST API (free, public) |
| EIA (Energy Information Administration) | Grid capex, transformer orders, energy demand | Monthly | REST API (free, public) |
| Company investor relations pages | Procurement announcements, capacity expansions | On event | Monitored RSS / scrape |

**Storage:**
- Raw data: PostgreSQL (structured) + S3-compatible object store (filings, transcripts)
- Schema: `companies`, `prices`, `filings`, `transcripts`, `news`, `supply_chain_edges`, `procurement_contracts`

---

### Layer 2 — AI Research Engine

**Purpose:** Use Claude API to extract structured intelligence from unstructured documents.

**Tasks the AI agent performs:**

1. **Fundamental analysis** — Given a 10-K or 10-Q, extract: revenue breakdown by segment, gross margin trend, FCF, ROIC, capex guidance, and any mentions of supply chain constraints or customer concentration.

2. **Earnings call parsing** — Extract: forward guidance language (specific vs. vague), metrics management stopped mentioning vs. prior quarters, tone shifts, and explicit supply chain commentary.

3. **Supply chain graph extraction** — Given any filing or press release, extract supplier/customer relationships in structured form: `{company_a} --[relationship_type]--> {company_b}` with supporting evidence and document source.

4. **Moat scoring inputs** — Extract qualitative signals: switching costs, sole-source language, long-term contract mentions, pricing power language.

**Implementation:**
- Python service using `anthropic` SDK
- Prompt templates stored as versioned `.txt` files (one per task type)
- Outputs written to PostgreSQL as structured JSON
- All AI outputs include source citation and confidence flag

**Model:** `claude-sonnet-4-6` for all research tasks (balance of quality and cost at scale).

---

### Layer 3 — Supply Chain Graph (Proprietary Moat)

**Purpose:** Maintain a live, queryable dependency graph of companies in the grid & energy sector, expanding to other sectors over time.

**Starting sector — Grid & Energy Infrastructure:**

Map the following tiers:

```
Tier 3 (raw inputs):
  - Specialty steel & copper producers (Nucor, Southwire)
  - Insulation & dielectric materials (Weidmann, DuPont)
  - Silicon steel for transformer cores (AK Steel / Cleveland-Cliffs)

Tier 2 (components):
  - Power transformer manufacturers (ABB, Eaton, SPX Transformer)
  - High-voltage cable manufacturers (Prysmian, Nexans, NKT)
  - Grid inverter / power electronics (SMA Solar, Ametek, Sensata)
  - Switchgear & protection (Schneider Electric, Eaton, Hubbell)

Tier 1 (system integrators / end suppliers):
  - Grid infrastructure contractors (Quanta Services, MYR Group, MYR)
  - Substation builders (Burns & McDonnell — private, Fluor, Bechtel)
  - Utility-scale solar/wind EPC contractors

End market:
  - Investor-owned utilities (NextEra, Duke, Dominion, Xcel)
  - Municipal utilities
  - Data center operators (direct procurement — Microsoft, Amazon, Google)
  - Industrial customers (large manufacturers, EV charging networks)
```

**Graph data model:**

```sql
-- Companies (nodes)
CREATE TABLE companies (
  id           SERIAL PRIMARY KEY,
  ticker       VARCHAR(10),
  name         VARCHAR(255),
  sector       VARCHAR(100),
  tier         INTEGER,           -- 1, 2, 3, or 0 for end market
  is_public    BOOLEAN,
  created_at   TIMESTAMP
);

-- Supply chain relationships (edges)
CREATE TABLE supply_chain_edges (
  id                 SERIAL PRIMARY KEY,
  supplier_id        INTEGER REFERENCES companies(id),
  customer_id        INTEGER REFERENCES companies(id),
  relationship_type  VARCHAR(50),   -- 'sole_source', 'preferred', 'commodity', 'unknown'
  revenue_dependency FLOAT,         -- % of supplier revenue from this customer (if known)
  source_doc         VARCHAR(255),  -- filing or transcript that revealed this edge
  confidence         FLOAT,         -- 0.0 to 1.0, set by AI extraction
  extracted_at       TIMESTAMP,
  valid_from         DATE,
  valid_to           DATE
);

-- Signals derived from the graph
CREATE TABLE supply_chain_signals (
  id             SERIAL PRIMARY KEY,
  company_id     INTEGER REFERENCES companies(id),
  signal_type    VARCHAR(100),  -- 'lead_time_increase', 'capex_announcement', 'sole_source_risk', 'capacity_expansion'
  signal_value   TEXT,
  signal_date    DATE,
  source_doc     VARCHAR(255),
  created_at     TIMESTAMP
);
```

**Key derived metrics (computed weekly):**

- **Bottleneck score** — how many downstream companies depend on this supplier with no clear substitute
- **Concentration risk score** — % of a supplier's revenue from its top 3 customers
- **Pricing power score** — combination of sole-source relationships + long-term contract language + margin trend
- **Upstream demand signal** — change in procurement activity at tier 2/3 that predicts tier 1 revenue 2–3 quarters forward

---

### Layer 4 — Scoring & Portfolio Construction

**Purpose:** Combine fundamental scores with supply chain position score to rank investment candidates and size positions.

**Composite score (0–100):**

| Dimension | Weight | Components |
|-----------|--------|------------|
| Quality | 30% | FCF yield, ROIC vs. WACC, gross margin trend, balance sheet |
| Momentum | 20% | Earnings revision direction, price vs. 52-week high, analyst estimate trend |
| Valuation | 20% | EV/FCF vs. sector median, PEG ratio |
| Supply chain position | 30% | Bottleneck score, pricing power score, upstream demand signal |

**Portfolio construction rules:**
- Maximum 25 positions
- Maximum 8% in any single position at cost
- Maximum 40% in any single sector
- No position initiated below composite score of 65/100
- Rebalance trigger: composite score drops below 50, or supply chain signal flags structural change

**Position sizing:** Kelly-fraction sizing capped at 8%, scaled by conviction (composite score) and liquidity (average daily volume).

---

### Layer 5 — Dashboard & Decision Log

**Purpose:** Give the portfolio manager a daily view of the portfolio and force structured decision-making.

**Dashboard views:**

1. **Portfolio summary** — current positions, weights, PnL by position, total portfolio vs. benchmark (S&P 500)
2. **Signal feed** — new supply chain signals extracted in the last 7 days, ranked by estimated impact
3. **Watchlist** — top 10 candidates by composite score not yet in portfolio
4. **Graph explorer** — interactive visualization of the supply chain graph for any company in the watchlist or portfolio

**Decision log (required for every trade):**
```
Date:
Company:
Action: BUY / SELL / ADD / TRIM
Composite score at entry:
Supply chain signal that triggered review (if any):
Investment thesis (3–5 sentences):
Key risks:
Exit conditions:
```

**Post-trade review (quarterly):**
- Was the thesis correct?
- Which score component was most predictive?
- What did the supply chain graph tell us that the market missed?

---

## 4. Tech Stack

| Component | Technology | Rationale |
|-----------|-----------|-----------|
| Backend API | Python (FastAPI) | Fast iteration, strong data science ecosystem |
| Database | PostgreSQL | Relational model fits graph edges + time-series prices |
| Graph queries | NetworkX (Python) | Lightweight graph analysis without needing a full graph DB |
| AI research agent | Anthropic SDK (`claude-sonnet-4-6`) | Best-in-class document understanding |
| Data pipeline | Apache Airflow or simple cron + Python | Scheduled ingestion and scoring runs |
| Frontend dashboard | React + Recharts | Interactive portfolio and graph views |
| Graph visualization | D3.js or Cytoscape.js | Supply chain graph explorer |
| Hosting (prototype) | Single VPS (Railway, Render, or Fly.io) | Low cost for prototype stage |
| Secrets management | `.env` + python-dotenv (prototype) → AWS Secrets Manager (production) | |

---

## 5. Phased Build Plan

Build in this order. Do not skip phases.

### Phase 1 — Data foundation (weeks 1–2)
- [ ] Set up PostgreSQL schema (all tables from Layer 3 spec above)
- [ ] Build ingestion scripts: Polygon.io prices, EDGAR filings, EIA grid data
- [ ] Build EDGAR filing downloader — pull all 10-Ks and 10-Qs for the 30 target companies in the grid & energy sector
- [ ] Store raw filings in `/data/filings/` with metadata in DB

### Phase 2 — AI extraction pipeline (weeks 3–4)
- [ ] Build prompt templates for: fundamental extraction, earnings call parsing, supply chain relationship extraction
- [ ] Build AI agent runner: iterate over filings, call Claude API, parse structured output, write to DB
- [ ] Manually review first 20 extractions for accuracy — tune prompts as needed
- [ ] Build `supply_chain_edges` population script

### Phase 3 — Graph layer (weeks 5–6)
- [ ] Seed the grid & energy supply chain graph manually (use the tier map in Layer 3 spec)
- [ ] Build graph analysis functions: bottleneck score, concentration risk, pricing power score
- [ ] Build upstream demand signal detector — weekly diff on procurement/capex announcements
- [ ] Unit test all scoring functions

### Phase 4 — Scoring model (week 7)
- [ ] Build composite score calculator (weights in Layer 4 spec)
- [ ] Run scoring on all 30 target companies
- [ ] Paper trade: pick top 10 by composite score, log in decision log template

### Phase 5 — Dashboard (weeks 8–10)
- [ ] FastAPI backend: endpoints for portfolio, signals, watchlist, graph data
- [ ] React frontend: portfolio summary, signal feed, watchlist table
- [ ] Graph explorer: Cytoscape.js visualization of supply chain graph
- [ ] Decision log UI: form to log trades, table to review history

### Phase 6 — Automation & monitoring (ongoing)
- [ ] Airflow DAGs for daily price ingestion, weekly scoring refresh, monthly filing scan
- [ ] Alert system: email/Slack notification when a new supply chain signal fires
- [ ] Performance tracking: log paper trade returns vs. S&P 500 weekly

---

## 6. Starting Universe — Grid & Energy (30 companies)

Seed the system with this initial universe. Expand as the graph is built out.

**Tier 3 — Materials & components:**
- Nucor (NUE) — steel for transformer cores
- Cleveland-Cliffs (CLF) — electrical steel
- Southwire (private) — copper wire & cable
- DuPont (DD) — insulation materials
- Weidmann (private) — transformer insulation

**Tier 2 — Equipment manufacturers:**
- Eaton (ETN) — transformers, switchgear, power management
- Hubbell (HUBB) — electrical components, grid hardware
- Ametek (AME) — power electronics, instrumentation
- Sensata (ST) — sensors for grid applications
- Roper Technologies (ROP) — industrial technology
- Prysmian (PRY.MI) — HV cable (Milan-listed, ADR available)
- Nexans (NEX.PA) — HV cable (Paris-listed)
- NKT (NKT.CO) — HV cable (Copenhagen-listed)
- SMA Solar (S92.DE) — grid inverters

**Tier 1 — Grid infrastructure & contractors:**
- Quanta Services (PWR) — grid construction, EPC
- MYR Group (MYRG) — electrical construction
- EMCOR Group (EME) — mechanical & electrical construction
- IES Holdings (IESC) — electrical infrastructure
- Argan (AGX) — power plant construction

**End market — Utilities & large buyers:**
- NextEra Energy (NEE) — largest US utility, massive grid capex
- Duke Energy (DUK)
- Dominion Energy (D)
- Xcel Energy (XEL)
- Entergy (ETR)
- American Electric Power (AEP)
- Consolidated Edison (ED)
- Evergy (EVRG)
- Portland General Electric (POR)
- Avangrid (AGR)

---

## 7. Key Files & Directory Structure

```
portfolio-system/
├── data/
│   ├── filings/          # Raw EDGAR filings
│   ├── transcripts/      # Earnings call transcripts
│   └── prices/           # Daily price CSVs (backup)
├── ingestion/
│   ├── edgar.py          # EDGAR filing downloader
│   ├── prices.py         # Polygon.io / yfinance price puller
│   ├── eia.py            # EIA grid data ingestion
│   └── news.py           # NewsAPI ingestion
├── ai_agent/
│   ├── prompts/
│   │   ├── fundamental_extraction.txt
│   │   ├── earnings_call_parsing.txt
│   │   └── supply_chain_extraction.txt
│   ├── runner.py         # Orchestrates AI extraction jobs
│   └── parser.py         # Parses Claude API output to structured JSON
├── graph/
│   ├── build.py          # Builds NetworkX graph from DB
│   ├── metrics.py        # Bottleneck, concentration, pricing power scores
│   └── signals.py        # Upstream demand signal detection
├── scoring/
│   └── composite.py      # Composite score calculator
├── api/
│   └── main.py           # FastAPI app
├── frontend/
│   └── src/              # React app
├── db/
│   └── schema.sql        # Full PostgreSQL schema
├── tests/
├── .env.example
├── requirements.txt
└── ARCHITECTURE.md       # This file
```

---

## 8. Environment Variables

```bash
# .env.example
ANTHROPIC_API_KEY=
POLYGON_API_KEY=
DATABASE_URL=postgresql://user:password@localhost:5432/portfolio
EIA_API_KEY=          # Free from eia.gov
NEWS_API_KEY=         # Free tier from newsapi.org
```

---

## 9. Important Constraints

- **No live trading in prototype.** All trades are paper trades logged in the decision log. Do not build any brokerage API integration until Phase 6 is complete and a 12-month paper track record exists.
- **AI outputs are research inputs, not decisions.** Every position change requires a human decision log entry.
- **Graph data quality over quantity.** It is better to have 50 high-confidence supply chain edges than 500 low-confidence ones. Every edge needs a source document citation.
- **Claude API costs.** Processing 30 companies × 4 filings/year × average 50k tokens/filing = ~6M tokens/year. Budget accordingly. Use `claude-haiku-4-5-20251001` for simple extraction tasks, `claude-sonnet-4-6` for complex analysis.
- **Non-US listed companies** (Prysmian, Nexans, NKT, SMA Solar): pull financials from their investor relations pages or use a data provider with international coverage. EDGAR will not have their filings.

---

*Last updated: April 2026 — built from architecture conversations with Claude.*