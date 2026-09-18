from decimal import Decimal as D

import pytest

from edgehunter.research.btc_lag import (
    choose_candidate,
    realized_vol_per_second,
    robust_probability_bounds,
)


def test_robust_probability_moves_with_spot_and_stays_bounded():
    low, high = robust_probability_bounds(101, 100, 0.0002, 60)
    assert 0.5 < low <= high < 1
    down_low, down_high = robust_probability_bounds(99, 100, 0.0002, 60)
    assert 0 < down_low <= down_high < 0.5


def test_realized_vol_requires_enough_causal_observations():
    closes = [100 + offset for offset in (0, 1, 0, 2, 1, 3, 2, 4, 3, 5)]
    assert realized_vol_per_second(closes) > 0
    with pytest.raises(ValueError):
        realized_vol_per_second([100] * 9)


def test_candidate_is_favorite_only_costed_and_bounded():
    decision = choose_candidate(D("0.60"), D("0.42"), 0.75, 0.80, tick=D("0.01"))
    assert decision is not None
    assert decision.side == "UP"
    assert decision.edge_per_share == D("0.11621")
    assert choose_candidate(D("0.49"), D("0.48"), 0.80, 0.85, tick=D("0.01")) is None
    assert choose_candidate(D("0.60"), D("0.60"), 0.80, 0.81, tick=D("0.01")) is None
