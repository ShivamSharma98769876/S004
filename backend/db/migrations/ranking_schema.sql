-- W04 ranking + liquidity persistence schema

CREATE TABLE IF NOT EXISTS s004_strike_scores (
    id BIGSERIAL PRIMARY KEY,
    instrument VARCHAR(20) NOT NULL,
    expiry VARCHAR(20) NOT NULL,
    strike NUMERIC(12,2) NOT NULL,
    option_type VARCHAR(2) NOT NULL, -- CE / PE
    confidence_score NUMERIC(14,6) NOT NULL,
    technical_score NUMERIC(14,6) NOT NULL,
    volume_score NUMERIC(14,6) NOT NULL,
    oi_score NUMERIC(14,6) NOT NULL,
    greeks_score NUMERIC(14,6) NOT NULL,
    liquidity_score NUMERIC(14,6) NOT NULL,
    rank_value INTEGER NOT NULL,
    cycle_ts TIMESTAMP NOT NULL,
    model_version VARCHAR(32) NOT NULL DEFAULT 'v1',
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_s004_strike_scores_cycle
ON s004_strike_scores (instrument, expiry, strike, option_type, cycle_ts);

CREATE INDEX IF NOT EXISTS ix_s004_strike_scores_lookup
ON s004_strike_scores (instrument, expiry, cycle_ts DESC, rank_value ASC);


CREATE TABLE IF NOT EXISTS s004_liquidity_zones (
    id BIGSERIAL PRIMARY KEY,
    instrument VARCHAR(20) NOT NULL,
    expiry VARCHAR(20) NOT NULL,
    strike NUMERIC(12,2) NOT NULL,
    trap_type VARCHAR(20) NOT NULL, -- BULL_TRAP / BEAR_TRAP / NONE
    confidence NUMERIC(8,2) NOT NULL,
    reason TEXT,
    cycle_ts TIMESTAMP NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_s004_liquidity_zones_lookup
ON s004_liquidity_zones (instrument, expiry, cycle_ts DESC, confidence DESC);

