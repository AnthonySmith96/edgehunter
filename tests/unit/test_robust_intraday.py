from dataclasses import replace

import pytest

from edgehunter.research.btc_intraday import IntradayObservation, IntradaySpec
from edgehunter.research.robust_intraday import (
    CostScenario,
    EvaluationConfig,
    chronological_split,
    evaluate_spec,
    evaluate_untouched_holdout,
    monte_carlo_risk_of_ruin,
    summarize_trades,
)


def observation(epoch: int, *, horizon: int = 60, outcome: int = 1) -> IntradayObservation:
    return IntradayObservation(
        slug=f"btc-{epoch}-{horizon}",
        epoch=epoch,
        horizon_seconds=horizon,
        open_price=100.0,
        spot_price=100.3,
        vol_per_second=0.0002,
        up_price=0.70,
        down_price=0.30,
        outcome_up=outcome,
    )


def test_split_is_chronological_and_keeps_same_epoch_together():
    rows = [observation(epoch, horizon=horizon) for epoch in range(10) for horizon in (60, 120)]
    split = chronological_split(list(reversed(rows)), train_fraction=0.6, validation_fraction=0.2)
    assert {row.epoch for row in split.train} == set(range(6))
    assert {row.epoch for row in split.validation} == {6, 7}
    assert {row.epoch for row in split.test} == {8, 9}
    assert max(row.epoch for row in split.train) < min(row.epoch for row in split.validation)
    assert max(row.epoch for row in split.validation) < min(row.epoch for row in split.test)


def test_fixed_holdout_cutoff_is_preserved_when_more_history_is_added():
    rows = [observation(epoch * 300) for epoch in range(20)]
    cutoff = 15 * 300
    split = chronological_split(
        rows, train_fraction=0.6, validation_fraction=0.2,
        fixed_holdout_start_epoch=cutoff,
    )
    assert min(row.epoch for row in split.test) == cutoff
    assert all(row.epoch < cutoff for row in (*split.train, *split.validation))


def test_selection_does_not_change_when_untouched_test_outcomes_change():
    rows: list[IntradayObservation] = []
    for index in range(30):
        epoch = index * 86_400
        development = index < 24
        rows.append(observation(epoch, horizon=60, outcome=1 if development else 0))
        rows.append(observation(epoch, horizon=120, outcome=0 if development else 1))
    specs = [IntradaySpec(60, 0.02, 0.80, 1.0), IntradaySpec(120, 0.02, 0.80, 1.0)]
    config = EvaluationConfig(min_train_trades=5, min_validation_trades=3, bootstrap_draws=100,
                              monte_carlo_runs=100, monte_carlo_horizon_trades=20)
    result = evaluate_untouched_holdout(rows, specs, config=config)
    changed = [
        replace(row, outcome_up=1 - row.outcome_up) if row.epoch >= 24 * 86_400 else row
        for row in rows
    ]
    rerun = evaluate_untouched_holdout(changed, specs, config=config)
    assert result["selected_spec"] == specs[0].to_dict()
    assert rerun["selected_spec"] == result["selected_spec"]
    assert result["split"]["test_was_used_for_selection"] is False
    assert result["test"] != rerun["test"]


def test_failed_development_gate_does_not_open_test():
    rows = [observation(epoch * 86_400, outcome=0) for epoch in range(20)]
    config = EvaluationConfig(min_train_trades=2, min_validation_trades=2, bootstrap_draws=100,
                              monte_carlo_runs=100, monte_carlo_horizon_trades=10)
    result = evaluate_untouched_holdout(rows, [IntradaySpec(60, 0.02, 0.80, 1.0)], config=config)
    assert result["status"] == "NO_CANDIDATE_PASSED_DEVELOPMENT_GATE"
    assert result["test"] is None
    assert result["monte_carlo"] is None


def test_conservative_costs_cannot_improve_same_trades():
    rows = [observation(epoch) for epoch in range(20)]
    spec = IntradaySpec(60, 0.02, 0.80, 1.0)
    cheap = evaluate_spec(rows, spec, CostScenario("base", 0.0, 0.0, 10.0),
                          initial_capital=100, bootstrap_draws=100, seed=1)
    expensive = evaluate_spec(rows, spec, CostScenario("stress", 0.03, 0.10, 10.0),
                              initial_capital=100, bootstrap_draws=100, seed=1)
    assert expensive["metrics"]["net_pnl"] < cheap["metrics"]["net_pnl"]


def test_backtest_stops_when_fixed_stake_can_no_longer_be_funded():
    rows = [observation(epoch * 300, outcome=0) for epoch in range(20)]
    result = evaluate_spec(
        rows, IntradaySpec(60, 0.02, 0.80, 1.0),
        CostScenario("conservative", 0.03, 0.10, 10.0),
        initial_capital=20, bootstrap_draws=100, seed=1,
    )
    assert result["metrics"]["trades"] == 2
    assert result["metrics"]["ruined_at_epoch"] is not None
    assert result["metrics"]["skipped_insufficient_capital"] > 0
    assert result["metrics"]["final_capital"] == pytest.approx(0.0)


def test_metrics_include_drawdown_and_reproducible_cluster_bootstrap():
    trades = [
        {"epoch": 0, "slug": "a", "pnl": 10.0, "stake": 10.0},
        {"epoch": 86_400, "slug": "b", "pnl": -15.0, "stake": 10.0},
        {"epoch": 172_800, "slug": "c", "pnl": 5.0, "stake": 10.0},
    ]
    first = summarize_trades(trades, initial_capital=100, bootstrap_draws=200, seed=7)
    second = summarize_trades(trades, initial_capital=100, bootstrap_draws=200, seed=7)
    assert first == second
    assert first["win_rate"] == pytest.approx(2 / 3)
    assert first["max_drawdown"] == pytest.approx(15.0)
    assert first["bootstrap"]["clusters"] == 3
    assert first["bootstrap"]["net_pnl_ci95"] is not None


def test_monte_carlo_reports_certain_ruin_and_survival_extremes():
    losing = monte_carlo_risk_of_ruin([-10.0], initial_capital=100, minimum_operating_capital=10,
                                      runs=200, horizon_trades=20, seed=3)
    winning = monte_carlo_risk_of_ruin([1.0], initial_capital=100, minimum_operating_capital=10,
                                       runs=200, horizon_trades=20, seed=3)
    assert losing["risk_of_ruin"] == 1.0
    assert winning["risk_of_ruin"] == 0.0
    assert losing["terminal_capital_median"] < winning["terminal_capital_median"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"initial_capital": 0, "minimum_operating_capital": 10},
        {"initial_capital": 100, "minimum_operating_capital": float("nan")},
    ],
)
def test_monte_carlo_rejects_invalid_capital(kwargs):
    with pytest.raises(ValueError):
        monte_carlo_risk_of_ruin([1.0], runs=100, horizon_trades=10, seed=1, **kwargs)
