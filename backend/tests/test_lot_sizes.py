from app.services.lot_sizes import contract_multiplier_for_trade


def test_bn_by_instrument():
    assert contract_multiplier_for_trade(
        instrument="BANKNIFTY", nifty_lot=65, banknifty_lot=30
    ) == 30


def test_bn_by_strategy_id():
    assert contract_multiplier_for_trade(
        strategy_id="strat-stochastic-bnf", nifty_lot=65, banknifty_lot=28
    ) == 28
    assert contract_multiplier_for_trade(
        strategy_id="strat-ps-vs-mtf", nifty_lot=65, banknifty_lot=28
    ) == 28


def test_bn_by_symbol():
    assert contract_multiplier_for_trade(
        symbol="BANKNIFTY24APR50000CE", nifty_lot=65, banknifty_lot=30
    ) == 30


def test_nifty_default():
    assert contract_multiplier_for_trade(
        strategy_id="strat-trendsnap-momentum", symbol="NIFTY24APR22400CE", nifty_lot=65, banknifty_lot=30
    ) == 65
