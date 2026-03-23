-- Daily "market fit" picks: option buyer vs seller strategy suggestions for landing.
-- Outcomes aggregate realized PnL from s004_live_trades (EXIT) across all subscribed users.

CREATE TABLE IF NOT EXISTS s004_landing_strategy_fit_daily (
    fit_date DATE NOT NULL PRIMARY KEY,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    market_fingerprint JSONB NOT NULL DEFAULT '{}',
    buyer_strategy_id VARCHAR(64),
    buyer_strategy_version VARCHAR(32),
    buyer_score NUMERIC(8, 4),
    seller_strategy_id VARCHAR(64),
    seller_strategy_version VARCHAR(32),
    seller_score NUMERIC(8, 4),
    picks_json JSONB NOT NULL DEFAULT '{}',
    outcome_computed_at TIMESTAMPTZ,
    buyer_agg_pnl NUMERIC(14, 4),
    seller_agg_pnl NUMERIC(14, 4),
    buyer_bucket_median_pnl NUMERIC(14, 4),
    seller_bucket_median_pnl NUMERIC(14, 4),
    buyer_beat_median BOOLEAN,
    seller_beat_median BOOLEAN
);

CREATE INDEX IF NOT EXISTS ix_s004_landing_fit_outcome_pending
ON s004_landing_strategy_fit_daily (fit_date DESC)
WHERE outcome_computed_at IS NULL;
