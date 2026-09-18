"""Exploratory capital/cost stress; never selects models or opens a holdout."""
from __future__ import annotations

import heapq
import math
from dataclasses import dataclass
from typing import Any, Sequence

from edgehunter.research.btc_intraday import IntradayObservation
from edgehunter.research.learned_intraday import LearnedSpec, LogisticSnapshot, predict_up
from edgehunter.research.robust_intraday import CostScenario, summarize_trades


@dataclass(frozen=True)
class StakePolicy:
    name: str
    maximum_stake: float = 10.0
    kelly_fraction: float | None = None
    maximum_bankroll_fraction: float = 0.10

    def __post_init__(self) -> None:
        if not self.name or not math.isfinite(self.maximum_stake) or self.maximum_stake <= 0:
            raise ValueError("invalid stake policy")
        if not 0 < self.maximum_bankroll_fraction <= 1:
            raise ValueError("invalid bankroll fraction")
        if self.kelly_fraction is not None and not 0 < self.kelly_fraction <= 1:
            raise ValueError("invalid Kelly fraction")

    def budget(self, *, equity: float, probability: float, unit_cost: float) -> float:
        if not all(math.isfinite(v) for v in (equity, probability, unit_cost)):
            raise ValueError("non-finite sizing input")
        if equity < 0 or not 0 <= probability <= 1 or not 0 < unit_cost < 1:
            raise ValueError("invalid sizing input")
        if self.kelly_fraction is None:
            return self.maximum_stake
        fraction = self.kelly_fraction * max(0.0, (probability - unit_cost) / (1 - unit_cost))
        return min(self.maximum_stake, equity * min(fraction, self.maximum_bankroll_fraction))


def replay_capital(
    rows: Sequence[IntradayObservation], model: LogisticSnapshot, spec: LearnedSpec,
    costs: CostScenario, policy: StakePolicy, *, initial_capital: float = 100.0,
    settlement_delay_seconds: int = 300, minimum_shares: float = 5.0,
    bootstrap_draws: int = 2_000, seed: int = 20260918,
) -> dict[str, Any]:
    """Reserve actual stake until settlement; future outcomes only enter cash on release.

    All rows are historical diagnostics. Minimum order size, costs and settlement lag
    are declared sensitivities, not a claim that a historical order was executable.
    """
    if not math.isfinite(initial_capital) or initial_capital <= 0:
        raise ValueError("initial capital must be positive")
    if settlement_delay_seconds < 0 or not math.isfinite(minimum_shares) or minimum_shares < 0:
        raise ValueError("invalid execution sensitivity")
    if model.horizon_seconds != spec.horizon_seconds or model.ridge != spec.ridge:
        raise ValueError("model and specification mismatch")
    selected = sorted(
        (row for row in rows if row.horizon_seconds == spec.horizon_seconds),
        key=lambda row: (row.epoch, row.slug),
    )
    if len({row.slug for row in selected}) != len(selected):
        raise ValueError("duplicate market in replay")
    cash = initial_capital
    reserved = 0.0
    maximum_reserved = 0.0
    minimum_cash = cash
    # settlement time, sequence, reserved stake, returned payout
    pending: list[tuple[int, int, float, float]] = []
    signals = skipped_cash = skipped_size = 0
    trades: list[dict[str, float | int | str]] = []
    for row in selected:
        decision_at = row.epoch + 300 - row.horizon_seconds
        while pending and pending[0][0] <= decision_at:
            _, _, stake, proceeds = heapq.heappop(pending)
            cash += proceeds
            reserved -= stake
        probability_up = predict_up(model, row)
        candidates: list[tuple[float, str, float, float, int]] = []
        for side, observed, probability, payout in (
            ("UP", row.up_price, probability_up, row.outcome_up),
            ("DOWN", row.down_price, 1 - probability_up, 1 - row.outcome_up),
        ):
            entry = observed + costs.slippage
            if not 0 < entry <= spec.max_ask or entry >= 1:
                continue
            unit_cost = entry + costs.fee_curve * entry * (1 - entry)
            edge = probability - unit_cost
            if 0 < unit_cost < 1 and edge >= spec.min_edge:
                candidates.append((edge, side, unit_cost, probability, payout))
        if not candidates:
            continue
        signals += 1
        edge, side, unit_cost, probability, payout = max(candidates)
        budget = policy.budget(equity=cash + reserved, probability=probability, unit_cost=unit_cost)
        if budget > cash + 1e-9:
            skipped_cash += 1
            continue
        quantity = math.floor(min(budget, cash) / unit_cost * 1_000_000) / 1_000_000
        if quantity <= 0 or quantity < minimum_shares:
            skipped_size += 1
            continue
        cost = quantity * unit_cost
        proceeds = quantity * payout
        cash -= cost
        reserved += cost
        maximum_reserved = max(maximum_reserved, reserved)
        minimum_cash = min(minimum_cash, cash)
        settlement_at = row.epoch + 300 + settlement_delay_seconds
        heapq.heappush(pending, (settlement_at, len(trades), cost, proceeds))
        trades.append({
            "slug": row.slug, "epoch": row.epoch, "side": side, "stake": cost,
            "pnl": proceeds - cost, "probability": probability, "unit_cost": unit_cost,
            "edge": edge, "settlement_epoch": settlement_at,
        })
    while pending:
        _, _, cost, proceeds = heapq.heappop(pending)
        cash += proceeds
        reserved -= cost
    metrics = summarize_trades(
        trades, initial_capital=initial_capital, bootstrap_draws=bootstrap_draws, seed=seed,
        calendar_start=selected[0].epoch if selected else None,
        calendar_end=selected[-1].epoch if selected else None,
    )
    if abs(cash - float(metrics["final_capital"])) > 1e-7 or abs(reserved) > 1e-7:
        raise AssertionError("cash accounting mismatch")
    metrics.update({
        "signals": signals, "skipped_unfunded": skipped_cash, "skipped_minimum_size": skipped_size,
        "maximum_reserved": maximum_reserved, "minimum_available_cash": minimum_cash,
        "settlement_delay_seconds": settlement_delay_seconds,
        "minimum_shares_assumption": minimum_shares,
    })
    return {"metrics": metrics, "trade_log": trades}
