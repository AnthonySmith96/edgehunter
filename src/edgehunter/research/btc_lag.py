from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from typing import Iterable

D = Decimal


@dataclass(frozen=True)
class BtcLagDecision:
    side: str
    ask: Decimal
    conservative_probability: Decimal
    fee_per_share: Decimal
    edge_per_share: Decimal


def _normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


@lru_cache(maxsize=300_000)
def robust_probability_bounds(
    spot: float,
    open_price: float,
    vol_per_second: float,
    seconds_remaining: float,
    *,
    betas: tuple[float, ...] = (0.83, 1.36),
    tail_weight: float = 0.25,
    tail_scale: float = 2.5,
) -> tuple[float, float]:
    """Return low/high P(Up) across fixed response regimes.

    This is an independent, small implementation of the public model description
    registered for the paper experiment. It intentionally has no fitted parameters.
    """
    values = (spot, open_price, vol_per_second, seconds_remaining, tail_weight, tail_scale, *betas)
    if not all(math.isfinite(value) for value in values):
        raise ValueError("model inputs must be finite")
    if spot <= 0 or open_price <= 0 or vol_per_second <= 0 or seconds_remaining <= 0:
        raise ValueError("prices, volatility and remaining time must be positive")
    if not betas or any(beta <= 0 for beta in betas):
        raise ValueError("at least one positive beta is required")
    if not 0 <= tail_weight <= 1 or tail_scale < 1:
        raise ValueError("invalid tail mixture")
    distance = math.log(spot / open_price) / (vol_per_second * math.sqrt(seconds_remaining))
    probabilities = [
        (1 - tail_weight) * _normal_cdf(beta * distance)
        + tail_weight * _normal_cdf(beta * distance / tail_scale)
        for beta in betas
    ]
    return min(probabilities), max(probabilities)


def realized_vol_per_second(closes: Iterable[float], *, floor: float = 0.000001) -> float:
    values = list(closes)
    if len(values) < 10 or any(not math.isfinite(value) or value <= 0 for value in values):
        raise ValueError("at least ten positive finite closes are required")
    returns = [math.log(current / previous) for previous, current in zip(values, values[1:])]
    return max(statistics.stdev(returns) / math.sqrt(60.0), floor)


def choose_candidate(
    up_ask: Decimal,
    down_ask: Decimal,
    probability_low: float,
    probability_high: float,
    *,
    tick: Decimal,
    min_ask: Decimal = D("0.50"),
    max_ask: Decimal = D("0.80"),
    min_edge: Decimal = D("0.10"),
    max_edge: Decimal = D("0.20"),
    fee_curve: Decimal = D("0.10"),
) -> BtcLagDecision | None:
    if not D(0) < up_ask < D(1) or not D(0) < down_ask < D(1) or tick <= 0:
        raise ValueError("invalid book")
    if not 0 <= probability_low <= probability_high <= 1:
        raise ValueError("invalid probability bounds")
    if up_ask == down_ask:
        return None
    side = "UP" if up_ask > down_ask else "DOWN"
    ask = up_ask if side == "UP" else down_ask
    if not min_ask <= ask <= max_ask:
        return None
    probability = D(str(probability_low if side == "UP" else 1 - probability_high))
    modeled_price = ask + tick
    if modeled_price >= 1:
        return None
    fee = fee_curve * modeled_price * (1 - modeled_price)
    edge = probability - modeled_price - fee
    if not min_edge < edge <= max_edge:
        return None
    return BtcLagDecision(side, ask, probability, fee, edge)
