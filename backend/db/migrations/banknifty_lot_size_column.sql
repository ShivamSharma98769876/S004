-- Per-user Bank Nifty F&O contract size (NSE revises periodically; verify on nseindia.com).

ALTER TABLE IF EXISTS s004_user_strategy_settings
    ADD COLUMN IF NOT EXISTS banknifty_lot_size INTEGER NOT NULL DEFAULT 30;

COMMENT ON COLUMN s004_user_strategy_settings.lot_size IS 'NIFTY (index options) contract multiplier per lot.';
COMMENT ON COLUMN s004_user_strategy_settings.banknifty_lot_size IS 'Bank Nifty index options contract multiplier per lot (NSE).';
