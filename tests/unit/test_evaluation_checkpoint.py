import json
from dataclasses import replace

import pytest

from edgehunter.research import learned_intraday, robust_intraday
from edgehunter.research.btc_intraday import IntradayObservation, IntradaySpec
from edgehunter.research.evaluation_checkpoint import DevelopmentCheckpoint
from edgehunter.research.learned_intraday import LearnedSpec
from edgehunter.research.robust_intraday import EvaluationConfig, evaluate_untouched_holdout


def test_cache_fingerprints_corruption_and_holdout_replay_guard(tmp_path):
    checkpoint = DevelopmentCheckpoint(tmp_path, {"hash": "A"}, 1)
    checkpoint.save_candidate(0, {"score": 1})
    assert DevelopmentCheckpoint(tmp_path, {"hash": "A"}, 1).load_candidate(0) == {"score": 1}
    with pytest.raises(ValueError, match="fingerprint"):
        DevelopmentCheckpoint(tmp_path, {"hash": "B"}, 1)
    file = tmp_path / "candidate_00000.json"
    raw = json.loads(file.read_text())
    raw["payload"]["record"]["score"] = 100
    file.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="corrupt"):
        checkpoint.load_candidate(0)
    checkpoint.begin_holdout()
    with pytest.raises(ValueError, match="already started"):
        checkpoint.load_final()
    with pytest.raises(ValueError, match="already started"):
        checkpoint.begin_holdout()
    checkpoint.save_final({"status": "DONE"})
    assert checkpoint.load_final() == {"status": "DONE"}


def rows():
    return [IntradayObservation(f"btc-{i}", 86400*(i+1), 60, 100, 100.3,
                                0.0002, 0.70, 0.30, 1) for i in range(40)]


def config():
    return EvaluationConfig(min_train_trades=2, min_validation_trades=2, min_test_trades=2,
                            min_train_days=2, min_validation_days=2, min_test_days=2,
                            bootstrap_draws=100, monte_carlo_runs=100, monte_carlo_horizon_trades=5)


def test_manual_interrupted_development_resumes_exactly_without_replaying_saved_candidate(tmp_path, monkeypatch):
    data = rows()
    specs = [IntradaySpec(60, 0.02, 0.8, 1), IntradaySpec(60, 0.03, 0.8, 1)]
    expected = evaluate_untouched_holdout(data, specs, config=config())

    def stop(message):
        if message == "development candidate 2/2":
            raise RuntimeError("simulated shutdown")

    with pytest.raises(RuntimeError, match="shutdown"):
        evaluate_untouched_holdout(data, specs, config=config(), checkpoint_dir=tmp_path, progress=stop)
    assert (tmp_path / "candidate_00000.json").exists()
    messages = []
    actual = evaluate_untouched_holdout(data, specs, config=config(), checkpoint_dir=tmp_path, progress=messages.append)
    assert actual == expected
    assert "restored development candidate 1/2" in messages
    monkeypatch.setattr(robust_intraday, "evaluate_spec", lambda *args, **kw: pytest.fail("replayed"))
    assert evaluate_untouched_holdout(data, specs, config=config(), checkpoint_dir=tmp_path) == expected


def test_learned_completed_result_restores_without_refit_or_reopened_holdout(tmp_path, monkeypatch):
    data = rows()
    specs = [LearnedSpec(60, 100, 0.02, 0.85)]
    # Model learns the constant positive label; this is a software fixture only.
    first = learned_intraday.learn_validate_holdout(data, specs, config=config(), checkpoint_dir=tmp_path)
    monkeypatch.setattr(learned_intraday, "fit_logistic", lambda *args, **kw: pytest.fail("refitted"))
    assert learned_intraday.learn_validate_holdout(data, specs, config=config(), checkpoint_dir=tmp_path) == first
    changed = [replace(r, outcome_up=0) for r in data]
    with pytest.raises(ValueError, match="fingerprint"):
        learned_intraday.learn_validate_holdout(changed, specs, config=config(), checkpoint_dir=tmp_path)
