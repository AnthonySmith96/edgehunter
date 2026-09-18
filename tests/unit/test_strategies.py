from dataclasses import replace
from decimal import Decimal as D

import pytest

from edgehunter.strategies import Quote, StrategyContext, StrategyRegistry, taker_fee


def quote(token="yes", bid="0.44", ask="0.45", **changes):
    return replace(Quote(token, D(bid), D(ask), D("100"), D("80"), 990, D("0.02")), **changes)


def context(features=None, quotes=None, **changes):
    return replace(StrategyContext(1000, "market", "event", tuple(quotes or [quote()]), features or {}, ("source-hash",), 995), **changes)


def forecast_features():
    return {"calibration_validated": True, "calibrator_id": "train-only-v1", "training_labels_available_at": 900, "forecast_issued_at": 950, "p_lower": ".55", "p_calibrated": ".65", "p_upper": ".75", "adverse_selection_per_share": ".01"}


def structural_features():
    return {"contracts_verified": True, "exhaustive_scenarios": True, "payoff_matrix": [[1, 0], [0, 1]], "conversion_cost_per_share": ".001", "unwind_loss_per_share": ".03", "adverse_selection_per_share": ".002", "atomic_execution_verified": True}


@pytest.mark.parametrize("name", StrategyRegistry.names)
def test_every_plugin_abstains_on_unknown_future_or_stale_availability(name):
    registry = StrategyRegistry()
    for available in (None, 1001, 900):
        assert registry.evaluate(name, context(available_at=available)).action == "ABSTAIN"


@pytest.mark.parametrize("name", StrategyRegistry.names)
def test_missing_dependency_cannot_crash_strategy_host(name):
    result = StrategyRegistry().evaluate(name, context(quotes=[], features={}))
    assert result.action == "ABSTAIN"
    result = StrategyRegistry().evaluate(name, replace(context(), quotes=()))
    assert result.action == "ABSTAIN"


def test_registry_cannot_promote_live_and_lists_seven_families():
    health = StrategyRegistry().health()
    assert len(health) == 7
    assert all("LIVE" not in item["allowed_modes"] and not item["automatic_live_promotion"] for item in health)


def test_fee_formula_rejects_nonfinite_and_negative_inputs():
    assert taker_fee(D(100), D(".5"), D(".07")) == D("1.75")
    for bad in (D("NaN"), D("Infinity"), D(-1)):
        with pytest.raises(ValueError):
            taker_fee(bad, D(".5"), D(".07"))


def test_quote_validation():
    with pytest.raises(ValueError):
        quote(bid=".6", ask=".5")
    with pytest.raises(ValueError):
        quote(min_size=D(0))


def test_structural_covers_every_scenario_and_charges_both_legs():
    result = StrategyRegistry().evaluate("structural", context(structural_features(), [quote(), quote("no", ".46", ".47")]))
    expected = D(1) - D(".45") - D(".47") - taker_fee(D(1), D(".45"), D(".02")) - taker_fee(D(1), D(".47"), D(".02")) - D(".003")
    assert result.expected_edge == expected
    assert len(result.legs) == 2
    assert result.expires_at == 1050  # Oldest source, not evaluation time + TTL.


def test_structural_partial_leg_risk_requires_verified_unwind():
    f = structural_features() | {"atomic_execution_verified": False}
    c = context(f, [quote(), quote("no")])
    assert StrategyRegistry().evaluate("structural", c).reason == "NONATOMIC_NO_ACCEPTABLE_UNWIND"
    result = StrategyRegistry().evaluate("structural", replace(c, features=f | {"unwind_route_verified": True}))
    assert result.action == "PROPOSE"
    assert result.estimates["partial_leg_loss_bound"] == "3.00"


def test_structural_abstains_when_invalid_outcome_destroys_guarantee():
    f = structural_features() | {"payoff_matrix": [[1, 0], [0, 1], [0, 0]]}
    assert StrategyRegistry().evaluate("structural", context(f, [quote(), quote("no")])).action == "ABSTAIN"


def test_structural_unknown_fees_and_negative_cost_block():
    c = context(structural_features(), [quote(fee_rate=None), quote("no")])
    assert StrategyRegistry().evaluate("structural", c).reason == "UNKNOWN_FEES"
    assert StrategyRegistry().evaluate("structural", context(structural_features() | {"unwind_loss_per_share": "-.1"}, [quote(), quote("no")])).reason == "NEGATIVE_COST"


def test_market_making_inventory_post_only_cost_and_cooldown():
    f = {"post_only_supported": True, "fill_model_validated": True, "inventory": "20", "inventory_cap": "100", "half_spread": ".04", "adverse_selection": ".002", "fair_price": ".5", "fill_probability": ".2", "maker_fee_rate": "0", "last_quote_at": 900}
    c = context(f, [quote(bid=".47", ask=".53")])
    r = StrategyRegistry().evaluate("market_making", c)
    assert r.action == "PROPOSE" and all(leg["post_only"] for leg in r.legs)
    assert all(D(leg["quantity"]) <= D(20) for leg in r.legs if leg["side"] == "SELL")
    assert StrategyRegistry().evaluate("market_making", replace(c, features=f | {"last_quote_at": 999})).reason == "REPRICE_COOLDOWN"
    assert StrategyRegistry().evaluate("market_making", replace(c, features=f | {"maker_fee_rate": "1"})).action == "ABSTAIN"
    assert StrategyRegistry().evaluate("market_making", replace(c, features=f | {"fill_probability": "0"})).action == "ABSTAIN"


def test_wallet_cashflows_do_not_count_deposits_as_profit_and_need_forward_evidence():
    f = {"cashflows": [{"kind": "DEPOSIT", "amount": "1000", "available_at": 600}, {"kind": "BUY", "amount": "50", "available_at": 650}, {"kind": "REDEEM", "amount": "60", "available_at": 700}, {"kind": "FEE", "amount": "1", "available_at": 700}], "selection_cutoff": 800, "selected_at": 900, "cashflows_complete": True, "discarded_cohort_recorded": True, "open_position_liquidation_value": "0"}
    result = StrategyRegistry().evaluate("wallet_intelligence", context(f))
    assert result.execution == "MANUAL" and result.estimates["reconstructed_pnl"] == "9"
    assert result.expected_edge is None and result.max_size == 0
    assert StrategyRegistry().evaluate("wallet_intelligence", context(f | {"selection_cutoff": 1000})).reason == "WALLET_SELECTION_LEAKAGE"


def test_weather_needs_issued_forecast_contract_mapping_and_calibration():
    f = {"contract_station_matched": True, "forecast_target_matched": True, "forecast_available_at": 900, "members": [20, 21, 21, 22], "received_members": 4, "expected_members": 50, "target_low": 20, "target_high": 22}
    result = StrategyRegistry().evaluate("weather", context(f))
    assert result.reason == "RAW_ENSEMBLE_NOT_CALIBRATED"
    assert result.estimates["raw_frequency"] == "0.75"
    assert result.estimates["independence_assumed"] is False
    assert StrategyRegistry().evaluate("weather", context(f | forecast_features())).action == "PROPOSE"
    assert StrategyRegistry().evaluate("weather", context(f | {"forecast_available_at": 1001})).reason == "FUTURE_FORECAST_RUN"


def test_news_requires_primary_identity_and_cannot_treat_score_as_probability():
    f = {"entity_id_verified": True, "primary_source_verified": True, "published_at": 900, "first_seen_at": 990, "price_before": 100, "price_after": 110}
    result = StrategyRegistry().evaluate("news_cross_market", context(f))
    assert result.execution == "MANUAL" and result.expected_edge is None
    assert result.estimates["observed_move"] == "0.1"
    assert StrategyRegistry().evaluate("news_cross_market", context(f | {"recycled": True})).action == "ABSTAIN"
    assert StrategyRegistry().evaluate("news_cross_market", context(f | forecast_features())).action == "PROPOSE"


def test_forecasting_uses_lower_interval_after_costs_and_past_labels():
    f = forecast_features()
    result = StrategyRegistry().evaluate("forecasting", context(f))
    assert result.expected_edge == D(".55") - D(".45") - D(".01") - D(".02") * D(".45") * D(".55")
    assert result.estimates["conservative_lower_used"]
    assert StrategyRegistry().evaluate("forecasting", context(f | {"p_lower": ".44"})).action == "ABSTAIN"
    assert StrategyRegistry().evaluate("forecasting", context(f | {"training_labels_available_at": 960})).reason == "FORECAST_TEMPORAL_LEAKAGE"


def test_radar_reports_joint_anomaly_only_as_manual_observation():
    f = {"seasonality_adjusted": True, "corporate_actions_adjusted": True, "historical_volume": [90, 100, 110] * 10, "current_volume": 1000, "primary_catalyst": True, "available_exit_liquidity": 1000}
    result = StrategyRegistry().evaluate("radar", context(f))
    assert result.action == "PROPOSE" and result.execution == "MANUAL"
    assert result.estimates["score_is_probability"] is False
    assert result.expected_edge is None
    assert StrategyRegistry().evaluate("radar", context(f | {"manipulation_flags": ["honeypot"]})).action == "ABSTAIN"
