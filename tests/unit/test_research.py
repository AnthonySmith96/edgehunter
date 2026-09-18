from dataclasses import replace
from decimal import Decimal as D

import pytest

from edgehunter.research.evaluation import (
    BinCalibrator,
    HistoricalObservation,
    Hypothesis,
    calibration_metrics,
    cluster_bootstrap,
    evaluate,
    freeze_holdout,
    temporal_split,
    walk_forward,
)


def observation(event="e", decision=100, settle=200, price=".6", outcome=1, cluster=None):
    return HistoricalObservation(event, "m-" + event, decision, decision - 1, settle, D(price), 1 - D(price), outcome, cluster or event)


def test_temporal_price_and_settlement_reject_future_or_resolved_decisions():
    row = observation()
    with pytest.raises(ValueError, match="temporal"):
        replace(row, price_available_at=101)
    with pytest.raises(ValueError, match="temporal"):
        replace(row, settlement_at=100)


def test_purge_label_overlap_and_shared_clusters():
    rows = [observation("a", 10, 20), observation("b", 90, 110), observation("c", 110, 120), observation("d", 210, 220)]
    result = temporal_split(rows, 100, 200, embargo_seconds=10)
    assert [r.event_id for r in result["train"]] == ["a"]
    assert [r.event_id for r in result["validation"]] == ["c"]
    assert [r.event_id for r in result["purged"]] == ["b"]
    rows = [replace(rows[0], cluster_id="same"), rows[1], rows[2], replace(rows[3], cluster_id="same")]
    result = temporal_split(rows, 100, 200, embargo_seconds=0)
    assert not result["train"] and not result["test"]


def test_duplicate_event_is_never_independent_sample():
    with pytest.raises(ValueError, match="duplicate"):
        temporal_split([observation(), observation()], 300, 400)
    with pytest.raises(ValueError, match="duplicate"):
        evaluate([observation(), observation()], Hypothesis("x", "favorite", D(".5")))


def test_virtual_ledger_costs_lock_and_release_without_double_spend():
    rows = [observation(str(i), 100, 200) for i in range(50)]
    result = evaluate(rows, Hypothesis("favorite", "favorite", D(".5")))
    assert D(result["max_locked"]) <= 100
    assert result["trades"] == 10
    assert result["ignored"]["minimum_or_capital_limit"] == 40
    assert D(result["final_capital"]) == 1000 + sum(D(t["pnl"]) for t in result["trade_log"])
    assert D(result["fees"]) > 0
    assert result["classification"] == "EXPLORATORY_HISTORICAL_SIMULATION_NOT_EXECUTABLE_PROOF"


def test_losing_fixture_really_loses_and_risk_is_cash_limited():
    rows = [observation(str(i), i * 100 + 1, i * 100 + 2, outcome=0) for i in range(40)]
    result = evaluate(rows, Hypothesis("favorite", "favorite", D(".5")), initial_capital=D(100), max_locked=D(100))
    assert 0 <= D(result["final_capital"]) < 10
    assert D(result["net_pnl"]) < 0
    assert D(result["max_drawdown_settled_cost_basis"]) > 90


def test_more_costs_cannot_improve_return_for_identical_single_trade():
    rows = [observation()]
    hypothesis = Hypothesis("favorite", "favorite", D(".5"))
    cheap = evaluate(rows, hypothesis, slippage_per_share=D(0), fee_rate=D(0))
    expensive = evaluate(rows, hypothesis, slippage_per_share=D(".03"), fee_rate=D(".10"))
    assert D(expensive["net_pnl"]) < D(cheap["net_pnl"])


@pytest.mark.parametrize("parameter,value", [("fee_rate", D(-1)), ("initial_capital", D("NaN")), ("max_locked", D(2000)), ("risk_per_event", D(101))])
def test_simulator_rejects_invalid_risk_parameters(parameter, value):
    with pytest.raises(ValueError):
        evaluate([observation()], Hypothesis("favorite", "favorite", D(".5")), **{parameter: value})


def test_calibration_cannot_train_or_predict_backwards():
    rows = [observation()]
    with pytest.raises(ValueError, match="future"):
        BinCalibrator.fit(rows, 200)
    calibrator = BinCalibrator.fit(rows, 201)
    with pytest.raises(ValueError):
        calibrator.predict(D(".6"), 200)
    assert 0 < calibrator.predict(D(".6"), 300) < 1


def test_calibration_metrics_and_bootstrap_are_reproducible():
    metrics = calibration_metrics([.8, .2], [1, 0])
    assert metrics["brier"] == pytest.approx(.04)
    pnls = [("same-day", 10.0), ("same-day", -5.0), ("next-day", -20.0)]
    result = cluster_bootstrap(pnls)
    assert result == cluster_bootstrap(pnls)
    assert result["clusters"] == 2 and result["ci95"][0] < 0


def test_holdout_refuses_new_data_or_same_name_with_different_parameter(tmp_path):
    path = tmp_path / "holdout.json"
    hypothesis = Hypothesis("x", "favorite", D(".6"))
    freeze_holdout(path, "dataset-one", hypothesis)
    freeze_holdout(path, "dataset-one", hypothesis)
    with pytest.raises(ValueError):
        freeze_holdout(path, "dataset-two", hypothesis)
    with pytest.raises(ValueError):
        freeze_holdout(path, "dataset-one", replace(hypothesis, threshold=D(".7")))


def test_walk_forward_selects_only_mature_past_labels():
    train = [observation(str(i), 1, 2) for i in range(12)]
    future = [observation("future" + str(i), 5, 150, outcome=0) for i in range(12)]
    test = [observation("test", 100, 110)]
    result = walk_forward(train + future + test, [Hypothesis("f", "favorite", D(".5"))], [100, 120], embargo_seconds=0)
    fold = result["folds"][0]
    assert fold["train_events"] == 12
    assert fold["evaluation_events"] == 1
    assert fold["evaluation"]["trades"] == 1


def test_walk_forward_excludes_shared_clusters():
    train = [observation(str(i), 1, 2, cluster="same") for i in range(12)]
    result = walk_forward(train + [observation("test", 100, 110, cluster="same")], [Hypothesis("f", "favorite", D(".5"))], [100, 120], embargo_seconds=0)
    assert result["folds"][0]["train_events"] == 0
    assert result["folds"][0]["status"] == "INSUFFICIENT_DATA"
