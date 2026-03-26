-- Functional Core Schema for S004 major workflows
-- Covers:
-- 1) Admin strategy define/configure/publish for user/admin live-paper usage
-- 2) User/Admin settings persistence
-- 3) Option-chain analytics persistence
-- 4) Ranked trade candidates + manual execution capture
-- 5) Executed trades visible on dashboard via queryable tables/views

CREATE TABLE IF NOT EXISTS s004_users (
    id BIGSERIAL PRIMARY KEY,
    username VARCHAR(80) NOT NULL UNIQUE,
    full_name VARCHAR(120),
    role VARCHAR(16) NOT NULL CHECK (role IN ('ADMIN', 'USER')),
    status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'DISABLED')),
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS s004_strategy_catalog (
    id BIGSERIAL PRIMARY KEY,
    strategy_id VARCHAR(64) NOT NULL,
    version VARCHAR(32) NOT NULL,
    display_name VARCHAR(128) NOT NULL,
    description TEXT,
    risk_profile VARCHAR(20) NOT NULL CHECK (risk_profile IN ('LOW', 'MEDIUM', 'HIGH')),
    owner_type VARCHAR(20) NOT NULL CHECK (owner_type IN ('ADMIN', 'PUBLISHER')),
    publish_status VARCHAR(20) NOT NULL CHECK (publish_status IN ('DRAFT', 'PUBLISHED', 'ARCHIVED')),
    execution_modes TEXT[] NOT NULL DEFAULT ARRAY['PAPER'],
    supported_segments TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    performance_snapshot JSONB,
    created_by BIGINT REFERENCES s004_users(id),
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (strategy_id, version)
);

CREATE INDEX IF NOT EXISTS ix_s004_strategy_catalog_status
ON s004_strategy_catalog (publish_status, updated_at DESC);

CREATE TABLE IF NOT EXISTS s004_strategy_config_versions (
    id BIGSERIAL PRIMARY KEY,
    strategy_id VARCHAR(64) NOT NULL,
    strategy_version VARCHAR(32) NOT NULL,
    config_version INTEGER NOT NULL,
    config_json JSONB NOT NULL,
    active BOOLEAN NOT NULL DEFAULT FALSE,
    changed_by BIGINT REFERENCES s004_users(id),
    changed_reason VARCHAR(256),
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (strategy_id, strategy_version, config_version)
);

CREATE INDEX IF NOT EXISTS ix_s004_strategy_cfg_active
ON s004_strategy_config_versions (strategy_id, strategy_version, active, created_at DESC);

CREATE TABLE IF NOT EXISTS s004_strategy_subscriptions (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES s004_users(id),
    strategy_id VARCHAR(64) NOT NULL,
    strategy_version VARCHAR(32) NOT NULL,
    mode VARCHAR(10) NOT NULL CHECK (mode IN ('PAPER', 'LIVE')),
    status VARCHAR(20) NOT NULL CHECK (status IN ('ACTIVE', 'PAUSED', 'STOPPED')),
    user_config JSONB,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, strategy_id, strategy_version)
);

CREATE INDEX IF NOT EXISTS ix_s004_strategy_sub_lookup
ON s004_strategy_subscriptions (user_id, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS s004_user_master_settings (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL UNIQUE REFERENCES s004_users(id),
    go_live BOOLEAN NOT NULL DEFAULT FALSE,
    engine_running BOOLEAN NOT NULL DEFAULT FALSE,
    broker_connected BOOLEAN NOT NULL DEFAULT FALSE,
    shared_api_connected BOOLEAN NOT NULL DEFAULT TRUE,
    platform_api_online BOOLEAN NOT NULL DEFAULT TRUE,
    mode VARCHAR(10) NOT NULL DEFAULT 'PAPER' CHECK (mode IN ('PAPER', 'LIVE')),
    max_parallel_trades INTEGER NOT NULL DEFAULT 3,
    max_trades_day INTEGER NOT NULL DEFAULT 4,
    max_profit_day NUMERIC(14,2) NOT NULL DEFAULT 5000,
    max_loss_day NUMERIC(14,2) NOT NULL DEFAULT 2000,
    initial_capital NUMERIC(16,2) NOT NULL DEFAULT 100000,
    max_investment_per_trade NUMERIC(16,2) NOT NULL DEFAULT 50000,
    credentials_json JSONB,
    updated_by BIGINT REFERENCES s004_users(id),
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS s004_user_strategy_settings (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES s004_users(id),
    strategy_id VARCHAR(64) NOT NULL,
    strategy_version VARCHAR(32) NOT NULL,
    lots INTEGER NOT NULL DEFAULT 1,
    lot_size INTEGER NOT NULL DEFAULT 65,
    max_strike_distance_atm INTEGER NOT NULL DEFAULT 5,
    max_premium NUMERIC(14,2) NOT NULL DEFAULT 200,
    min_premium NUMERIC(14,2) NOT NULL DEFAULT 30,
    min_entry_strength_pct NUMERIC(8,2) NOT NULL DEFAULT 0,
    sl_type VARCHAR(20) NOT NULL DEFAULT 'Fixed Points',
    sl_points NUMERIC(10,2) NOT NULL DEFAULT 15,
    breakeven_trigger_pct NUMERIC(8,2) NOT NULL DEFAULT 50,
    target_points NUMERIC(10,2) NOT NULL DEFAULT 10,
    trailing_sl_points NUMERIC(10,2) NOT NULL DEFAULT 20,
    timeframe VARCHAR(16) NOT NULL DEFAULT '3-min',
    trade_start TIME NOT NULL DEFAULT '09:15',
    trade_end TIME NOT NULL DEFAULT '15:00',
    enabled_indices TEXT[] NOT NULL DEFAULT ARRAY['NIFTY'],
    auto_pause_after_losses INTEGER NOT NULL DEFAULT 3,
    updated_by BIGINT REFERENCES s004_users(id),
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, strategy_id, strategy_version)
);

CREATE INDEX IF NOT EXISTS ix_s004_user_strategy_settings_lookup
ON s004_user_strategy_settings (user_id, strategy_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS s004_option_chain (
    id BIGSERIAL PRIMARY KEY,
    instrument VARCHAR(20) NOT NULL,
    expiry VARCHAR(20) NOT NULL,
    strike NUMERIC(12,2) NOT NULL,
    option_type VARCHAR(2) NOT NULL CHECK (option_type IN ('CE', 'PE')),
    ltp NUMERIC(14,4) NOT NULL,
    ltp_change_pct NUMERIC(12,4),
    volume BIGINT NOT NULL DEFAULT 0,
    open_interest BIGINT NOT NULL DEFAULT 0,
    oi_change_pct NUMERIC(12,4),
    iv_pct NUMERIC(12,6),
    delta NUMERIC(12,6),
    theta NUMERIC(12,6),
    buildup VARCHAR(32),
    captured_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_s004_option_chain_bucket
ON s004_option_chain (instrument, expiry, strike, option_type, captured_at);

CREATE INDEX IF NOT EXISTS ix_s004_option_chain_lookup
ON s004_option_chain (instrument, expiry, captured_at DESC);

CREATE TABLE IF NOT EXISTS s004_strike_scores (
    id BIGSERIAL PRIMARY KEY,
    instrument VARCHAR(20) NOT NULL,
    expiry VARCHAR(20) NOT NULL,
    strike NUMERIC(12,2) NOT NULL,
    option_type VARCHAR(2) NOT NULL CHECK (option_type IN ('CE', 'PE')),
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

CREATE TABLE IF NOT EXISTS s004_trade_recommendations (
    id BIGSERIAL PRIMARY KEY,
    recommendation_id VARCHAR(64) NOT NULL UNIQUE,
    strategy_id VARCHAR(64) NOT NULL,
    strategy_version VARCHAR(32) NOT NULL,
    user_id BIGINT NOT NULL REFERENCES s004_users(id),
    instrument VARCHAR(20) NOT NULL,
    expiry VARCHAR(20) NOT NULL,
    symbol VARCHAR(100) NOT NULL,
    side VARCHAR(10) NOT NULL,
    entry_price NUMERIC(14,4) NOT NULL,
    target_price NUMERIC(14,4) NOT NULL,
    stop_loss_price NUMERIC(14,4) NOT NULL,
    confidence_score NUMERIC(14,6) NOT NULL,
    rank_value INTEGER NOT NULL,
    reason_code VARCHAR(64) NOT NULL,
    status VARCHAR(20) NOT NULL CHECK (status IN ('GENERATED', 'ACCEPTED', 'REJECTED', 'EXPIRED', 'SKIPPED')),
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_s004_trade_recommendations_lookup
ON s004_trade_recommendations (user_id, strategy_id, created_at DESC, rank_value ASC);

CREATE TABLE IF NOT EXISTS s004_execution_orders (
    id BIGSERIAL PRIMARY KEY,
    order_ref VARCHAR(64) NOT NULL UNIQUE,
    recommendation_id VARCHAR(64) NOT NULL,
    user_id BIGINT NOT NULL REFERENCES s004_users(id),
    requested_mode VARCHAR(10) NOT NULL CHECK (requested_mode IN ('PAPER', 'LIVE')),
    side VARCHAR(10) NOT NULL,
    quantity INTEGER NOT NULL,
    requested_price NUMERIC(14,4),
    manual_execute BOOLEAN NOT NULL DEFAULT TRUE,
    order_status VARCHAR(20) NOT NULL CHECK (order_status IN ('PENDING', 'PLACED', 'FILLED', 'REJECTED', 'CANCELLED')),
    broker_order_id VARCHAR(128),
    order_payload JSONB,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_s004_execution_orders_lookup
ON s004_execution_orders (user_id, order_status, created_at DESC);

CREATE TABLE IF NOT EXISTS s004_live_trades (
    id BIGSERIAL PRIMARY KEY,
    trade_ref VARCHAR(64) NOT NULL UNIQUE,
    order_ref VARCHAR(64) NOT NULL,
    recommendation_id VARCHAR(64) NOT NULL,
    user_id BIGINT NOT NULL REFERENCES s004_users(id),
    strategy_id VARCHAR(64) NOT NULL,
    strategy_version VARCHAR(32) NOT NULL,
    symbol VARCHAR(100) NOT NULL,
    mode VARCHAR(10) NOT NULL CHECK (mode IN ('PAPER', 'LIVE')),
    side VARCHAR(10) NOT NULL,
    quantity INTEGER NOT NULL,
    entry_price NUMERIC(14,4) NOT NULL,
    current_price NUMERIC(14,4),
    target_price NUMERIC(14,4) NOT NULL,
    stop_loss_price NUMERIC(14,4) NOT NULL,
    current_state VARCHAR(20) NOT NULL CHECK (current_state IN ('ENTRY', 'ACTIVE', 'TRAIL', 'EXIT', 'REJECTED')),
    realized_pnl NUMERIC(14,4) NOT NULL DEFAULT 0,
    unrealized_pnl NUMERIC(14,4) NOT NULL DEFAULT 0,
    opened_at TIMESTAMP NOT NULL,
    closed_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_s004_live_trades_dashboard
ON s004_live_trades (user_id, current_state, updated_at DESC);

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

CREATE TABLE IF NOT EXISTS s004_dashboard_snapshots (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES s004_users(id),
    snapshot_ts TIMESTAMP NOT NULL,
    open_trades INTEGER NOT NULL DEFAULT 0,
    closed_trades INTEGER NOT NULL DEFAULT 0,
    gross_pnl NUMERIC(14,4) NOT NULL DEFAULT 0,
    net_pnl NUMERIC(14,4) NOT NULL DEFAULT 0,
    unrealized_pnl NUMERIC(14,4) NOT NULL DEFAULT 0,
    win_rate_pct NUMERIC(8,2) NOT NULL DEFAULT 0,
    data_json JSONB
);

CREATE INDEX IF NOT EXISTS ix_s004_dashboard_snapshots_lookup
ON s004_dashboard_snapshots (user_id, snapshot_ts DESC);

CREATE OR REPLACE VIEW s004_dashboard_live_view AS
SELECT
    t.user_id,
    COUNT(*) FILTER (WHERE t.current_state <> 'EXIT') AS open_trades,
    COUNT(*) FILTER (WHERE t.current_state = 'EXIT') AS closed_trades,
    COALESCE(SUM(t.realized_pnl), 0) AS realized_pnl,
    COALESCE(SUM(t.unrealized_pnl), 0) AS unrealized_pnl,
    MAX(t.updated_at) AS latest_update_at
FROM s004_live_trades t
GROUP BY t.user_id;

-- Compatibility upgrades for environments where older W03-W08 tables already exist.
ALTER TABLE IF EXISTS s004_strategy_catalog
    ADD COLUMN IF NOT EXISTS execution_modes TEXT[] NOT NULL DEFAULT ARRAY['PAPER'],
    ADD COLUMN IF NOT EXISTS created_by BIGINT REFERENCES s004_users(id);

ALTER TABLE IF EXISTS s004_option_chain
    ADD COLUMN IF NOT EXISTS ltp_change_pct NUMERIC(12,4),
    ADD COLUMN IF NOT EXISTS iv_pct NUMERIC(12,6),
    ADD COLUMN IF NOT EXISTS delta NUMERIC(12,6),
    ADD COLUMN IF NOT EXISTS theta NUMERIC(12,6),
    ADD COLUMN IF NOT EXISTS buildup VARCHAR(32);

ALTER TABLE IF EXISTS s004_trade_recommendations
    ADD COLUMN IF NOT EXISTS strategy_version VARCHAR(32) NOT NULL DEFAULT '1.0.0',
    ADD COLUMN IF NOT EXISTS rank_value INTEGER NOT NULL DEFAULT 9999,
    ADD COLUMN IF NOT EXISTS score INTEGER;

ALTER TABLE IF EXISTS s004_trade_recommendations
    ADD COLUMN IF NOT EXISTS details_json JSONB;

ALTER TABLE IF EXISTS s004_live_trades
    ADD COLUMN IF NOT EXISTS order_ref VARCHAR(64),
    ADD COLUMN IF NOT EXISTS strategy_version VARCHAR(32) NOT NULL DEFAULT '1.0.0',
    ADD COLUMN IF NOT EXISTS side VARCHAR(10) NOT NULL DEFAULT 'BUY',
    ADD COLUMN IF NOT EXISTS current_price NUMERIC(14,4),
    ADD COLUMN IF NOT EXISTS realized_pnl NUMERIC(14,4) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS unrealized_pnl NUMERIC(14,4) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS opened_at TIMESTAMP,
    ADD COLUMN IF NOT EXISTS closed_at TIMESTAMP,
    ADD COLUMN IF NOT EXISTS broker_order_id VARCHAR(128);

-- Older deployments may have created s004_execution_orders without this column
ALTER TABLE IF EXISTS s004_execution_orders
    ADD COLUMN IF NOT EXISTS manual_execute BOOLEAN NOT NULL DEFAULT TRUE;

ALTER TABLE IF EXISTS s004_user_master_settings
    ADD COLUMN IF NOT EXISTS charges_per_trade NUMERIC(10,2) NOT NULL DEFAULT 20;

ALTER TABLE IF EXISTS s004_user_strategy_settings
    ADD COLUMN IF NOT EXISTS strategy_details_json JSONB;

ALTER TABLE IF EXISTS s004_strategy_catalog
    ADD COLUMN IF NOT EXISTS strategy_details_json JSONB;
