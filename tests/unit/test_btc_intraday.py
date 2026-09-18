from edgehunter.research.btc_intraday import (
    IntradayObservation,
    IntradaySpec,
    evaluate_intraday,
    simulate_trade,
)


def observation(**changes):
    values = {
        "slug": "btc-1",
        "epoch": 1,
        "horizon_seconds": 60,
        "open_price": 100.0,
        "spot_price": 100.3,
        "vol_per_second": 0.0002,
        "up_price": 0.70,
        "down_price": 0.30,
        "outcome_up": 1,
    }
    values.update(changes)
    return IntradayObservation(**values)


def test_intraday_trade_uses_market_favorite_and_costs():
    spec = IntradaySpec(60, 0.02, 0.80, 1.0)
    trade = simulate_trade(observation(), spec)
    assert trade is not None
    assert trade["side"] == "UP"
    assert trade["entry_price"] == 0.72
    assert trade["pnl"] > 0


def test_intraday_trade_rejects_horizon_underdog_and_extreme_edge():
    spec = IntradaySpec(60, 0.02, 0.80, 1.0)
    assert simulate_trade(observation(horizon_seconds=120), spec) is None
    assert simulate_trade(observation(up_price=0.40, down_price=0.60), spec) is None


def test_intraday_evaluation_preserves_losing_trade():
    spec = IntradaySpec(60, 0.02, 0.80, 1.0)
    result = evaluate_intraday([observation(outcome_up=0)], spec)
    assert result["trades"] == 1
    assert result["wins"] == 0
    assert result["net_pnl"] < 0
