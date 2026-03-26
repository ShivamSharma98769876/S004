-- Strategy Evolution: daily performance rollups, optimization recommendations, version changelog.

CREATE TABLE IF NOT EXISTS s004_strategy_daily_metrics (
    id BIGSERIAL PRIMARY KEY,
    strategy_id VARCHAR(64) NOT NULL,
    strategy_version VARCHAR(32) NOT NULL,
    trade_date_ist DATE NOT NULL,
    closed_trades INTEGER NOT NULL DEFAULT 0,
    winning_trades INTEGER NOT NULL DEFAULT 0,
    losing_trades INTEGER NOT NULL DEFAULT 0,
    realized_pnl NUMERIC(14, 4) NOT NULL DEFAULT 0,
    gross_win_pnl NUMERIC(14, 4) NOT NULL DEFAULT 0,
    gross_loss_pnl NUMERIC(14, 4) NOT NULL DEFAULT 0,
    win_rate_pct NUMERIC(8, 2),
    metrics_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    computed_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (strategy_id, strategy_version, trade_date_ist)
);

CREATE INDEX IF NOT EXISTS ix_s004_strategy_daily_metrics_lookup
ON s004_strategy_daily_metrics (strategy_id, strategy_version, trade_date_ist DESC);

CREATE TABLE IF NOT EXISTS s004_strategy_evolution_recommendations (
    id BIGSERIAL PRIMARY KEY,
    strategy_id VARCHAR(64) NOT NULL,
    from_version VARCHAR(32) NOT NULL,
    recommendation_code VARCHAR(64) NOT NULL,
    proposed_title VARCHAR(256) NOT NULL,
    rationale_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    proposed_details_patch JSONB NOT NULL DEFAULT '{}'::jsonb,
    status VARCHAR(24) NOT NULL
        CHECK (status IN ('DRAFT', 'PENDING_REVIEW', 'APPROVED', 'REJECTED', 'IMPLEMENTED', 'SUPERSEDED')),
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    approved_by BIGINT REFERENCES s004_users (id),
    approved_at TIMESTAMP,
    implemented_version VARCHAR(32)
);

CREATE INDEX IF NOT EXISTS ix_s004_evolution_rec_strategy
ON s004_strategy_evolution_recommendations (strategy_id, status, created_at DESC);

CREATE TABLE IF NOT EXISTS s004_strategy_version_changelog (
    id BIGSERIAL PRIMARY KEY,
    strategy_id VARCHAR(64) NOT NULL,
    from_version VARCHAR(32) NOT NULL,
    to_version VARCHAR(32) NOT NULL,
    summary TEXT,
    changelog_md TEXT,
    recommendation_id BIGINT REFERENCES s004_strategy_evolution_recommendations (id),
    created_by BIGINT REFERENCES s004_users (id),
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_s004_strategy_changelog_lookup
ON s004_strategy_version_changelog (strategy_id, created_at DESC);
