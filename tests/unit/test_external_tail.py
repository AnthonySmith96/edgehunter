from decimal import Decimal as D

import pytest

from edgehunter.research.evaluation import HistoricalObservation
from edgehunter.research.external_tail import ExternalFavoriteTail


def row(category="politics", yes="0.91", no="0.09", outcome=1):
    return HistoricalObservation("e", "m", 100, 99, 200, D(yes), D(no), outcome, "day", False, category)


def test_external_tail_rule_is_fixed_and_excludes_sports():
    rule = ExternalFavoriteTail()
    assert rule.side(row()) == ("YES", D("0.91"), 1)
    assert rule.side(row(yes="0.08", no="0.92", outcome=0)) == ("NO", D("0.92"), 1)
    assert rule.side(row(category="sports")) is None
    assert rule.side(row(category="economics")) is None
    assert rule.side(row(yes="0.89", no="0.11")) is None
    assert rule.side(row(yes="0.99", no="0.01")) is None


def test_external_tail_rejects_parameter_drift():
    with pytest.raises(ValueError):
        ExternalFavoriteTail(categories=frozenset({"sports"}))
