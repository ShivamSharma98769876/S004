-- W06 execution, events, and position reconciliation schema

CREATE TABLE IF NOT EXISTS s004_live_trades (
    id BIGSERIAL PRIMARY KEY,
    trade_ref VARCHAR(64) NOT NULL UNIQUE,
    recommendation_id VARCHAR(64) NOT NULL,
    user_id BIGINT NOT NULL,
    strategy_id VARCHAR(64) NOT NULL,
    symbol VARCHAR(100) NOT NULL,
    mode VARCHAR(10) NOT NULL, -- PAPER / LIVE
    quantity INTEGER NOT NULL,
    entry_price NUMERIC(14,4) NOT NULL,
    target_price NUMERIC(14,4) NOT NULL,
    stop_loss_price NUMERIC(14,4) NOT NULL,
    current_state VARCHAR(20) NOT NULL, -- ENTRY / ACTIVE / TRAIL / EXIT / REJECTED
    broker_order_id VARCHAR(128),
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_s004_live_trades_lookup
ON s004_live_trades (user_id, strategy_id, created_at DESC);


CREATE TABLE IF NOT EXISTS s004_trade_events (
    id BIGSERIAL PRIMARY KEY,
    trade_ref VARCHAR(64) NOT NULL,
    event_type VARCHAR(40) NOT NULL,
    prev_state VARCHAR(20),
    next_state VARCHAR(20),
    reason_code VARCHAR(64),
    event_payload JSONB,
    occurred_at TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_s004_trade_events_lookup
ON s004_trade_events (trade_ref, occurred_at DESC);


CREATE TABLE IF NOT EXISTS s004_position_reconciliation (
    id BIGSERIAL PRIMARY KEY,
    run_id VARCHAR(64) NOT NULL,
    user_id BIGINT NOT NULL,
    symbol VARCHAR(100) NOT NULL,
    issue_type VARCHAR(40) NOT NULL,
    internal_value TEXT,
    broker_value TEXT,
    detected_at TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_s004_position_reconciliation_lookup
ON s004_position_reconciliation (user_id, detected_at DESC);

