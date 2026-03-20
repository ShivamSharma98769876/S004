-- Add charges_per_trade column for brokerage/charges calculation
-- Run this if you see: column "charges_per_trade" does not exist
-- Usage: psql -h localhost -U postgres -d tradingpro -f add_charges_per_trade.sql
-- Or execute in your DB client:
ALTER TABLE IF EXISTS s004_user_master_settings
ADD COLUMN IF NOT EXISTS charges_per_trade NUMERIC(10,2) NOT NULL DEFAULT 20;
