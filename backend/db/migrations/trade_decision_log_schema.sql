-- Auto-execute factual decision log (per user, periodic) + optional market snapshot on trade open for heatmaps.

CREATE TABLE IF NOT EXISTS s004_auto_execute_decision_log (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES s004_users(id),
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    mode VARCHAR(10),
    strategy_id VARCHAR(64),
    strategy_version VARCHAR(32),
    gate_blocked BOOLEAN NOT NULL DEFAULT FALSE,
    gate_reason VARCHAR(96),
    cycle_summary VARCHAR(48),
    auto_trade_threshold NUMERIC(14, 4),
    score_display_threshold NUMERIC(14, 4),
    min_confidence_threshold NUMERIC(14, 4),
    open_trades INTEGER,
    trades_today INTEGER,
    max_parallel INTEGER,
    max_trades_day INTEGER,
    within_trade_window BOOLEAN,
    has_kite_live BOOLEAN,
    daily_pnl_ok BOOLEAN,
    market_context JSONB,
    evaluations JSONB,
    executed_recommendation_ids JSONB
);

CREATE INDEX IF NOT EXISTS ix_s004_auto_decision_user_time
    ON s004_auto_execute_decision_log (user_id, occurred_at DESC);

CREATE INDEX IF NOT EXISTS ix_s004_auto_decision_occurred
    ON s004_auto_execute_decision_log (occurred_at DESC);

ALTER TABLE IF EXISTS s004_live_trades
    ADD COLUMN IF NOT EXISTS entry_market_snapshot JSONB;

COMMENT ON COLUMN s004_live_trades.entry_market_snapshot IS
    'At entry: pcr, pcr_bucket, nifty_chg_pct, regime_label, india_vix (optional), reason_code, score, confidence from recommendation.';
