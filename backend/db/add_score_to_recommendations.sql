-- Add score column to recommendations so Score displays in Strategy Signals
-- (no longer relies only on in-memory cache)
ALTER TABLE IF EXISTS s004_trade_recommendations
    ADD COLUMN IF NOT EXISTS score INTEGER;
