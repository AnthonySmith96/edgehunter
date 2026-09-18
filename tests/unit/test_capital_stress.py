from dataclasses import replace

import pytest

from edgehunter.research.btc_intraday import IntradayObservation
from edgehunter.research.capital_stress import StakePolicy, replay_capital
from edgehunter.research.learned_intraday import LearnedSpec, LogisticSnapshot
from edgehunter.research.robust_intraday import CostScenario

MODEL = LogisticSnapshot(60, 100, (0, 0, 0, 0), (1, 1, 1, 1), (2, 0, 0, 0, 0), 100, 0)
SPEC = LearnedSpec(60, 100, 0.06, 0.85)
COSTS = CostScenario("test", 0, 0, 10)


def observation(index, outcome=1):
    return IntradayObservation(f"market-{index}", 86400 + index * 300, 60,
                               100, 100, 0.001, 0.5, 0.5, outcome)


def replay(rows, **kwargs):
    return replay_capital(rows, MODEL, SPEC, COSTS, StakePolicy("fixed10"),
                          initial_capital=10, bootstrap_draws=100, **kwargs)


def test_unsettled_winnings_cannot_finance_the_next_order():
    result = replay([observation(0), observation(1)], settlement_delay_seconds=600)
    assert result["metrics"]["trades"] == 1
    assert result["metrics"]["skipped_unfunded"] == 1
    assert result["metrics"]["final_capital"] == 20
    assert result["metrics"]["minimum_available_cash"] == 0


def test_settlement_releases_cash_and_loss_stops_unfunded_orders():
    won = replay([observation(0), observation(1)], settlement_delay_seconds=0)
    lost = replay([observation(0, 0), observation(1)], settlement_delay_seconds=0)
    assert won["metrics"]["trades"] == 2
    assert lost["metrics"]["trades"] == 1
    assert lost["metrics"]["final_capital"] == 0


def test_changing_pending_outcome_cannot_change_cash_or_intermediate_entries():
    rows = [observation(0), observation(1), observation(2)]
    a = replay(rows, settlement_delay_seconds=3600)
    b = replay([replace(r, outcome_up=0) for r in rows], settlement_delay_seconds=3600)
    assert [t["slug"] for t in a["trade_log"]] == [t["slug"] for t in b["trade_log"]]
    assert a["metrics"]["minimum_available_cash"] == b["metrics"]["minimum_available_cash"]


def test_fractional_sizing_is_bounded_and_never_forces_minimum_order():
    policy = StakePolicy("quarter", kelly_fraction=0.25)
    assert policy.budget(equity=100, probability=0.60, unit_cost=0.55) == pytest.approx(2.77777778)
    assert policy.budget(equity=100, probability=0.99, unit_cost=0.01) == 10
    assert policy.budget(equity=100, probability=0.4, unit_cost=0.5) == 0
    result = replay_capital([observation(0)], MODEL, SPEC, COSTS, policy,
                            initial_capital=1, bootstrap_draws=100)
    assert result["metrics"]["trades"] == 0
    assert result["metrics"]["skipped_minimum_size"] == 1


def test_replay_rejects_duplicate_market():
    with pytest.raises(ValueError, match="duplicate"):
        replay([observation(0), observation(0)])


def test_more_expensive_execution_can_eliminate_signal():
    result = replay_capital([observation(0)], MODEL, SPEC,
                            CostScenario("stress", 0.35, 0.1, 10), StakePolicy("ten"),
                            bootstrap_draws=100)
    assert result["metrics"]["trades"] == 0
