-- W08 strategy marketplace schema

CREATE TABLE IF NOT EXISTS s004_strategy_catalog (
    id BIGSERIAL PRIMARY KEY,
    strategy_id VARCHAR(64) NOT NULL,
    version VARCHAR(32) NOT NULL,
    display_name VARCHAR(128) NOT NULL,
    description TEXT,
    risk_profile VARCHAR(20) NOT NULL, -- LOW / MEDIUM / HIGH
    supported_segments TEXT[] NOT NULL DEFAULT '{}',
    owner_type VARCHAR(20) NOT NULL, -- ADMIN / PUBLISHER
    publish_status VARCHAR(20) NOT NULL, -- DRAFT / PUBLISHED / ARCHIVED
    performance_snapshot JSONB,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (strategy_id, version)
);

CREATE INDEX IF NOT EXISTS ix_s004_strategy_catalog_lookup
ON s004_strategy_catalog (publish_status, risk_profile, updated_at DESC);


CREATE TABLE IF NOT EXISTS s004_strategy_subscriptions (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    strategy_id VARCHAR(64) NOT NULL,
    strategy_version VARCHAR(32) NOT NULL,
    mode VARCHAR(10) NOT NULL, -- PAPER / LIVE
    status VARCHAR(20) NOT NULL, -- ACTIVE / PAUSED / STOPPED
    user_config JSONB,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, strategy_id, strategy_version)
);

CREATE INDEX IF NOT EXISTS ix_s004_strategy_subscriptions_lookup
ON s004_strategy_subscriptions (user_id, status, updated_at DESC);


CREATE TABLE IF NOT EXISTS s004_strategy_subscription_events (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    strategy_id VARCHAR(64) NOT NULL,
    strategy_version VARCHAR(32) NOT NULL,
    event_type VARCHAR(40) NOT NULL, -- SUBSCRIBED / UNSUBSCRIBED / MODE_CHANGED / PAUSED / RESUMED
    reason_code VARCHAR(64),
    payload JSONB,
    occurred_at TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_s004_strategy_subscription_events_lookup
ON s004_strategy_subscription_events (user_id, strategy_id, occurred_at DESC);

