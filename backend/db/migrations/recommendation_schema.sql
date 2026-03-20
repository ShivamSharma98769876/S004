-- W05 recommendation persistence schema

CREATE TABLE IF NOT EXISTS s004_trade_recommendations (
    id BIGSERIAL PRIMARY KEY,
    recommendation_id VARCHAR(64) NOT NULL UNIQUE,
    strategy_id VARCHAR(64) NOT NULL,
    user_id BIGINT NOT NULL,
    instrument VARCHAR(20) NOT NULL,
    expiry VARCHAR(20) NOT NULL,
    symbol VARCHAR(100) NOT NULL,
    side VARCHAR(10) NOT NULL,
    entry_price NUMERIC(14,4) NOT NULL,
    target_price NUMERIC(14,4) NOT NULL,
    stop_loss_price NUMERIC(14,4) NOT NULL,
    confidence_score NUMERIC(14,6) NOT NULL,
    reason_code VARCHAR(64) NOT NULL,
    status VARCHAR(20) NOT NULL, -- GENERATED / ACCEPTED / EXPIRED / SKIPPED
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_s004_trade_recommendations_lookup
ON s004_trade_recommendations (user_id, strategy_id, created_at DESC);

