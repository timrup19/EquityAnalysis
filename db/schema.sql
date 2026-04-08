-- Portfolio Intelligence System — Database Schema
-- Source: GRAPH_DESIGN.md sections 2–5
-- All four core tables for the supply chain graph layer.

-- ============================================================
-- Section 2: Companies (nodes)
-- ============================================================
CREATE TABLE companies (
    id                  SERIAL PRIMARY KEY,
    ticker              VARCHAR(10),                -- NULL for private companies
    name                VARCHAR(255) NOT NULL,
    sector              VARCHAR(100) NOT NULL,       -- e.g. 'grid_energy'
    tier                INTEGER NOT NULL,            -- 0, 1, 2, or 3
    is_public           BOOLEAN NOT NULL,
    country             VARCHAR(50) DEFAULT 'US',
    is_bottleneck       BOOLEAN DEFAULT FALSE,       -- manually flagged, updated by scoring
    lead_time_weeks     INTEGER,                     -- tier 2 only: current equipment lead time
    backlog_usd         BIGINT,                      -- tier 1 only: reported order backlog
    capex_guided_usd    BIGINT,                      -- tier 0 only: guided annual capex
    notes               TEXT,                        -- analyst notes, sourcing quirks
    created_at          TIMESTAMP DEFAULT NOW(),
    updated_at          TIMESTAMP DEFAULT NOW()
);

-- ============================================================
-- Section 3: Supply chain edges (relationships)
-- Direction: supplier → customer (upstream to downstream)
-- ============================================================
CREATE TABLE supply_chain_edges (
    id                  SERIAL PRIMARY KEY,
    supplier_id         INTEGER NOT NULL REFERENCES companies(id),
    customer_id         INTEGER NOT NULL REFERENCES companies(id),
    relationship_type   VARCHAR(50) NOT NULL,        -- 'sole_source', 'preferred_supplier', 'commodity_supplier', 'capacity_dependency'
    revenue_dependency  FLOAT,                       -- % of supplier revenue from this customer
    substitution_ease   INTEGER,                     -- 1 (impossible) to 5 (trivial) — SET MANUALLY
    lead_time_weeks     INTEGER,                     -- weeks from order to delivery for this edge
    confidence          FLOAT NOT NULL DEFAULT 0.5,  -- 0.0 to 1.0, set by AI extraction
    source_doc          VARCHAR(512),                -- filing or transcript that revealed this edge
    source_date         DATE,                        -- date of source document
    extracted_by        VARCHAR(50) DEFAULT 'manual', -- 'manual' or 'ai_agent'
    valid_from          DATE,
    valid_to            DATE,                        -- NULL = currently active
    created_at          TIMESTAMP DEFAULT NOW(),
    updated_at          TIMESTAMP DEFAULT NOW()
);

-- ============================================================
-- Section 4: Supply chain signals (derived events)
-- ============================================================
CREATE TABLE supply_chain_signals (
    id              SERIAL PRIMARY KEY,
    company_id      INTEGER NOT NULL REFERENCES companies(id),
    signal_type     VARCHAR(100) NOT NULL,           -- e.g. 'capex_increase', 'lead_time_extension'
    signal_value    TEXT,                             -- quantified value where possible
    direction       VARCHAR(10),                     -- 'positive', 'negative', 'neutral'
    estimated_lag   INTEGER,                         -- quarters until signal appears in earnings
    source_doc      VARCHAR(512),
    signal_date     DATE NOT NULL,
    created_at      TIMESTAMP DEFAULT NOW()
);

-- ============================================================
-- Section 5: Derived scores (computed weekly)
-- ============================================================
CREATE TABLE company_scores (
    id                    SERIAL PRIMARY KEY,
    company_id            INTEGER NOT NULL REFERENCES companies(id),
    score_date            DATE NOT NULL,
    bottleneck_score      FLOAT,                     -- 0 to 10
    concentration_risk    FLOAT,                     -- 0 to 10 (higher = more risk)
    pricing_power_score   FLOAT,                     -- 0 to 10
    upstream_demand_score FLOAT,                     -- 0 to 10
    composite_sc_score    FLOAT,                     -- 0 to 10, weighted average
    UNIQUE(company_id, score_date)
);
