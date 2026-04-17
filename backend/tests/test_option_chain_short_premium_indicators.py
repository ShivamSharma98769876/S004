"""Short premium: option legs use bearish (premium weakness) indicator pack on LTP series."""

import pytest

from app.services import option_chain_zerodha as ocz
from app.services.option_chain_zerodha import (
    _add_ivr_to_chain,
    _apply_short_premium_enrichment_filters,
    _apply_short_premium_skew_pcr_leg_scores,
    _augment_option_leg_series_if_thin,
    _indicator_pack_from_quote_fallback,
    _indicator_pack_from_quote_fallback_bearish,
    _indicator_pack_from_series,
    _indicator_pack_from_series_bearish,
    _resolve_regime_sell_pe_ce_at_strike,
    _rsi_strictly_falling_last_n_bars,
    _strike_leg_regime_sell_pe,
)


def test_augment_option_leg_series_fetches_hist_when_poll_short():
    ocz._OPTION_LEG_HIST_CACHE.clear()

    class _Kite:
        def historical_data(self, token, frm, to, interval):
            assert token == 999001
            return [{"close": 100.0 + i * 0.05, "volume": 500} for i in range(40)]

    poll_ltps = [100.0]
    poll_vols = [100.0]
    row = {"instrument_token": 999001}
    bud = [5]
    mlp, mvo = _augment_option_leg_series_if_thin(_Kite(), poll_ltps, poll_vols, 102.5, 800.0, row, bud)
    assert len(mlp) >= 30
    assert mlp[-1] == 102.5
    assert bud[0] == 4


def test_augment_option_leg_series_skips_when_poll_long_enough():
    class _Kite:
        def historical_data(self, *a, **k):
            raise AssertionError("should not fetch when poll series is warm")

    long_poll = [100.0 + i * 0.01 for i in range(25)]
    long_vols = [1000.0] * 25
    row = {"instrument_token": 999002}
    bud = [5]
    a, b = _augment_option_leg_series_if_thin(_Kite(), long_poll, long_vols, 100.24, 100.0, row, bud)
    assert a is long_poll
    assert b is long_vols
    assert bud[0] == 5


def test_regime_pe_sustained_bearish_when_cross_is_stale():
    """After max_candles_since_cross, regime still passes if LTP<VWAP and rounded EMA9<EMA21."""
    n = 40
    ltps = [100.0 - i * 0.22 for i in range(n)]
    vols = [400.0 + (i % 4) * 25.0 for i in range(n)]
    ok, _bb = _strike_leg_regime_sell_pe(ltps, vols, max_cross_i=2)
    assert ok is True


def test_resolve_regime_sell_pe_ce_tie_bars_keeps_both_legs(monkeypatch: pytest.MonkeyPatch):
    """Same bars-since on PE and CE must not force regimeSellPe=regimeSellCe=false."""

    def _pe(*a: object, **k: object) -> tuple[bool, int]:
        return True, 3

    def _ce(*a: object, **k: object) -> tuple[bool, int]:
        return True, 3

    monkeypatch.setattr(ocz, "_strike_leg_regime_sell_pe", _pe)
    monkeypatch.setattr(ocz, "_strike_leg_regime_sell_ce", _ce)
    pe_ok, ce_ok = _resolve_regime_sell_pe_ce_at_strike(
        [1.0] * 25, [1.0] * 25, [1.0] * 25, [1.0] * 25, 8
    )
    assert pe_ok is True and ce_ok is True


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


def test_vwap_eligible_buffer_pct_relaxes_primary_ok():
    ltps = [100.0] * 29 + [100.02]
    vols = [1000.0] * 30
    strict = _indicator_pack_from_series_bearish(
        ltps,
        vols,
        3,
        None,
        45,
        85,
        1.1,
        vwap_eligible_buffer_pct=0.0,
    )
    buffered = _indicator_pack_from_series_bearish(
        ltps,
        vols,
        3,
        None,
        45,
        85,
        1.1,
        vwap_eligible_buffer_pct=0.08,
    )
    assert strict["primaryOk"] is False
    assert buffered["primaryOk"] is True


def test_three_factor_eligible_without_ltp_below_vwap_when_disabled():
    """Score-only eligibility: 2/3 factors can pass threshold 2 without primaryOk."""
    ltps = [110 - i * 0.5 for i in range(29)] + [104.0]
    vols = [1000.0] * 30
    relaxed = _indicator_pack_from_series_bearish(
        ltps,
        vols,
        2,
        None,
        45,
        85,
        1.1,
        leg_score_mode="three_factor",
        rsi_below_for_weak=56.0,
        three_factor_require_ltp_below_vwap_for_eligible=False,
    )
    strict_eligible = _indicator_pack_from_series_bearish(
        ltps,
        vols,
        2,
        None,
        45,
        85,
        1.1,
        leg_score_mode="three_factor",
        rsi_below_for_weak=56.0,
        three_factor_require_ltp_below_vwap_for_eligible=True,
    )
    assert relaxed["primaryOk"] is False
    assert int(relaxed["score"]) >= 2
    assert relaxed["signalEligible"] is True
    assert strict_eligible["signalEligible"] is False


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


def test_three_factor_rsi_decreasing_below_threshold():
    """Leg RSI must be below threshold and strictly below prior-bar RSI (same period as leg RSI)."""
    ltps = [100.0 - i * 0.12 + (0.15 if i % 2 == 0 else -0.15) for i in range(29)] + [96.0]
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
        rsi_direct_band=True,
        rsi_require_decreasing=True,
    )
    assert pack["rsiOk"] is True


def test_three_factor_rsi_decreasing_overrides_direct_band():
    """When rsi_require_decreasing is True, shortPremiumRsiDirectBand (overbought) must not apply."""
    ltps = [100.0 + i * 0.02 for i in range(30)]
    vols = [1000.0] * 30
    overbought = _indicator_pack_from_series_bearish(
        ltps,
        vols,
        3,
        None,
        65.0,
        100.0,
        1.1,
        leg_score_mode="three_factor",
        rsi_direct_band=True,
    )
    assert overbought["rsiOk"] is True
    decreasing_mode = _indicator_pack_from_series_bearish(
        ltps,
        vols,
        3,
        None,
        65.0,
        100.0,
        1.1,
        leg_score_mode="three_factor",
        rsi_direct_band=True,
        rsi_require_decreasing=True,
    )
    assert decreasing_mode["rsiOk"] is False


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


def test_zone_or_reversal_soft_zone_when_prior_bar_rsi_unavailable():
    """With only 3 LTP points there is no prior-bar RSI; soft zone must still satisfy Branch A."""
    ltps = [100.0, 98.0, 96.0]
    vols = [1000.0] * 3
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
        rsi_zone_or_reversal=True,
        rsi_soft_zone_low=0.0,
        rsi_soft_zone_high=50.0,
        rsi_reversal_from_rsi=70.0,
    )
    assert pack["rsiPrev"] is None
    assert pack["rsiOk"] is True


def test_three_factor_rsi_reversal_falling_bars_strictly_falling():
    """Branch B: last N RSI closes strictly decreasing (oldest > … > current)."""
    base = [100.0 + 0.02 * i for i in range(27)]
    ltps = base + [100.5, 98.0, 94.0, 89.0]
    vols = [1000.0] * len(ltps)
    assert _rsi_strictly_falling_last_n_bars(ltps, 3) is True
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
        rsi_zone_or_reversal=True,
        rsi_soft_zone_low=20.0,
        rsi_soft_zone_high=45.0,
        rsi_reversal_falling_bars=3,
    )
    assert pack["rsiOk"] is True


def test_three_factor_rsi_zone_or_reversal_overrides_decreasing():
    """Soft zone 20–45 OR reversal from overbought; overrides rsi_require_decreasing when both requested."""
    ltps = [100.0 + i * 0.05 for i in range(25)] + [101.2 - i * 0.35 for i in range(5)]
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
        rsi_require_decreasing=True,
        rsi_zone_or_reversal=True,
        rsi_soft_zone_low=20.0,
        rsi_soft_zone_high=45.0,
        rsi_reversal_from_rsi=70.0,
    )
    assert pack["rsiOk"] is True
    assert pack["rsiPrev"] is not None


def test_enrichment_expansion_block_clears_eligible():
    chain = [
        {
            "strike": 24000,
            "call": {
                "signalEligible": True,
                "primaryOk": True,
                "emaOk": True,
                "rsiOk": True,
                "rsi": 80.0,
                "vwap": 100.0,
                "ltp": 105.0,
                "rsiPrev": 82.0,
            },
        }
    ]
    ip = {"positionIntent": "short_premium", "shortPremiumExpansionBlockRsi": 75.0}
    _apply_short_premium_enrichment_filters(chain, ip, 3)
    assert chain[0]["call"]["signalEligible"] is False
    assert chain[0]["call"].get("shortPremiumExpansionBlocked") is True


def test_enrichment_ghost_rsi_drop_requires_points():
    chain = [
        {
            "strike": 24000,
            "call": {
                "signalEligible": True,
                "primaryOk": True,
                "emaOk": True,
                "rsiOk": True,
                "rsi": 48.0,
                "vwap": 100.0,
                "ltp": 95.0,
                "rsiPrev": 50.0,
            },
        }
    ]
    ip = {"positionIntent": "short_premium", "shortPremiumGhostRsiDropPts": 5.0}
    _apply_short_premium_enrichment_filters(chain, ip, 3)
    assert chain[0]["call"]["signalEligible"] is False
    assert chain[0]["call"].get("shortPremiumGhostBlocked") is True
