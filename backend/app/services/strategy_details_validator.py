"""Validate strategy details JSON before save. Returns list of error messages; empty means valid."""

from __future__ import annotations


def validate_strategy_details(details: dict) -> list[str]:
    """Validate strategy details structure. Returns list of error strings; empty if valid."""
    errors: list[str] = []

    if not isinstance(details, dict):
        return ["Strategy details must be a JSON object."]

    pi = details.get("positionIntent")
    if pi is not None and str(pi).strip().lower() not in ("long_premium", "short_premium", ""):
        errors.append("'positionIntent' must be 'long_premium' or 'short_premium'.")

    strategy_type = str(details.get("strategyType", "rule-based")).strip().lower()

    for bool_key in (
        "includeEmaCrossoverInScore",
        "strictBullishComparisons",
        "includeVolumeInLegScore",
        "requireRsiForEligible",
        "longPremiumSpotAlign",
    ):
        if bool_key in details and not isinstance(details.get(bool_key), bool):
            errors.append(f"'{bool_key}' must be a boolean.")

    if "spotRegimeMode" in details and not isinstance(details.get("spotRegimeMode"), str):
        errors.append("'spotRegimeMode' must be a string.")
    srs = details.get("spotRegimeSatisfiedScore")
    if srs is not None and not isinstance(srs, (int, float)):
        errors.append("'spotRegimeSatisfiedScore' must be a number.")

    if strategy_type == "trendpulse-z":
        tpz = details.get("trendPulseZ")
        if tpz is not None and not isinstance(tpz, dict):
            errors.append("'trendPulseZ' must be an object.")
        elif isinstance(tpz, dict):
            prof = tpz.get("profile")
            if prof is not None and str(prof).strip().lower() not in ("conservative", "balanced", "aggressive", ""):
                errors.append("'trendPulseZ.profile' must be 'conservative', 'balanced', or 'aggressive'.")
            sess = tpz.get("session")
            if sess is not None and not isinstance(sess, dict):
                errors.append("'trendPulseZ.session' must be an object.")
            br = tpz.get("breadth")
            if br is not None and not isinstance(br, dict):
                errors.append("'trendPulseZ.breadth' must be an object.")
            for key, label in [
                ("zWindow", "trendPulseZ.zWindow"),
                ("slopeLookback", "trendPulseZ.slopeLookback"),
                ("adxPeriod", "trendPulseZ.adxPeriod"),
                ("htfEmaFast", "trendPulseZ.htfEmaFast"),
                ("htfEmaSlow", "trendPulseZ.htfEmaSlow"),
                ("candleDaysBack", "trendPulseZ.candleDaysBack"),
            ]:
                if key in tpz and not isinstance(tpz.get(key), (int, float)):
                    errors.append(f"'{label}' must be a number.")
            for key, label in [
                ("adxMin", "trendPulseZ.adxMin"),
                ("ivRankMaxPercentile", "trendPulseZ.ivRankMaxPercentile"),
            ]:
                if key in tpz and not isinstance(tpz.get(key), (int, float)):
                    errors.append(f"'{label}' must be a number.")
            md = tpz.get("minDteCalendarDays")
            if md is not None and (
                type(md) is bool or not isinstance(md, int) or md < 0 or md > 120
            ):
                errors.append("'trendPulseZ.minDteCalendarDays' must be an integer 0–120.")
            nw = tpz.get("niftyWeeklyExpiryWeekday")
            if nw is not None and not isinstance(nw, (int, str)):
                errors.append("'trendPulseZ.niftyWeeklyExpiryWeekday' must be an int 0–6, weekday name, or 'ANY'.")
            mop = tpz.get("maxOptionPremiumInr")
            if mop is not None and (
                type(mop) is bool or not isinstance(mop, (int, float)) or float(mop) < 0
            ):
                errors.append("'trendPulseZ.maxOptionPremiumInr' must be a non-negative number (0 disables cap).")
            msr = tpz.get("maxStrikeRecommendations")
            if msr is not None and (
                type(msr) is bool or not isinstance(msr, int) or msr < 1 or msr > 10
            ):
                errors.append("'trendPulseZ.maxStrikeRecommendations' must be an integer 1–10.")
            if "selectStrikeByMaxGamma" in tpz and tpz.get("selectStrikeByMaxGamma") is not None:
                if not isinstance(tpz.get("selectStrikeByMaxGamma"), bool):
                    errors.append("'trendPulseZ.selectStrikeByMaxGamma' must be a boolean.")
        pi = str(details.get("positionIntent", "long_premium")).strip().lower()
        if pi and pi != "long_premium":
            errors.append("TrendPulse Z requires positionIntent 'long_premium'.")

    if strategy_type == "supertrend-trail":
        stt = details.get("superTrendTrail")
        if stt is not None and not isinstance(stt, dict):
            errors.append("'superTrendTrail' must be an object.")
        elif isinstance(stt, dict):
            for key, label in [
                ("emaFast", "superTrendTrail.emaFast"),
                ("emaSlow", "superTrendTrail.emaSlow"),
                ("atrPeriod", "superTrendTrail.atrPeriod"),
                ("candleDaysBack", "superTrendTrail.candleDaysBack"),
                ("minDteCalendarDays", "superTrendTrail.minDteCalendarDays"),
            ]:
                if key in stt and not isinstance(stt.get(key), (int, float)):
                    errors.append(f"'{label}' must be a number.")
            if "atrMultiplier" in stt and not isinstance(stt.get("atrMultiplier"), (int, float)):
                errors.append("'superTrendTrail.atrMultiplier' must be a number.")
            if "vwapStepThresholdPct" in stt and not isinstance(stt.get("vwapStepThresholdPct"), (int, float)):
                errors.append("'superTrendTrail.vwapStepThresholdPct' must be a number.")
            if "entryVsVwapEpsPct" in stt and not isinstance(stt.get("entryVsVwapEpsPct"), (int, float)):
                errors.append("'superTrendTrail.entryVsVwapEpsPct' must be a number.")
        pi = str(details.get("positionIntent", "short_premium")).strip().lower()
        if pi != "short_premium":
            errors.append("SuperTrendTrail requires positionIntent 'short_premium'.")

    if strategy_type == "stochastic-bnf":
        sb = details.get("stochasticBnf")
        if sb is not None and not isinstance(sb, dict):
            errors.append("'stochasticBnf' must be an object.")
        elif isinstance(sb, dict):
            for key, label in [
                ("adxPeriod", "stochasticBnf.adxPeriod"),
                ("candleDaysBack", "stochasticBnf.candleDaysBack"),
                ("rsiLength", "stochasticBnf.rsiLength"),
                ("stochLength", "stochasticBnf.stochLength"),
                ("stochK", "stochasticBnf.stochK"),
                ("stochD", "stochasticBnf.stochD"),
            ]:
                if key in sb and not isinstance(sb.get(key), (int, float)):
                    errors.append(f"'{label}' must be a number.")
            if "adxThreshold" in sb and not isinstance(sb.get("adxThreshold"), (int, float)):
                errors.append("'stochasticBnf.adxThreshold' must be a number.")
            if "overbought" in sb and not isinstance(sb.get("overbought"), (int, float)):
                errors.append("'stochasticBnf.overbought' must be a number.")
            if "oversold" in sb and not isinstance(sb.get("oversold"), (int, float)):
                errors.append("'stochasticBnf.oversold' must be a number.")
            for bkey in ("usePullbackEntry", "stochConfirmation", "vwapFilter", "timeFilter"):
                if bkey in sb and not isinstance(sb.get(bkey), bool):
                    errors.append(f"'stochasticBnf.{bkey}' must be a boolean.")
        pi = str(details.get("positionIntent", "short_premium")).strip().lower()
        if pi != "short_premium":
            errors.append("StochasticBNF requires positionIntent 'short_premium'.")

    if strategy_type == "ps-vs-mtf":
        pv = details.get("psVsMtf")
        if pv is not None and not isinstance(pv, dict):
            errors.append("'psVsMtf' must be an object.")
        elif isinstance(pv, dict):
            for key, label in [
                ("rsiPeriod", "psVsMtf.rsiPeriod"),
                ("psEmaPeriod", "psVsMtf.psEmaPeriod"),
                ("vsWmaPeriod", "psVsMtf.vsWmaPeriod"),
                ("atrPeriod", "psVsMtf.atrPeriod"),
                ("adxPeriod", "psVsMtf.adxPeriod"),
                ("candleDaysBack", "psVsMtf.candleDaysBack"),
            ]:
                if key in pv and not isinstance(pv.get(key), (int, float)):
                    errors.append(f"'{label}' must be a number.")
            for key, label in [
                ("adxMin", "psVsMtf.adxMin"),
                ("adxRef", "psVsMtf.adxRef"),
                ("atrRangeMin", "psVsMtf.atrRangeMin"),
                ("atrRangeMax", "psVsMtf.atrRangeMax"),
                ("rsiBandLow", "psVsMtf.rsiBandLow"),
                ("rsiBandHigh", "psVsMtf.rsiBandHigh"),
                ("minConvictionPct", "psVsMtf.minConvictionPct"),
                ("volumeVsPriorMult", "psVsMtf.volumeVsPriorMult"),
            ]:
                if key in pv and not isinstance(pv.get(key), (int, float)):
                    errors.append(f"'{label}' must be a number.")
            if "strict15m" in pv and not isinstance(pv.get("strict15m"), bool):
                errors.append("'psVsMtf.strict15m' must be a boolean.")

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
                if "minThreshold" in val and not isinstance(val.get("minThreshold"), (int, float)):
                    errors.append("'indicators.ivr.minThreshold' must be a number.")
                if "maxLegThreshold" in val and not isinstance(val.get("maxLegThreshold"), (int, float)):
                    errors.append("'indicators.ivr.maxLegThreshold' must be a number.")
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
        if "deltaMinAbs" in strike and not isinstance(strike.get("deltaMinAbs"), (int, float)):
            errors.append("'strikeSelection.deltaMinAbs' must be a number.")
        if "deltaMaxAbs" in strike and not isinstance(strike.get("deltaMaxAbs"), (int, float)):
            errors.append("'strikeSelection.deltaMaxAbs' must be a number.")
        md = strike.get("minDteCalendarDays")
        if md is not None and (
            type(md) is bool or not isinstance(md, int) or md < 0 or md > 120
        ):
            errors.append("'strikeSelection.minDteCalendarDays' must be an integer 0–120.")
        nw = strike.get("niftyWeeklyExpiryWeekday")
        if nw is not None and not isinstance(nw, (int, str)):
            errors.append(
                "'strikeSelection.niftyWeeklyExpiryWeekday' must be an int 0–6, weekday name, or 'ANY'."
            )
        if "selectStrikeByMinGamma" in strike and strike.get("selectStrikeByMinGamma") is not None:
            if not isinstance(strike.get("selectStrikeByMinGamma"), bool):
                errors.append("'strikeSelection.selectStrikeByMinGamma' must be a boolean.")
        msr = strike.get("maxStrikeRecommendations")
        if msr is not None and (
            type(msr) is bool or not isinstance(msr, int) or msr < 1 or msr > 10
        ):
            errors.append("'strikeSelection.maxStrikeRecommendations' must be an integer 1–10.")
        mves = strike.get("minVolumeEarlySession")
        if mves is not None and (
            type(mves) is bool or not isinstance(mves, int) or mves < 0 or mves > 1_000_000
        ):
            errors.append(
                "'strikeSelection.minVolumeEarlySession' must be an integer 0–1000000 (optional)."
            )
        esh = strike.get("earlySessionEndHourIST")
        if esh is not None and (
            type(esh) is bool or not isinstance(esh, int) or esh < 0 or esh > 23
        ):
            errors.append("'strikeSelection.earlySessionEndHourIST' must be an integer hour 0–23 (IST).")
        esm = strike.get("earlySessionEndMinuteIST")
        if esm is not None and (
            type(esm) is bool or not isinstance(esm, int) or esm < 0 or esm > 59
        ):
            errors.append(
                "'strikeSelection.earlySessionEndMinuteIST' must be an integer minute 0–59 (IST, optional)."
            )
        if "shortPremiumAsymmetricDatm" in strike and strike.get("shortPremiumAsymmetricDatm") is not None:
            if not isinstance(strike.get("shortPremiumAsymmetricDatm"), bool):
                errors.append("'strikeSelection.shortPremiumAsymmetricDatm' must be a boolean.")
        if "shortPremiumDeltaOnlyStrikes" in strike and strike.get("shortPremiumDeltaOnlyStrikes") is not None:
            if not isinstance(strike.get("shortPremiumDeltaOnlyStrikes"), bool):
                errors.append("'strikeSelection.shortPremiumDeltaOnlyStrikes' must be a boolean.")
        if "shortPremiumRsiDirectBand" in strike and strike.get("shortPremiumRsiDirectBand") is not None:
            if not isinstance(strike.get("shortPremiumRsiDirectBand"), bool):
                errors.append("'strikeSelection.shortPremiumRsiDirectBand' must be a boolean.")
        if "shortPremiumRsiDecreasing" in strike and strike.get("shortPremiumRsiDecreasing") is not None:
            if not isinstance(strike.get("shortPremiumRsiDecreasing"), bool):
                errors.append("'strikeSelection.shortPremiumRsiDecreasing' must be a boolean.")
        for sp_key in (
            "shortPremiumCeMinSteps",
            "shortPremiumCeMaxSteps",
            "shortPremiumPeMinSteps",
            "shortPremiumPeMaxSteps",
        ):
            v = strike.get(sp_key)
            if v is not None and (type(v) is bool or not isinstance(v, int) or abs(int(v)) > 30):
                errors.append(f"'strikeSelection.{sp_key}' must be an integer with |value| ≤ 30.")
        spv = strike.get("shortPremiumDeltaVixBands")
        if spv is not None:
            if not isinstance(spv, dict):
                errors.append("'strikeSelection.shortPremiumDeltaVixBands' must be an object.")
            else:
                thr = spv.get("threshold")
                if not isinstance(thr, (int, float)) or isinstance(thr, bool):
                    errors.append(
                        "'strikeSelection.shortPremiumDeltaVixBands.threshold' must be a number (e.g. 17)."
                    )
                above = spv.get("vixAbove") if isinstance(spv.get("vixAbove"), dict) else spv.get("aboveThreshold")
                below = (
                    spv.get("vixAtOrBelow")
                    if isinstance(spv.get("vixAtOrBelow"), dict)
                    else spv.get("belowThreshold")
                )
                if not isinstance(above, dict) or not isinstance(below, dict):
                    errors.append(
                        "'strikeSelection.shortPremiumDeltaVixBands' must include vixAbove and vixAtOrBelow "
                        "(or aboveThreshold / belowThreshold objects with deltaMinCE/MaxCE/MinPE/MaxPE)."
                    )
                else:
                    for label, br in (("vixAbove", above), ("vixAtOrBelow", below)):
                        for fk in ("deltaMinCE", "deltaMaxCE", "deltaMinPE", "deltaMaxPE"):
                            fv = br.get(fk)
                            if fv is None or isinstance(fv, bool) or not isinstance(fv, (int, float)):
                                errors.append(
                                    f"'strikeSelection.shortPremiumDeltaVixBands.{label}.{fk}' must be a number."
                                )
        _slm = strike.get("shortPremiumLegScoreMode")
        if _slm is not None and str(_slm).strip():
            allowed_modes = {"legacy", "three_factor"}
            if str(_slm).strip().lower() not in allowed_modes:
                errors.append(
                    "'strikeSelection.shortPremiumLegScoreMode' must be 'legacy' or 'three_factor' when set."
                )
        for _nk, _lbl in (
            ("shortPremiumRsiBelow", "shortPremiumRsiBelow"),
            ("shortPremiumIvrSkewMin", "shortPremiumIvrSkewMin"),
            ("shortPremiumPcrChainEpsilon", "shortPremiumPcrChainEpsilon"),
            ("shortPremiumPcrMinForSellCe", "shortPremiumPcrMinForSellCe"),
            ("shortPremiumPcrMaxForSellPe", "shortPremiumPcrMaxForSellPe"),
            ("shortPremiumExpansionBlockRsi", "shortPremiumExpansionBlockRsi"),
            ("shortPremiumVwapWeaknessMinPct", "shortPremiumVwapWeaknessMinPct"),
            ("shortPremiumMinMomentumPoints", "shortPremiumMinMomentumPoints"),
            ("shortPremiumGhostRsiDropPts", "shortPremiumGhostRsiDropPts"),
            ("shortPremiumRsiSoftZoneLow", "shortPremiumRsiSoftZoneLow"),
            ("shortPremiumRsiSoftZoneHigh", "shortPremiumRsiSoftZoneHigh"),
            ("shortPremiumRsiReversalFromRsi", "shortPremiumRsiReversalFromRsi"),
        ):
            _nv = strike.get(_nk)
            if _nv is not None and (
                isinstance(_nv, bool) or not isinstance(_nv, (int, float))
            ):
                errors.append(f"'strikeSelection.{_lbl}' must be a number when set.")
        _pvc = strike.get("shortPremiumPcrBonusVsChain")
        if _pvc is not None and not isinstance(_pvc, (bool, str)):
            errors.append("'strikeSelection.shortPremiumPcrBonusVsChain' must be a boolean.")
        _szor = strike.get("shortPremiumRsiZoneOrReversal")
        if _szor is not None and not isinstance(_szor, (bool, str)):
            errors.append("'strikeSelection.shortPremiumRsiZoneOrReversal' must be a boolean or string flag.")
        _srfb = strike.get("shortPremiumRsiReversalFallingBars")
        if _srfb is not None:
            if isinstance(_srfb, bool) or not isinstance(_srfb, (int, float)):
                errors.append(
                    "'strikeSelection.shortPremiumRsiReversalFallingBars' must be an integer 0–20 when set."
                )
            elif int(_srfb) < 0 or int(_srfb) > 20:
                errors.append(
                    "'strikeSelection.shortPremiumRsiReversalFallingBars' must be between 0 and 20."
                )
        _vwbuf = strike.get("shortPremiumVwapEligibleBufferPct")
        if _vwbuf is not None:
            if isinstance(_vwbuf, bool) or not isinstance(_vwbuf, (int, float)):
                errors.append(
                    "'strikeSelection.shortPremiumVwapEligibleBufferPct' must be a number 0–3 when set."
                )
            elif float(_vwbuf) < 0 or float(_vwbuf) > 3:
                errors.append(
                    "'strikeSelection.shortPremiumVwapEligibleBufferPct' must be between 0 and 3."
                )
        _tfvw = strike.get("shortPremiumThreeFactorRequireLtpBelowVwapForEligible")
        if _tfvw is not None and not isinstance(_tfvw, (bool, str)):
            errors.append(
                "'strikeSelection.shortPremiumThreeFactorRequireLtpBelowVwapForEligible' "
                "must be a boolean or flag string when set."
            )
        for _ik, _il in (
            ("shortPremiumIvrMinCe", "shortPremiumIvrMinCe"),
            ("shortPremiumIvrMinPe", "shortPremiumIvrMinPe"),
        ):
            _iv = strike.get(_ik)
            if _iv is not None:
                if isinstance(_iv, bool) or not isinstance(_iv, (int, float)):
                    errors.append(f"'strikeSelection.{_il}' must be a number 0–100 when set.")
                elif float(_iv) < 0 or float(_iv) > 100:
                    errors.append(f"'strikeSelection.{_il}' must be between 0 and 100.")
        fr = strike.get("flowRanking")
        if fr is not None:
            if not isinstance(fr, dict):
                errors.append("'strikeSelection.flowRanking' must be an object.")
            else:
                for fk, fl in (
                    ("tiltWeight", "tiltWeight"),
                    ("percentileOiWeight", "percentileOiWeight"),
                    ("percentileVolWeight", "percentileVolWeight"),
                    ("oiChgScaleWeight", "oiChgScaleWeight"),
                    ("longBuildupBonus", "longBuildupBonus"),
                    ("shortCoveringBonus", "shortCoveringBonus"),
                    ("pinMaxDistanceFromSpot", "pinMaxDistanceFromSpot"),
                    ("pinOiDominanceRatio", "pinOiDominanceRatio"),
                    ("pinPenaltyWeight", "pinPenaltyWeight"),
                ):
                    fv = fr.get(fk)
                    if fv is not None and (isinstance(fv, bool) or not isinstance(fv, (int, float))):
                        errors.append(f"'strikeSelection.flowRanking.{fl}' must be a number when set.")
                _ppin = fr.get("pinPenaltyOnExpiryDay")
                if _ppin is not None and not isinstance(_ppin, (bool, str)):
                    errors.append(
                        "'strikeSelection.flowRanking.pinPenaltyOnExpiryDay' must be a boolean or flag string when set."
                    )

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
