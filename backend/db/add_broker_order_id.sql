-- Add broker_order_id to s004_live_trades for Live mode order tracking
ALTER TABLE IF EXISTS s004_live_trades
    ADD COLUMN IF NOT EXISTS broker_order_id VARCHAR(128);
