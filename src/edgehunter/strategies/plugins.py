"""Seven strategy families with explicit evidence, estimates and abstention.

All proposal money uses Decimal. Calculations do not imply source coverage or
permission to trade. Contexts are immutable read-only inputs from the host.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from math import isfinite
from statistics import mean, pstdev
from typing import Any, Callable, Mapping, Sequence

D = Decimal
ZERO = D("0")
ONE = D("1")


def dec(value: Any) -> Decimal:
    result = D(str(value))
    if not result.is_finite():
        raise ValueError("non-finite decimal")
    return result


def taker_fee(shares: Decimal, price: Decimal, fee_rate: Decimal) -> Decimal:
    """Current documented formula; caller must supply verified market fee rate."""
    if not all(x.is_finite() for x in (shares, price, fee_rate)):
        raise ValueError("non-finite fee input")
    if shares < 0 or not 0 <= price <= 1 or fee_rate < 0:
        raise ValueError("invalid fee input")
    return shares * fee_rate * price * (ONE - price)


@dataclass(frozen=True)
class Quote:
    token: str
    bid: Decimal
    ask: Decimal
    ask_size: Decimal
    bid_size: Decimal
    observed_at: int
    fee_rate: Decimal | None
    tick: Decimal = D("0.01")
    min_size: Decimal = D("5")

    def __post_init__(self) -> None:
        vals = (self.bid, self.ask, self.ask_size, self.bid_size, self.tick, self.min_size)
        if not all(v.is_finite() for v in vals):
            raise ValueError("non-finite quote")
        if not (0 <= self.bid <= self.ask <= 1):
            raise ValueError("crossed or invalid quote")
        if min(self.ask_size, self.bid_size) < 0 or self.min_size <= 0 or self.tick <= 0:
            raise ValueError("invalid size or tick")
        if self.fee_rate is not None and (not self.fee_rate.is_finite() or self.fee_rate < 0):
            raise ValueError("invalid fee")


@dataclass(frozen=True)
class StrategyContext:
    now: int
    market_id: str
    event_id: str
    quotes: tuple[Quote, ...] = ()
    features: Mapping[str, Any] = field(default_factory=dict)
    evidence_ids: tuple[str, ...] = ()
    available_at: int | None = None
    ttl_seconds: int = 60

    def __post_init__(self) -> None:
        if self.ttl_seconds <= 0 or self.now < 0:
            raise ValueError("invalid clock or freshness budget")


@dataclass(frozen=True)
class StrategyResult:
    strategy: str
    action: str
    reason: str
    expected_edge: Decimal | None = None
    max_size: Decimal = ZERO
    execution: str = "UNAVAILABLE"
    evidence_ids: tuple[str, ...] = ()
    estimates: Mapping[str, Any] = field(default_factory=dict)
    legs: tuple[Mapping[str, Any], ...] = ()
    expires_at: int | None = None
    version: str = "1.0.0"


class StrategyRegistry:
    """No strategy can authorize itself. All results need host risk checks."""

    names = ("structural", "market_making", "wallet_intelligence", "weather", "news_cross_market", "forecasting", "radar")

    def health(self) -> list[dict[str, Any]]:
        inputs = {
            "structural": "verified scenario payoff matrix + fees + atomic/unwind route",
            "market_making": "fresh book + markout/fill model + inventory + post-only support",
            "wallet_intelligence": "point-in-time cashflow-complete candidate cohort + prospective validation",
            "weather": "exact station contract + historical issued forecast + calibrated mapping",
            "news_cross_market": "primary entity IDs + timestamped catalyst + calibrated effect",
            "forecasting": "temporally validated forecast + interval + fresh book",
            "radar": "seasonally matched historical activity + price/liquidity + catalyst",
        }
        return [{"strategy": name, "version": "1.0.0", "state": "IMPLEMENTED", "runtime_state": "DATA_BLOCKED", "inputs": inputs[name], "allowed_modes": ["REPLAY", "SHADOW", "PAPER"], "automatic_live_promotion": False} for name in self.names]

    def evaluate(self, name: str, context: StrategyContext) -> StrategyResult:
        if name not in self.names:
            raise ValueError("unknown strategy")
        if context.available_at is None or context.available_at > context.now:
            return self._abstain(name, "UNKNOWN_OR_FUTURE_AVAILABILITY")
        if context.now - context.available_at > context.ttl_seconds:
            return self._abstain(name, "STALE_INPUT")
        if any(q.observed_at > context.now or context.now - q.observed_at > context.ttl_seconds for q in context.quotes):
            return self._abstain(name, "STALE_OR_FUTURE_BOOK")
        try:
            evaluator: Callable[[StrategyContext], StrategyResult] = getattr(self, "_" + name)
            return evaluator(context)
        except (KeyError, IndexError, TypeError, ValueError, ArithmeticError, OverflowError):
            return self._abstain(name, "INVALID_OR_MISSING_INPUT")

    @staticmethod
    def _abstain(name: str, reason: str, **estimates: Any) -> StrategyResult:
        return StrategyResult(name, "ABSTAIN", reason, estimates=estimates)

    @staticmethod
    def _proposal(name: str, c: StrategyContext, edge: Decimal | None, size: Decimal, *, execution: str = "AUTO", legs: tuple[Mapping[str, Any], ...] = (), **estimates: Any) -> StrategyResult:
        expiry = min([c.available_at if c.available_at is not None else c.now] + [q.observed_at for q in c.quotes]) + c.ttl_seconds
        return StrategyResult(name, "PROPOSE", "RISK_REVIEW_REQUIRED", edge, size, execution, c.evidence_ids, estimates, legs, expiry)

    def _structural(self, c: StrategyContext) -> StrategyResult:
        f = c.features
        if not f.get("contracts_verified") or not f.get("exhaustive_scenarios"):
            return self._abstain("structural", "UNVERIFIED_PAYOFF_COVERAGE")
        matrix = f["payoff_matrix"]
        if len(c.quotes) < 2 or not matrix or any(len(row) != len(c.quotes) for row in matrix):
            return self._abstain("structural", "INVALID_PAYOFF_MATRIX")
        payoff_rows = [[dec(v) for v in row] for row in matrix]
        if any(v < 0 for row in payoff_rows for v in row):
            return self._abstain("structural", "NEGATIVE_OR_UNCOLLATERALIZED_PAYOFF")
        guaranteed = min(sum(row, ZERO) for row in payoff_rows)
        if any(q.fee_rate is None for q in c.quotes):
            return self._abstain("structural", "UNKNOWN_FEES")
        conversion = dec(f["conversion_cost_per_share"])
        unwind = dec(f["unwind_loss_per_share"])
        adverse = dec(f["adverse_selection_per_share"])
        if min(conversion, unwind, adverse) < 0:
            return self._abstain("structural", "NEGATIVE_COST")
        atomic = bool(f.get("atomic_execution_verified"))
        if not atomic and not f.get("unwind_route_verified"):
            return self._abstain("structural", "NONATOMIC_NO_ACCEPTABLE_UNWIND")
        entry = sum((q.ask + taker_fee(ONE, q.ask, q.fee_rate or ZERO) for q in c.quotes), ZERO)
        edge = guaranteed - entry - conversion - adverse - (ZERO if atomic else unwind)
        size = min(q.ask_size for q in c.quotes)
        if edge <= 0 or size < max(q.min_size for q in c.quotes):
            return self._abstain("structural", "NO_NET_EDGE_OR_CAPACITY", calculated_edge=str(edge))
        return self._proposal("structural", c, edge, size, legs=tuple({"token": q.token, "side": "BUY", "limit_price": str(q.ask)} for q in c.quotes), classification="STRUCTURAL_ATOMIC" if atomic else "STRUCTURAL_NONATOMIC", guaranteed_payout=str(guaranteed), partial_leg_loss_bound=str(size * unwind), residual_risks=["venue", "collateral", "settlement", "contract"])

    def _market_making(self, c: StrategyContext) -> StrategyResult:
        f, q = c.features, c.quotes[0]
        if not f.get("post_only_supported") or not f.get("fill_model_validated"):
            return self._abstain("market_making", "POST_ONLY_OR_QUEUE_MODEL_UNVERIFIED")
        if c.now - int(f.get("last_quote_at", 0)) < int(f.get("minimum_reprice_seconds", 5)):
            return self._abstain("market_making", "REPRICE_COOLDOWN")
        inventory, cap = dec(f["inventory"]), dec(f["inventory_cap"])
        half_spread, adverse = dec(f["half_spread"]), dec(f["adverse_selection"])
        if cap <= 0 or inventory < 0 or inventory > cap or min(half_spread, adverse) < 0:
            return self._abstain("market_making", "INVENTORY_OR_COST_LIMIT")
        fair = dec(f["fair_price"])
        if not 0 < fair < 1:
            return self._abstain("market_making", "INVALID_FAIR_VALUE")
        skew = inventory / cap * half_spread
        bid = (min(q.ask - q.tick, fair - half_spread - skew) / q.tick).to_integral_value(rounding=ROUND_FLOOR) * q.tick
        ask = (max(q.bid + q.tick, fair + half_spread - skew) / q.tick).to_integral_value(rounding=ROUND_CEILING) * q.tick
        fill_p = dec(f["fill_probability"])
        if not 0 <= fill_p <= 1:
            return self._abstain("market_making", "INVALID_FILL_PROBABILITY")
        maker_fee_rate = dec(f["maker_fee_rate"])
        if maker_fee_rate < 0 or fill_p == 0:
            return self._abstain("market_making", "INVALID_MAKER_COST_OR_NO_FILLS")
        legs: list[Mapping[str, Any]] = []
        if bid > 0 and fair - bid > adverse and cap - inventory >= q.min_size:
            legs.append({"token": q.token, "side": "BUY", "price": str(bid), "quantity": str(min(cap - inventory, q.bid_size)), "post_only": True})
        if ask < 1 and ask - fair > adverse and inventory >= q.min_size:
            legs.append({"token": q.token, "side": "SELL", "price": str(ask), "quantity": str(inventory), "post_only": True})
        legs = [leg for leg in legs if dec(leg["quantity"]) >= q.min_size]
        if not legs:
            return self._abstain("market_making", "NO_INVENTORY_SAFE_QUOTE")
        edge = min(abs(dec(leg["price"]) - fair) - adverse - taker_fee(ONE, dec(leg["price"]), maker_fee_rate) for leg in legs) * fill_p
        if edge <= 0:
            return self._abstain("market_making", "NO_NET_MAKER_EDGE")
        return self._proposal("market_making", c, edge, max(dec(leg["quantity"]) for leg in legs), legs=tuple(legs), queue_model="validated input; touching quote is not a fill", confirmed_rebates_only=True, inventory_skew=str(skew))

    def _wallet_intelligence(self, c: StrategyContext) -> StrategyResult:
        f = c.features
        records: Sequence[Mapping[str, Any]] = f["cashflows"]
        cutoff, selected = int(f["selection_cutoff"]), int(f["selected_at"])
        if cutoff >= selected or selected >= c.now or any(int(r["available_at"]) > cutoff for r in records):
            return self._abstain("wallet_intelligence", "WALLET_SELECTION_LEAKAGE")
        if not f.get("cashflows_complete") or not f.get("discarded_cohort_recorded"):
            return self._abstain("wallet_intelligence", "INCOMPLETE_CASHFLOWS_OR_COHORT")
        pnl = ZERO
        for r in records:
            kind, amount = r["kind"], dec(r["amount"])
            if amount < 0:
                raise ValueError("negative cashflow")
            if kind in ("SELL", "REDEEM"):
                pnl += amount
            elif kind in ("BUY", "FEE"):
                pnl -= amount
            elif kind not in ("DEPOSIT", "WITHDRAW", "MINT", "TRANSFER"):
                raise ValueError("unknown cashflow type")
        pnl += dec(f["open_position_liquidation_value"])
        if not f.get("prospective_validated"):
            return self._proposal("wallet_intelligence", c, None, ZERO, execution="MANUAL", reconstructed_pnl=str(pnl), reason_detail="historical wallet performance is auxiliary evidence; external hedges unknown")
        result = self._forecast_proposal("wallet_intelligence", c)
        return result

    def _weather(self, c: StrategyContext) -> StrategyResult:
        f = c.features
        if not f.get("contract_station_matched") or not f.get("forecast_target_matched"):
            return self._abstain("weather", "STATION_UNIT_PERIOD_OR_ROUNDING_MISMATCH")
        if int(f["forecast_available_at"]) > c.now:
            return self._abstain("weather", "FUTURE_FORECAST_RUN")
        members = [dec(v) for v in f["members"]]
        if not members or len(members) != int(f["received_members"]) or len(members) > int(f["expected_members"]):
            return self._abstain("weather", "INVALID_ENSEMBLE_COVERAGE")
        low, high = dec(f["target_low"]), dec(f["target_high"])
        if low >= high:
            raise ValueError("empty target")
        raw = D(sum(low <= x < high for x in members)) / D(len(members))
        if not f.get("calibration_validated"):
            return self._abstain("weather", "RAW_ENSEMBLE_NOT_CALIBRATED", raw_frequency=str(raw), members=len(members), independence_assumed=False)
        return self._forecast_proposal("weather", c, raw_frequency=str(raw), members=len(members), independence_assumed=False)

    def _news_cross_market(self, c: StrategyContext) -> StrategyResult:
        f = c.features
        if not f.get("entity_id_verified") or not f.get("primary_source_verified"):
            return self._abstain("news_cross_market", "UNVERIFIED_ENTITY_OR_SOURCE")
        published, first_seen = int(f["published_at"]), int(f["first_seen_at"])
        if published > first_seen or first_seen > c.now or f.get("recycled"):
            return self._abstain("news_cross_market", "FUTURE_OR_RECYCLED_NEWS")
        prior, current = dec(f["price_before"]), dec(f["price_after"])
        if prior <= 0 or current < 0:
            raise ValueError("invalid news price")
        move = current / prior - ONE
        if not f.get("calibration_validated"):
            return self._proposal("news_cross_market", c, None, ZERO, execution="MANUAL", observed_move=str(move), detection_delay_seconds=first_seen - published, priced_in_assessment="UNKNOWN", classification="RELATIVE_VALUE" if f.get("related_market") else "CATALYST_ALERT")
        return self._forecast_proposal("news_cross_market", c, observed_move=str(move), priced_in_assessment="ESTIMATE", classification="RELATIVE_VALUE")

    def _forecasting(self, c: StrategyContext) -> StrategyResult:
        return self._forecast_proposal("forecasting", c)

    def _forecast_proposal(self, name: str, c: StrategyContext, **extra: Any) -> StrategyResult:
        f, q = c.features, c.quotes[0]
        if not f.get("calibration_validated") or not f.get("calibrator_id"):
            return self._abstain(name, "UNCALIBRATED_FORECAST")
        if int(f["training_labels_available_at"]) >= int(f["forecast_issued_at"]) or int(f["forecast_issued_at"]) > c.now:
            return self._abstain(name, "FORECAST_TEMPORAL_LEAKAGE")
        if q.fee_rate is None:
            return self._abstain(name, "UNKNOWN_FEES")
        lower, probability, upper = (dec(f[k]) for k in ("p_lower", "p_calibrated", "p_upper"))
        if not 0 <= lower <= probability <= upper <= 1:
            raise ValueError("invalid probability interval")
        adverse = dec(f["adverse_selection_per_share"])
        if adverse < 0:
            raise ValueError("negative adverse selection")
        edge = lower - q.ask - taker_fee(ONE, q.ask, q.fee_rate) - adverse
        if edge <= 0 or q.ask_size < q.min_size:
            return self._abstain(name, "NO_CONSERVATIVE_NET_EDGE", calculated_edge=str(edge))
        return self._proposal(name, c, edge, q.ask_size, legs=({"token": q.token, "side": "BUY", "limit_price": str(q.ask)},), p_event=str(probability), probability_interval=[str(lower), str(upper)], conservative_lower_used=True, **extra)

    def _radar(self, c: StrategyContext) -> StrategyResult:
        f = c.features
        if not f.get("seasonality_adjusted") or not f.get("corporate_actions_adjusted"):
            return self._abstain("radar", "INCOMPARABLE_BASELINE")
        if f.get("manipulation_flags"):
            return self._abstain("radar", "MANIPULATION_OR_EXIT_RESTRICTION", flags=f["manipulation_flags"])
        series = [float(x) for x in f["historical_volume"]]
        current = float(f["current_volume"])
        if len(series) < 20 or not all(isfinite(x) and x >= 0 for x in series + [current]):
            return self._abstain("radar", "INSUFFICIENT_OR_INVALID_BASELINE")
        sigma = pstdev(series)
        if not sigma:
            return self._abstain("radar", "ZERO_VARIANCE_BASELINE")
        zscore = (current - mean(series)) / sigma
        catalyst, liquidity = bool(f.get("primary_catalyst")), dec(f["available_exit_liquidity"])
        if zscore < 3 or not catalyst or liquidity <= 0:
            return self._abstain("radar", "NO_JOINT_ANOMALY", volume_zscore=zscore)
        return self._proposal("radar", c, None, ZERO, execution="MANUAL", volume_zscore=zscore, score_is_probability=False, horizon=f.get("horizon", "days_weeks"), total_loss_possible=True, exit_liquidity=str(liquidity), scenario_upside=f.get("scenario_upside"), missing_data=f.get("missing_data", []))
