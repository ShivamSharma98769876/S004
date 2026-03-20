-- W03-S04: analytics output persistence schema
-- These tables align with PRD v2.5 analytics needs.

CREATE TABLE IF NOT EXISTS s004_option_chain (
    id BIGSERIAL PRIMARY KEY,
    instrument VARCHAR(20) NOT NULL,
    expiry VARCHAR(20) NOT NULL,
    strike NUMERIC(12,2) NOT NULL,
    option_type VARCHAR(2) NOT NULL, -- CE / PE
    ltp NUMERIC(14,4) NOT NULL,
    volume BIGINT NOT NULL DEFAULT 0,
    open_interest BIGINT NOT NULL DEFAULT 0,
    oi_change_pct NUMERIC(12,4),
    captured_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_s004_option_chain_bucket
ON s004_option_chain (instrument, expiry, strike, option_type, captured_at);

CREATE INDEX IF NOT EXISTS ix_s004_option_chain_lookup
ON s004_option_chain (instrument, expiry, captured_at DESC);


CREATE TABLE IF NOT EXISTS s004_option_greeks (
    id BIGSERIAL PRIMARY KEY,
    instrument VARCHAR(20) NOT NULL,
    expiry VARCHAR(20) NOT NULL,
    strike NUMERIC(12,2) NOT NULL,
    option_type VARCHAR(2) NOT NULL, -- CE / PE
    delta NUMERIC(12,6),
    gamma NUMERIC(12,6),
    theta NUMERIC(12,6),
    vega NUMERIC(12,6),
    iv_pct NUMERIC(12,6),
    model_version VARCHAR(32) NOT NULL DEFAULT 'v1',
    captured_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_s004_option_greeks_bucket
ON s004_option_greeks (instrument, expiry, strike, option_type, captured_at);

CREATE INDEX IF NOT EXISTS ix_s004_option_greeks_lookup
ON s004_option_greeks (instrument, expiry, captured_at DESC);


CREATE TABLE IF NOT EXISTS s004_analytics_pipeline_runs (
    id BIGSERIAL PRIMARY KEY,
    run_id VARCHAR(64) NOT NULL UNIQUE,
    status VARCHAR(20) NOT NULL, -- STARTED / COMPLETED / FAILED
    records_written INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    started_at TIMESTAMP NOT NULL,
    completed_at TIMESTAMP
);

