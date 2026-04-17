-- TrendSnap: four-factor score (strict close>VWAP, EMA9>EMA21, RSI 50-75, vol>minRatio), threshold 3 / max 4; no crossover or IVR in score.
UPDATE s004_strategy_catalog
SET strategy_details_json = '{
      "displayName": "TrendSnap Momentum",
      "description": "Simple four-factor option read on the latest candle: close above VWAP (required gate), EMA9 above EMA21, RSI 50-75, volume above 1.0x average. Signal when at least three of four factors pass. Exits use SL, target, and breakeven from Settings.",
      "includeEmaCrossoverInScore": false,
      "strictBullishComparisons": true,
      "indicators": {
        "ema": {"fast": 9, "slow": 21, "description": "EMA9 strictly above EMA21 adds one point."},
        "emaCrossover": {"bonus": 0, "maxCandlesSinceCross": 10, "description": "Not counted in score; metadata only."},
        "ivr": {"bonus": 0, "maxThreshold": 20, "description": "IVR for reference on the chain; no score bonus."},
        "rsi": {"period": 14, "min": 50, "max": 75, "description": "RSI between 50 and 75 adds one point."},
        "vwap": {"description": "Latest candle close strictly above VWAP is the primary gate and first point."},
        "volumeSpike": {"minRatio": 1.0, "description": "Volume strictly above 1.0x recent average adds one point."}
      },
      "strikeSelection": {
        "minOi": 5000,
        "minVolume": 300,
        "maxOtmSteps": 3,
        "deltaPreferredCE": 0.45,
        "deltaPreferredPE": -0.45,
        "description": "Liquidity: min OI 5k, min volume 300. Max 3 steps OTM. Prefer delta near 0.45 CE / -0.45 PE; rank by score and fit."
      },
      "scoreThreshold": 3,
      "scoreMax": 4,
      "autoTradeScoreThreshold": 4,
      "scoreDescription": "Primary: latest option close must be above VWAP (otherwise no signal). Score 0-4: +1 VWAP pass, +1 EMA9 above EMA21, +1 RSI 50-75, +1 volume above 1.0x average. No crossover or IVR points. Eligible BUY CE/PE when score is at least 3."
    }'::jsonb,
    updated_at = NOW()
WHERE strategy_id = 'strat-trendsnap-momentum' AND version = '1.0.0';
