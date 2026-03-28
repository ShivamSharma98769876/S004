"""Short premium: option legs use bearish (premium weakness) indicator pack on LTP series."""

from app.services.option_chain_zerodha import (
    _add_ivr_to_chain,
    _apply_short_premium_skew_pcr_leg_scores,
    _indicator_pack_from_quote_fallback,
    _indicator_pack_from_quote_fallback_bearish,
    _indicator_pack_from_series,
    _indicator_pack_from_series_bearish,
)


def test_series_bearish_downtrend_primary_and_ema_weakness():
    ltps = [100.0 - i * 0.5 for i in range(30)]
    vols = [800.0 + (i % 3) * 50 for i in range(30)]
    bull = _indicator_pack_from_series(ltps, vols, 3, None, 45, 85, 1.1)
    bear = _indicator_pack_from_series_bearish(ltps, vols, 3, None, 45, 85, 1.1)
    assert bull["primaryOk"] is False
    assert bull["emaOk"] is False
    assert bear["primaryOk"] is True
    assert bear["emaOk"] is True


def test_series_bullish_four_factor_no_crossover_max_score_four():
    """TrendSnap-style: crossover excluded from score → at most 4 points."""
    ltps = [100.0 + i * 0.02 for i in range(30)]
    vols = [1000.0] * 29 + [2000.0]
    pack = _indicator_pack_from_series(
        ltps,
        vols,
        3,
        None,
        50,
        75,
        1.5,
        include_ema_crossover_in_score=False,
        strict_bullish_comparisons=True,
    )
    assert pack["score"] <= 4
    assert pack["signalEligible"] == (pack["primaryOk"] and pack["score"] >= 3)


def test_require_rsi_for_eligible_blocks_high_rsi_even_if_score_ok():
    """RSI hard gate: score can be 3+ from primary+EMA+vol but RSI out of band → not eligible."""
    ltps = [100.0 + i * 0.15 for i in range(30)]
    vols = [1000.0] * 29 + [2000.0]
    pack = _indicator_pack_from_series(
        ltps,
        vols,
        3,
        None,
        50,
        75,
        1.5,
        include_ema_crossover_in_score=False,
        strict_bullish_comparisons=True,
        require_rsi_for_eligible=True,
    )
    if not pack["rsiOk"]:
        assert pack["signalEligible"] is False
    else:
        assert pack["signalEligible"] == (pack["primaryOk"] and pack["score"] >= 3)


def test_quote_fallback_bearish_delegates_to_bearish_series():
    quote = {"ohlc": {"open": 50.0, "high": 52.0, "low": 45.0, "close": 46.0}}
    bull = _indicator_pack_from_quote_fallback(quote, 44.0, 2000.0, 3, None, 45, 85, 1.1)
    bear = _indicator_pack_from_quote_fallback_bearish(quote, 44.0, 2000.0, 3, None, 45, 85, 1.1)
    assert bear["primaryOk"] is True
    assert bear["emaOk"] is True
    assert bull["primaryOk"] is not bear["primaryOk"] or bull["emaOk"] is not bear["emaOk"]


def test_three_factor_bearish_rsi_below_50():
    ltps = [100.0 - i * 0.5 for i in range(30)]
    vols = [800.0 + (i % 3) * 50 for i in range(30)]
    pack = _indicator_pack_from_series_bearish(
        ltps,
        vols,
        3,
        None,
        45,
        85,
        1.1,
        leg_score_mode="three_factor",
        rsi_below_for_weak=50.0,
    )
    assert pack["score"] <= 3
    assert pack["technicalScore"] == pack["score"]
    assert pack["rsiOk"] is True


def test_three_factor_bearish_high_rsi_loses_point():
    ltps = [100.0 + i * 0.02 for i in range(30)]
    vols = [1000.0] * 30
    pack = _indicator_pack_from_series_bearish(
        ltps,
        vols,
        3,
        None,
        45,
        85,
        1.1,
        leg_score_mode="three_factor",
        rsi_below_for_weak=50.0,
    )
    assert pack["rsiOk"] is False


def test_three_factor_rsi_direct_band_overbought():
    """Uptrend LTP → high RSI; direct band 65–100 matches overbought short-premium intent."""
    ltps = [100.0 + i * 0.02 for i in range(30)]
    vols = [1000.0] * 30
    pack = _indicator_pack_from_series_bearish(
        ltps,
        vols,
        3,
        None,
        65.0,
        100.0,
        1.1,
        leg_score_mode="three_factor",
        rsi_below_for_weak=50.0,
        rsi_direct_band=True,
    )
    assert 65.0 <= pack["rsi"] <= 100.0
    assert pack["rsiOk"] is True


def test_apply_skew_pcr_bumps_ce_score():
    chain = [
        {
            "strike": 24000,
            "call": {
                "oi": "100000",
                "iv": 0.22,
                "score": 3,
                "technicalScore": 3,
                "scoreBonusSkew": 0,
                "scoreBonusPcr": 0,
            },
            "put": {
                "oi": "120000",
                "iv": 0.18,
                "score": 3,
                "technicalScore": 3,
                "scoreBonusSkew": 0,
                "scoreBonusPcr": 0,
            },
        }
    ]
    _add_ivr_to_chain(chain)
    ip = {
        "shortPremiumLegScoreMode": "three_factor",
        "scoreMaxLeg": 5,
        "shortPremiumIvrSkewMin": 5,
        "shortPremiumPcrBonusVsChain": True,
        "shortPremiumPcrChainEpsilon": 0,
    }
    _apply_short_premium_skew_pcr_leg_scores(chain, ip)
    call = chain[0]["call"]
    assert call["scoreBonusSkew"] == 1
    assert call["score"] >= 4
