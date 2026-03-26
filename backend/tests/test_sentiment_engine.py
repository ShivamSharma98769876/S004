"""Tests for landing sentiment / options intel."""

from app.services.sentiment_engine import compute_sentiment_snapshot


def _sample_chain_row() -> dict:
    return {
        "call": {"oi": 1000, "volume": 200, "oiChgPct": 2.0, "ltpChg": 1.0, "ivr": 18.0},
        "put": {"oi": 800, "volume": 150, "oiChgPct": 1.0, "ltpChg": -0.5, "ivr": 20.0},
    }


def test_options_intel_ce_tilt_low_pcr() -> None:
    payload = {
        "pcr": 0.75,
        "pcrVol": 0.8,
        "chain": [_sample_chain_row() for _ in range(12)],
    }
    out = compute_sentiment_snapshot(chain_payload=payload, spot_chg_pct=0.5, trendpulse_signal=None)
    intel = out.get("optionsIntel") or {}
    assert intel.get("modelOptionTilt") == "CE"
    assert intel.get("oiDominant") == "CE"
    assert "CE" in str(intel.get("headline") or "")
    pcr_driver = next((d for d in (out.get("drivers") or []) if d.get("key") == "pcr"), None)
    assert pcr_driver and "0.75" in str(pcr_driver.get("reading") or "")
    assert pcr_driver.get("impact") != 0


def test_options_intel_pe_tilt_high_pcr() -> None:
    payload = {
        "pcr": 1.35,
        "pcrVol": 1.4,
        "chain": [
            {
                "call": {"oi": 500, "volume": 80, "oiChgPct": 0.5, "ltpChg": -0.5, "ivr": 22.0},
                "put": {"oi": 1200, "volume": 300, "oiChgPct": 4.0, "ltpChg": 1.2, "ivr": 24.0},
            }
            for _ in range(12)
        ],
    }
    out = compute_sentiment_snapshot(chain_payload=payload, spot_chg_pct=-0.8, trendpulse_signal=None)
    intel = out.get("optionsIntel") or {}
    assert intel.get("modelOptionTilt") == "PE"
    assert intel.get("oiDominant") == "PE"


def test_drivers_pcr_neutral_shows_ratio_in_reading_not_impact() -> None:
    """PCR 1.0 maps to zero *signal* so weighted impact is 0; reading still shows the ratio."""
    out = compute_sentiment_snapshot(
        chain_payload={"pcr": 1.0, "pcrVol": 1.0, "chain": []},
        spot_chg_pct=0.0,
    )
    pcr_d = next((d for d in (out.get("drivers") or []) if d.get("key") == "pcr"), None)
    assert pcr_d is not None
    assert float(pcr_d.get("impact") or 0) == 0.0
    assert "1.00" in str(pcr_d.get("reading") or "")


def test_options_intel_thin_chain_caveat() -> None:
    out = compute_sentiment_snapshot(chain_payload={"pcr": 1.0, "pcrVol": 1.0, "chain": []}, spot_chg_pct=0.0)
    intel = out.get("optionsIntel") or {}
    assert intel.get("hasChainData") is False
    assert "limited" in str(intel.get("dataCaveat") or "").lower()


def test_thin_chain_blends_bullish_index_into_ce_tilt() -> None:
    out = compute_sentiment_snapshot(
        chain_payload={"pcr": 1.0, "pcrVol": 1.0, "chain": []},
        spot_chg_pct=1.2,
    )
    intel = out.get("optionsIntel") or {}
    assert intel.get("modelOptionTilt") == "CE"
    assert int(intel.get("ceStrengthPct") or 0) >= 52


def test_thin_chain_blends_bearish_index_into_pe_tilt() -> None:
    out = compute_sentiment_snapshot(
        chain_payload={"pcr": 1.0, "pcrVol": 1.0, "chain": []},
        spot_chg_pct=-1.2,
    )
    intel = out.get("optionsIntel") or {}
    assert intel.get("modelOptionTilt") == "PE"
    assert int(intel.get("peStrengthPct") or 0) >= 52
