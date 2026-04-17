"""StochasticBNF: config and exit helpers."""

from app.strategies.stochastic_bnf import (
    resolve_stochastic_bnf_config,
    should_exit_on_ema5_15_cross,
)


def test_resolve_defaults():
    c = resolve_stochastic_bnf_config({})
    assert c["adxThreshold"] == 20
    assert c["rsiLength"] == 14
    assert c["stochLength"] == 14
    assert c["stochK"] == 3
    assert c["stochD"] == 3
    assert c["overbought"] == 70
    assert c["oversold"] == 30


def test_ema_exit_short_pe():
    assert should_exit_on_ema5_15_cross(option_type="PE", ema5=499.0, ema15=500.0) is True
    assert should_exit_on_ema5_15_cross(option_type="PE", ema5=501.0, ema15=500.0) is False


def test_ema_exit_short_ce():
    assert should_exit_on_ema5_15_cross(option_type="CE", ema5=501.0, ema15=500.0) is True
    assert should_exit_on_ema5_15_cross(option_type="CE", ema5=499.0, ema15=500.0) is False
