from app.services.trade_chain_snapshot_service import build_compact_chain_payload, trim_chain_around_strike


def _row(strike: int) -> dict:
    return {"strike": strike, "call": {"ltp": 1.0}, "put": {"ltp": 2.0}}


def test_trim_chain_around_strike_window():
    chain = [_row(22000 + i * 50) for i in range(40)]
    out = trim_chain_around_strike(chain, 22100, 12)
    strikes = sorted(int(r["strike"]) for r in out)
    assert len(strikes) <= 25
    assert 22100 in strikes or min(strikes, key=lambda s: abs(s - 22100)) in strikes
    assert min(strikes) >= 22000
    assert max(strikes) <= 22000 + 39 * 50


def test_build_compact_chain_payload_uses_trade_strike():
    full = {
        "spot": 22105.0,
        "spotChgPct": 0.1,
        "vix": 14.2,
        "pcr": 0.9,
        "pcrVol": 0.8,
        "updated": "2026-01-01T00:00:00Z",
        "chain": [_row(22050), _row(22100), _row(22150)],
    }
    p = build_compact_chain_payload(full, "NIFTY26JAN22100CE", 12)
    assert p["tradeStrike"] == 22100
    assert p["strikesWindowEachSide"] == 12
    assert len(p["chain"]) == 3
    assert "delta" in p["chain"][0]["call"]
