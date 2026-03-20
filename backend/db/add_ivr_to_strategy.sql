-- Add IVR indicator and scoreMax 6 to TrendSnap Momentum strategy.
-- Run: psql $DATABASE_URL -f db/add_ivr_to_strategy.sql

UPDATE s004_strategy_catalog
SET strategy_details_json = '{
  "displayName": "TrendSnap Momentum",
  "description": "Momentum crossover option strategy. Enters when short-term momentum confirms direction with price-action continuation and risk checks; exits use SL, target, and breakeven rules from Settings.",
  "indicators": {
    "ema": {"fast": 9, "slow": 21, "description": "EMA9 > EMA21 = bullish momentum (short-term above long-term)"},
    "emaCrossover": {"bonus": 1, "description": "Fast EMA crossed above slow EMA from lower to upper = +1 score bonus (bullish crossover)"},
    "rsi": {"period": 14, "min": 50, "max": 75, "description": "RSI in 50-75 = not overbought, bullish zone"},
    "vwap": {"description": "Price above VWAP = bullish intraday bias"},
    "volumeSpike": {"minRatio": 1.5, "description": "Current volume > 1.5x average = confirmation"},
    "ivr": {"maxThreshold": 20, "bonus": 1, "description": "IVR < 20 = low IV (cheap options) = +1 score bonus. IVR from Option Analytics per strike."}
  },
  "scoreThreshold": 3,
  "scoreMax": 6,
  "autoTradeScoreThreshold": 4,
  "scoreDescription": "Score 0-6: Primary(VWAP) + EMA + RSI + Volume + EMA crossover bonus + IVR bonus (when IVR<20). Signal when score >= 3. Auto-trade when score >= 4. IVR from Option Analytics."
}'::jsonb,
updated_at = NOW()
WHERE strategy_id = 'strat-trendsnap-momentum' AND version = '1.0.0';
