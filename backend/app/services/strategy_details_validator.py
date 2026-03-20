"""Validate strategy details JSON before save. Returns list of error messages; empty means valid."""

from __future__ import annotations


def validate_strategy_details(details: dict) -> list[str]:
    """Validate strategy details structure. Returns list of error strings; empty if valid."""
    errors: list[str] = []

    if not isinstance(details, dict):
        return ["Strategy details must be a JSON object."]

    strategy_type = str(details.get("strategyType", "rule-based")).strip().lower()

    # heuristics (for heuristic-voting strategy type)
    if strategy_type == "heuristic-voting":
        heuristics = details.get("heuristics")
        if heuristics is not None and not isinstance(heuristics, dict):
            errors.append("'heuristics' must be an object.")
        elif isinstance(heuristics, dict):
            for hkey, hval in heuristics.items():
                if isinstance(hval, dict):
                    if "weight" in hval and not isinstance(hval.get("weight"), (int, float)):
                        errors.append(f"'heuristics.{hkey}.weight' must be a number.")
                    if "enabled" in hval and not isinstance(hval.get("enabled"), bool):
                        errors.append(f"'heuristics.{hkey}.enabled' must be a boolean.")

        for alt_key in ("heuristicsCE", "heuristicsPE"):
            alt = details.get(alt_key)
            if alt is not None and not isinstance(alt, dict):
                errors.append(f"'{alt_key}' must be an object.")
        he_enh = details.get("heuristicEnhancements")
        if he_enh is not None and not isinstance(he_enh, dict):
            errors.append("'heuristicEnhancements' must be an object.")
        for st_alt in ("scoreThresholdCE", "scoreThresholdPE"):
            v = details.get(st_alt)
            if v is not None and not isinstance(v, (int, float)):
                errors.append(f"'{st_alt}' must be a number.")

    # indicators (for rule-based)
    indicators = details.get("indicators")
    if indicators is not None and not isinstance(indicators, dict):
        errors.append("'indicators' must be an object.")
    elif isinstance(indicators, dict):
        for key, val in indicators.items():
            if val is not None and not isinstance(val, dict):
                errors.append(f"'indicators.{key}' must be an object.")
                continue
            if isinstance(val, dict) and key == "ema":
                if "fast" in val and not isinstance(val.get("fast"), (int, float)):
                    errors.append("'indicators.ema.fast' must be a number.")
                if "slow" in val and not isinstance(val.get("slow"), (int, float)):
                    errors.append("'indicators.ema.slow' must be a number.")
            elif isinstance(val, dict) and key == "rsi":
                if "period" in val and not isinstance(val.get("period"), (int, float)):
                    errors.append("'indicators.rsi.period' must be a number.")
                if "min" in val and not isinstance(val.get("min"), (int, float)):
                    errors.append("'indicators.rsi.min' must be a number.")
                if "max" in val and not isinstance(val.get("max"), (int, float)):
                    errors.append("'indicators.rsi.max' must be a number.")
            elif isinstance(val, dict) and key == "emaCrossover":
                if "maxCandlesSinceCross" in val and not isinstance(val.get("maxCandlesSinceCross"), (int, float)):
                    errors.append("'indicators.emaCrossover.maxCandlesSinceCross' must be a number.")
            elif isinstance(val, dict) and key == "adx":
                if "period" in val and not isinstance(val.get("period"), (int, float)):
                    errors.append("'indicators.adx.period' must be a number.")
                if "minThreshold" in val and not isinstance(val.get("minThreshold"), (int, float)):
                    errors.append("'indicators.adx.minThreshold' must be a number.")
            elif isinstance(val, dict) and key == "ivr":
                if "maxThreshold" in val and not isinstance(val.get("maxThreshold"), (int, float)):
                    errors.append("'indicators.ivr.maxThreshold' must be a number.")
            elif isinstance(val, dict) and key == "volumeSpike":
                if "minRatio" in val and not isinstance(val.get("minRatio"), (int, float)):
                    errors.append("'indicators.volumeSpike.minRatio' must be a number.")

    # strikeSelection
    strike = details.get("strikeSelection")
    if strike is not None and not isinstance(strike, dict):
        errors.append("'strikeSelection' must be an object.")
    elif isinstance(strike, dict):
        if "minOi" in strike and not isinstance(strike.get("minOi"), (int, float)):
            errors.append("'strikeSelection.minOi' must be a number.")
        if "minVolume" in strike and not isinstance(strike.get("minVolume"), (int, float)):
            errors.append("'strikeSelection.minVolume' must be a number.")
        if "maxOtmSteps" in strike and not isinstance(strike.get("maxOtmSteps"), (int, float)):
            errors.append("'strikeSelection.maxOtmSteps' must be a number.")
        if "deltaPreferredCE" in strike and not isinstance(strike.get("deltaPreferredCE"), (int, float)):
            errors.append("'strikeSelection.deltaPreferredCE' must be a number.")
        if "deltaPreferredPE" in strike and not isinstance(strike.get("deltaPreferredPE"), (int, float)):
            errors.append("'strikeSelection.deltaPreferredPE' must be a number.")

    # score thresholds
    for key, label in [
        ("scoreThreshold", "scoreThreshold"),
        ("scoreMax", "scoreMax"),
        ("autoTradeScoreThreshold", "autoTradeScoreThreshold"),
    ]:
        v = details.get(key)
        if v is not None and not isinstance(v, (int, float)):
            errors.append(f"'{key}' must be a number.")
        elif isinstance(v, (int, float)) and (v < 0 or v > 20):
            errors.append(f"'{key}' must be between 0 and 20.")

    return errors
