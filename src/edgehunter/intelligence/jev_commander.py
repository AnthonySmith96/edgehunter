"""JevCommander: Executive Decision Maker for Polymarket trading.

Jev is the sovereign decision maker ('mandante'). All market feeds, order books,
and execution systems act as its instruments and tools.
"""
from __future__ import annotations

import asyncio
import math
import ssl
import time
from dataclasses import asdict, dataclass
from decimal import ROUND_FLOOR, Decimal
from typing import Any

import httpx

from edgehunter.intelligence.jev_forecast import (
    QUESTION,
    parse_forecast,
)
from edgehunter.intelligence.providers import AccessBlocked, CostBudget

D = Decimal
DEFAULT_ENDPOINT = "https://api.typesafe.ai/v1/systemone"


@dataclass(frozen=True)
class JevDecision:
    action: str  # "ENTER" or "PASS"
    side: str | None  # "UP", "DOWN", or None
    asset_id: str | None
    probability_up: float
    probability_down: float
    edge: Decimal
    chosen_ask: Decimal
    limit_price: Decimal
    fee_per_share: Decimal
    total_cost: Decimal
    quantity: Decimal
    stake_usd: Decimal
    jev_latency_ms: float
    jev_model: str
    reason: str
    tokens_used: dict[str, int]
    state_dossier: dict[str, Any]
    kelly_fraction: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        for k in ("edge", "chosen_ask", "limit_price", "fee_per_share", "total_cost", "quantity", "stake_usd"):
            result[k] = str(result[k])
        return result


class JevCommander:
    """Executive decision maker powered by Jev (TypeSafe AI)."""

    def __init__(
        self,
        *,
        api_key: str,
        budget: CostBudget,
        model: str = "jev-latest",
        max_call_cost: Decimal = Decimal("0.001"),
        timeout_seconds: float = 3.0,
        min_edge: Decimal = Decimal("0.04"),
        slippage_per_share: Decimal = Decimal("0.03"),
        fee_curve_sensitivity: Decimal = Decimal("0.10"),
        fixed_stake_usd: Decimal = Decimal("10.0"),
        max_stake_usd: Decimal = Decimal("10.0"),
        min_stake_usd: Decimal = Decimal("2.0"),
        max_entry_price: Decimal = Decimal("0.72"),
        sizing_mode: str = "fractional_kelly",
        kelly_fraction: Decimal = Decimal("0.25"),
        visible_depth_fraction: Decimal = Decimal("0.8"),
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not 0 < timeout_seconds <= 15:
            raise ValueError("invalid commander timeout")
        amounts = {
            "max_call_cost": max_call_cost,
            "fixed_stake_usd": fixed_stake_usd,
            "max_stake_usd": max_stake_usd,
            "min_stake_usd": min_stake_usd,
        }
        if any(not value.is_finite() or value <= 0 for value in amounts.values()):
            raise ValueError("commander costs and stake limits must be finite and positive")
        if min_stake_usd > max_stake_usd:
            raise ValueError("minimum stake exceeds maximum stake")
        fractions = (min_edge, slippage_per_share, fee_curve_sensitivity, kelly_fraction)
        if any(not value.is_finite() or not 0 <= value <= 1 for value in fractions):
            raise ValueError("commander fractions must be finite and between zero and one")
        if not max_entry_price.is_finite() or not 0 < max_entry_price < 1:
            raise ValueError("invalid maximum entry price")
        if not visible_depth_fraction.is_finite() or not 0 < visible_depth_fraction <= 1:
            raise ValueError("invalid visible depth fraction")
        if sizing_mode not in {"fractional_kelly", "fixed"}:
            raise ValueError("invalid commander sizing mode")
        self.api_key = api_key
        self.budget = budget
        self.model = model
        self.max_call_cost = max_call_cost
        self.timeout_seconds = timeout_seconds
        self.min_edge = min_edge
        self.slippage = slippage_per_share
        self.fee_sensitivity = fee_curve_sensitivity
        self.fixed_stake = fixed_stake_usd
        self.max_stake = max_stake_usd
        self.min_stake = min_stake_usd
        self.max_entry_price = max_entry_price
        self.sizing_mode = sizing_mode
        self.kelly_fraction = kelly_fraction
        self.depth_fraction = visible_depth_fraction
        self.client = client or httpx.AsyncClient(
            timeout=timeout_seconds,
            verify=ssl.create_default_context(),
            follow_redirects=False,
        )
        self.failures = 0
        self.blocked_until = 0.0

    def build_market_dossier(
        self,
        *,
        slug: str,
        seconds_to_expiry: float,
        spot_price: float,
        open_price: float,
        vol_per_second: float,
        up_ask: float,
        down_ask: float,
        available_cash: Decimal,
    ) -> dict[str, Any]:
        """Compile the market intelligence report for Jev."""
        if spot_price <= 0 or open_price <= 0 or vol_per_second <= 0:
            raise ValueError("invalid market numeric values")
        horizon = max(1.0, seconds_to_expiry)
        denom = vol_per_second * math.sqrt(horizon)
        z = math.log(spot_price / open_price) / denom if denom > 0 else 0.0
        dist_usd = spot_price - open_price
        direction = "ABOVE_OPEN" if dist_usd > 0 else ("BELOW_OPEN" if dist_usd < 0 else "AT_OPEN")

        market_prob_up = up_ask / (up_ask + down_ask) if (up_ask + down_ask) > 0 else 0.5
        spread = round(up_ask + down_ask - 1.0, 4)

        return {
            "mandate": "JEV_EXECUTIVE_COMMANDER",
            "contract": "Bitcoin Up or Down, 5-minute expiry on Polymarket",
            "slug": slug,
            "seconds_to_expiry": round(seconds_to_expiry, 1),
            "bitcoin_spot_price": spot_price,
            "bitcoin_open_reference": open_price,
            "price_direction_from_open": direction,
            "distance_from_open_usd": round(dist_usd, 2),
            "standardized_log_distance_z": round(z, 4),
            "volatility_per_sqrt_second": round(vol_per_second, 6),
            "polymarket_up_ask": up_ask,
            "polymarket_down_ask": down_ask,
            "polymarket_implied_probability_up": round(market_prob_up, 4),
            "polymarket_overround_spread": spread,
            "simulated_cash_available_usd": float(available_cash),
        }

    async def decide(
        self,
        *,
        slug: str,
        seconds_to_expiry: float,
        spot_price: float,
        open_price: float,
        vol_per_second: float,
        up_book: Any,
        down_book: Any,
        tokens: list[str],
        available_cash: Decimal,
        available_until: float,
    ) -> JevDecision:
        """Consult Jev and return the sovereign trade decision."""
        dossier = self.build_market_dossier(
            slug=slug,
            seconds_to_expiry=seconds_to_expiry,
            spot_price=spot_price,
            open_price=open_price,
            vol_per_second=vol_per_second,
            up_ask=float(up_book.ask),
            down_ask=float(down_book.ask),
            available_cash=available_cash,
        )
        prob_up = 0.5
        usage = {"input_tokens": 0, "output_tokens": 0}
        response_model = self.model
        elapsed_ms = 0.0

        def null_decision(reason: str) -> JevDecision:
            return JevDecision(
                action="PASS",
                side=None,
                asset_id=None,
                probability_up=prob_up,
                probability_down=1.0 - prob_up,
                edge=D("0"),
                chosen_ask=D("0"),
                limit_price=D("0"),
                fee_per_share=D("0"),
                total_cost=D("0"),
                quantity=D("0"),
                stake_usd=D("0"),
                jev_latency_ms=elapsed_ms,
                jev_model=response_model,
                reason=reason,
                tokens_used=usage,
                state_dossier=dossier,
            )

        if not self.api_key:
            return null_decision("TYPESAFE_API_KEY_MISSING")

        remaining = available_until - time.time()
        if remaining <= 0:
            return null_decision("DECISION_WINDOW_EXPIRED")

        if time.monotonic() < self.blocked_until:
            return null_decision("JEV_CIRCUIT_OPEN_TOO_MANY_FAILURES")

        try:
            reservation = self.budget.reserve(self.max_call_cost)
        except AccessBlocked as exc:
            return null_decision(f"BUDGET_EXHAUSTED_{type(exc).__name__}")

        body = {
            "model": self.model,
            "state": dossier,
            "questions": {"terminal_outcome": QUESTION},
        }

        start = time.perf_counter()
        try:
            timeout_limit = min(remaining, self.timeout_seconds)
            response = await asyncio.wait_for(
                self.client.post(
                    DEFAULT_ENDPOINT,
                    headers={"Authorization": "Bearer " + self.api_key},
                    json=body,
                ),
                timeout=timeout_limit,
            )
            if response.status_code != 200:
                raise ValueError(f"JEV_HTTP_{response.status_code}")
            raw = response.json()
            prob_up, usage = parse_forecast(raw, expected_model=self.model)
            response_model = raw["model"]
            self.failures = 0
        except Exception as exc:
            self.failures += 1
            if self.failures >= 3:
                self.blocked_until = time.monotonic() + 60.0
            return null_decision(f"JEV_INFERENCE_FAILED_{type(exc).__name__}")
        finally:
            self.budget.finish(reservation, None)

        elapsed_ms = (time.perf_counter() - start) * 1000.0
        if time.time() >= available_until:
            return null_decision("DECISION_WINDOW_EXPIRED_AFTER_INFERENCE")
        prob_down = 1.0 - prob_up

        # Jev calculates edge against each side
        # Candidate 0 = UP, Candidate 1 = DOWN
        evaluations = []
        for index, (book, prob, side_name) in enumerate([
            (up_book, prob_up, "UP"),
            (down_book, prob_down, "DOWN"),
        ]):
            entry = book.ask + self.slippage
            if entry >= 1 or entry > self.max_entry_price:
                continue
            fee = self.fee_sensitivity * entry * (1 - entry)
            cost_unit = entry + fee
            edge = D(str(round(prob, 6))) - cost_unit
            if edge >= self.min_edge:
                evaluations.append((edge, index, side_name, book, entry, fee, cost_unit))

        if not evaluations:
            return JevDecision(
                action="PASS",
                side=None,
                asset_id=None,
                probability_up=prob_up,
                probability_down=prob_down,
                edge=D("0"),
                chosen_ask=D("0"),
                limit_price=D("0"),
                fee_per_share=D("0"),
                total_cost=D("0"),
                quantity=D("0"),
                stake_usd=D("0"),
                jev_latency_ms=elapsed_ms,
                jev_model=response_model,
                reason="NO_COSTED_EDGE_MEETS_JEV_THRESHOLD",
                tokens_used=usage,
                state_dossier=dossier,
                kelly_fraction=0.0,
            )

        # Pick the best edge recommended by Jev
        best_edge, chosen_idx, side_name, chosen_book, entry, fee, cost_unit = max(
            evaluations, key=lambda x: x[0]
        )

        if self.sizing_mode == "fractional_kelly":
            denom = D("1.0") - cost_unit
            f_star = (best_edge / denom) if denom > D("0") else D("0")
            f_kelly = max(D("0"), f_star * self.kelly_fraction)
            calculated_stake = (available_cash * f_kelly).quantize(D("0.01"), rounding=ROUND_FLOOR)
            stake = min(self.max_stake, calculated_stake)
            kelly_pct = float(f_kelly)
        else:
            stake = min(self.fixed_stake, self.max_stake, available_cash)
            kelly_pct = 0.0

        stake = min(stake, available_cash)

        if stake < self.min_stake:
            return JevDecision(
                action="PASS",
                side=side_name,
                asset_id=tokens[chosen_idx],
                probability_up=prob_up,
                probability_down=prob_down,
                edge=best_edge,
                chosen_ask=chosen_book.ask,
                limit_price=entry,
                fee_per_share=fee,
                total_cost=D("0"),
                quantity=D("0"),
                stake_usd=D("0"),
                jev_latency_ms=elapsed_ms,
                jev_model=response_model,
                reason=(
                    "INSUFFICIENT_SIM_CASH_BALANCE" if available_cash < self.min_stake
                    else "KELLY_STAKE_BELOW_MINIMUM" if self.sizing_mode == "fractional_kelly"
                    else "FIXED_STAKE_BELOW_MINIMUM"
                ),
                tokens_used=usage,
                state_dossier=dossier,
                kelly_fraction=kelly_pct,
            )

        quantity = min(
            stake / cost_unit,
            chosen_book.size * self.depth_fraction,
        ).quantize(D("0.000001"), rounding=ROUND_FLOOR)

        if quantity < chosen_book.minimum:
            return JevDecision(
                action="PASS",
                side=side_name,
                asset_id=tokens[chosen_idx],
                probability_up=prob_up,
                probability_down=prob_down,
                edge=best_edge,
                chosen_ask=chosen_book.ask,
                limit_price=entry,
                fee_per_share=fee,
                total_cost=D("0"),
                quantity=D("0"),
                stake_usd=D("0"),
                jev_latency_ms=elapsed_ms,
                jev_model=response_model,
                reason="ORDER_BOOK_DEPTH_BELOW_MINIMUM",
                tokens_used=usage,
                state_dossier=dossier,
                kelly_fraction=kelly_pct,
            )

        total_cost = (quantity * cost_unit).quantize(D("0.0001"), rounding=ROUND_FLOOR)

        return JevDecision(
            action="ENTER",
            side=side_name,
            asset_id=tokens[chosen_idx],
            probability_up=prob_up,
            probability_down=prob_down,
            edge=best_edge,
            chosen_ask=chosen_book.ask,
            limit_price=entry,
            fee_per_share=fee,
            total_cost=total_cost,
            quantity=quantity,
            stake_usd=stake,
            jev_latency_ms=elapsed_ms,
            jev_model=response_model,
            reason=f"JEV_COMMAND_ENTER_{side_name}_EDGE_{best_edge:+.4f}_KELLY_{stake:.2f}USD",
            tokens_used=usage,
            state_dossier=dossier,
            kelly_fraction=kelly_pct,
        )

    async def close(self) -> None:
        await self.client.aclose()
