from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path
from runpy import run_path

import pytest

from edgehunter.research.calibrated import CalibratedFavorite, CalibrationSpec
from edgehunter.research.evaluation import HistoricalObservation, evaluate


def row(event="one", decision=1, settlement=2, yes=".7", outcome=1, category="sports"):
    return HistoricalObservation(event, "market" + event, decision, decision - 1, settlement, D(yes), 1 - D(yes), outcome, event, False, category)


def test_residual_fit_rejects_future_labels_and_duplicate_events():
    with pytest.raises(ValueError, match="future"):
        CalibratedFavorite.fit([row()], CalibrationSpec(10, D(0), False), 2)
    with pytest.raises(ValueError, match="duplicate"):
        CalibratedFavorite.fit([row(), row()], CalibrationSpec(10, D(0), False), 3)


def test_probability_uses_market_prior_and_learns_only_past_labels():
    empty = CalibratedFavorite.fit([], CalibrationSpec(10, D(0), False), 3)
    assert empty.estimate_yes(D(".73"), "sports", 4) == (D(".73"), D(".73"), D(".73"))
    learned = CalibratedFavorite.fit([row()], CalibrationSpec(10, D(0), False), 3)
    expected = D(".7") + D(".3") / 11
    assert learned.estimate_yes(D(".7"), "sports", 4)[0] == expected
    assert learned.estimate_yes(D(".3"), "sports", 4)[0] == 1 - expected
    with pytest.raises(ValueError, match="cutoff"):
        learned.estimate_yes(D(".7"), "sports", 3)


def test_future_outcomes_do_not_change_trade_selection_or_forecast():
    model = CalibratedFavorite.fit([row(str(i)) for i in range(100)], CalibrationSpec(10, D(".5"), True), 3)
    future = row("future", 4, 5)
    changed = replace(future, outcome_yes=0)
    assert model.forecast(future) == model.forecast(changed)
    assert model.side(future)[:2] == model.side(changed)[:2]
    assert D(evaluate([future], model)["net_pnl"]) > 0
    assert D(evaluate([changed], model)["net_pnl"]) < 0


def test_uncertainty_penalty_and_costs_prevent_untrained_trades():
    model = CalibratedFavorite.fit([], CalibrationSpec(10, D(1), True), 3)
    assert model.side(row("new", 4, 5)) is None
    mean, lower, upper = model.estimate_yes(D(".3"), "unseen", 4)
    assert lower < mean < upper


def test_snapshot_roundtrip_preserves_coefficients_and_refuses_corruption():
    model = CalibratedFavorite.fit([row()], CalibrationSpec(30, D(".5"), True), 3)
    snapshot = model.snapshot()
    restored = CalibratedFavorite.from_snapshot(snapshot)
    assert restored.snapshot() == snapshot
    assert restored.estimate_yes(D(".7"), "sports", 4) == model.estimate_yes(D(".7"), "sports", 4)
    snapshot["statistics"]["global:7"] = [1, "NaN"]
    with pytest.raises(ValueError, match="statistics"):
        CalibratedFavorite.from_snapshot(snapshot)


@pytest.mark.parametrize("z", [D("NaN"), D("Infinity"), D(-1)])
def test_invalid_calibration_uncertainty_is_rejected(z):
    with pytest.raises(ValueError):
        CalibratedFavorite.fit([], CalibrationSpec(10, z, False), 3)


def test_frozen_experiment_rejects_changed_model_or_dataset(tmp_path):
    namespace = run_path(str(Path(__file__).parents[2] / "scripts" / "research_calibrated.py"))
    freeze = namespace["freeze"]
    path = tmp_path / "frozen.json"
    freeze(path, {"model": "one", "coefficient": D(".01")})
    freeze(path, {"coefficient": D(".01"), "model": "one"})
    with pytest.raises(ValueError, match="immutable"):
        freeze(path, {"model": "one", "coefficient": D(".02")})
