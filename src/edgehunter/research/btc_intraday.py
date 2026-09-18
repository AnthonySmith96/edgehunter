from __future__ import annotations

from dataclasses import asdict, dataclass

from edgehunter.research.btc_lag import robust_probability_bounds


@dataclass(frozen=True)
class IntradayObservation:
    slug: str
    epoch: int
    horizon_seconds: int
    open_price: float
    spot_price: float
    vol_per_second: float
    up_price: float
    down_price: float
    outcome_up: int
    up_price_timestamp: int | None = None
    down_price_timestamp: int | None = None
    effective_horizon_seconds: int | None = None


@dataclass(frozen=True)
class IntradaySpec:
    horizon_seconds: int
    min_edge: float
    max_ask: float
    vol_scale: float

    def to_dict(self) -> dict[str, float | int]:
        return asdict(self)


def simulate_trade(
    observation: IntradayObservation,
    spec: IntradaySpec,
    *,
    slippage: float = 0.02,
    fee_curve: float = 0.10,
    stake: float = 10.0,
) -> dict[str, float | int | str] | None:
    if observation.horizon_seconds != spec.horizon_seconds:
        return None
    prices = (observation.up_price, observation.down_price)
    if any(not 0 < price < 1 for price in prices) or prices[0] == prices[1]:
        return None
    side = "UP" if prices[0] > prices[1] else "DOWN"
    market_price = max(prices)
    if not 0.50 <= market_price <= spec.max_ask:
        return None
    probability_horizon = observation.effective_horizon_seconds or observation.horizon_seconds
    probability_low, probability_high = robust_probability_bounds(
        observation.spot_price,
        observation.open_price,
        observation.vol_per_second * spec.vol_scale,
        probability_horizon,
    )
    conservative_probability = probability_low if side == "UP" else 1 - probability_high
    entry = market_price + slippage
    if entry >= 1:
        return None
    fee = fee_curve * entry * (1 - entry)
    edge = conservative_probability - entry - fee
    if not spec.min_edge < edge <= 0.20:
        return None
    unit_cost = entry + fee
    shares = stake / unit_cost
    payout = observation.outcome_up if side == "UP" else 1 - observation.outcome_up
    pnl = shares * (payout - unit_cost)
    return {
        "slug": observation.slug,
        "epoch": observation.epoch,
        "side": side,
        "market_price": market_price,
        "entry_price": entry,
        "conservative_probability": conservative_probability,
        "edge": edge,
        "shares": shares,
        "payout": payout,
        "pnl": pnl,
    }


def evaluate_intraday(
    observations: list[IntradayObservation],
    spec: IntradaySpec,
    *,
    slippage: float = 0.02,
) -> dict[str, object]:
    trades = [trade for observation in observations
              if (trade := simulate_trade(observation, spec, slippage=slippage)) is not None]
    pnl = sum(float(trade["pnl"]) for trade in trades)
    return {
        "spec": spec.to_dict(),
        "slippage_per_share": slippage,
        "trades": len(trades),
        "wins": sum(float(trade["pnl"]) > 0 for trade in trades),
        "net_pnl": pnl,
        "return_on_stake": pnl / (10 * len(trades)) if trades else None,
        "trade_log": trades,
    }
