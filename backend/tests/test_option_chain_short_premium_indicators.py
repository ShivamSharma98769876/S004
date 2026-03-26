"""Short premium: option legs use bearish (premium weakness) indicator pack on LTP series."""

from app.services.option_chain_zerodha import (
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


def test_quote_fallback_bearish_delegates_to_bearish_series():
    quote = {"ohlc": {"open": 50.0, "high": 52.0, "low": 45.0, "close": 46.0}}
    bull = _indicator_pack_from_quote_fallback(quote, 44.0, 2000.0, 3, None, 45, 85, 1.1)
    bear = _indicator_pack_from_quote_fallback_bearish(quote, 44.0, 2000.0, 3, None, 45, 85, 1.1)
    assert bear["primaryOk"] is True
    assert bear["emaOk"] is True
    assert bull["primaryOk"] is not bear["primaryOk"] or bull["emaOk"] is not bear["emaOk"]
