-- Option chain context around trades (entry / 30s sample while open / exit) + strategy-level EOD aggregates.

CREATE TABLE IF NOT EXISTS s004_trade_chain_snapshots (
    id BIGSERIAL PRIMARY KEY,
    trade_ref VARCHAR(64) NOT NULL REFERENCES s004_live_trades (trade_ref) ON DELETE CASCADE,
    recommendation_id VARCHAR(64) NOT NULL,
    user_id BIGINT NOT NULL REFERENCES s004_users (id) ON DELETE CASCADE,
    strategy_id VARCHAR(64) NOT NULL,
    strategy_version VARCHAR(32) NOT NULL,
    mode VARCHAR(10) NOT NULL CHECK (mode IN ('PAPER', 'LIVE')),
    phase VARCHAR(16) NOT NULL CHECK (phase IN ('entry', 'sample', 'exit')),
    captured_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    payload JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_s004_trade_chain_snapshots_trade_time
    ON s004_trade_chain_snapshots (trade_ref, captured_at DESC);

CREATE INDEX IF NOT EXISTS ix_s004_trade_chain_snapshots_strategy_time
    ON s004_trade_chain_snapshots (strategy_id, strategy_version, captured_at DESC);

CREATE INDEX IF NOT EXISTS ix_s004_trade_chain_snapshots_captured_at
    ON s004_trade_chain_snapshots (captured_at);

CREATE TABLE IF NOT EXISTS s004_strategy_eod_reports (
    id BIGSERIAL PRIMARY KEY,
    report_date_ist DATE NOT NULL,
    strategy_id VARCHAR(64) NOT NULL,
    strategy_version VARCHAR(32) NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ux_s004_strategy_eod_reports UNIQUE (report_date_ist, strategy_id, strategy_version)
);

CREATE INDEX IF NOT EXISTS ix_s004_strategy_eod_reports_date
    ON s004_strategy_eod_reports (report_date_ist DESC);
