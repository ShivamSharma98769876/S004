-- Align default trade window start with NSE F&O cash session open (09:15 IST).
-- Does not change existing user_strategy_settings rows; only default for new inserts.

ALTER TABLE s004_user_strategy_settings
    ALTER COLUMN trade_start SET DEFAULT TIME '09:15:00';
