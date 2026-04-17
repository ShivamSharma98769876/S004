-- TrendSnap Momentum v1.0.0: RSI floor ≥50 with relaxed upper band; early-session volume relax until 10:30 IST.
UPDATE s004_strategy_catalog
SET strategy_details_json =
  jsonb_set(
    jsonb_set(
      jsonb_set(
        jsonb_set(
          COALESCE(strategy_details_json, '{}'::jsonb),
          '{indicators,rsi}',
          COALESCE(strategy_details_json->'indicators'->'rsi', '{}'::jsonb)
            || '{"min": 50, "max": 100, "description": "RSI at or above 50 adds one point (upper band relaxed)."}'::jsonb,
          true
        ),
        '{strikeSelection}',
        COALESCE(strategy_details_json->'strikeSelection', '{}'::jsonb)
          || '{"earlySessionEndMinuteIST": 30}'::jsonb,
        true
      ),
      '{description}',
      to_jsonb(
        'Four-factor option read on the latest candle: close above VWAP (required gate), EMA9 above EMA21, RSI ≥ 50 (no 75 cap), volume above 1.0x average. RSI must be in band for eligibility (not score-only). NIFTY spot trend must align: bullish for CE, bearish for PE. Strike choice ranks eligible legs by score then by option flow (aligned with landing CE/PE tilt), OI/volume depth, OI change, and Long Buildup. Early session uses relaxed min contract volume until 10:30 IST. Exits use SL, target, and breakeven from Settings.'::text
      ),
      true
    ),
    '{scoreDescription}',
    to_jsonb(
      'Primary: latest option close must be above VWAP (otherwise no signal). Score 0-4: +1 VWAP pass, +1 EMA9 above EMA21, +1 RSI ≥ 50, +1 volume above 1.0x average. No crossover or IVR points. Eligible BUY when score >= 3 AND RSI ≥ 50 AND NIFTY spot regime matches leg (bullish/CE, bearish/PE). Among ties, strikes rank by flow (landing-style CE/PE tilt, OI/vol percentiles, OI change, Long Buildup). Auto-execute still requires autoTradeScoreThreshold.'::text
    ),
    true
  ),
  updated_at = NOW()
WHERE strategy_id = 'strat-trendsnap-momentum' AND version = '1.0.0';
