from __future__ import annotations

import json
from datetime import datetime, timedelta
from decimal import ROUND_FLOOR, Decimal
from pathlib import Path
from typing import Any, Protocol

from edgehunter.domain import Book, Level, Mode, OrderPlan
from edgehunter.execution import PaperExecutor
from edgehunter.ingestion.provenance import canonical_hash
from edgehunter.risk import HardEnvelope, RiskEngine
from edgehunter.storage.journal import Journal
from edgehunter.venues.polymarket.public import decode_list

D = Decimal


class ForecastModel(Protocol):
    def estimate_yes(self, market_probability: Decimal, category: str,
                     issued_at: int) -> tuple[Decimal, Decimal, Decimal]: ...


class PaperCandidate:
    """Prospective, virtual-money research using an immutable historical candidate.

    Book prices are observed; fees/latency are conservative scenario assumptions.
    No current contract is assumed complementary merely from its question text.
    A single binary share is purchased, so there is no hidden atomic-leg claim.
    """

    def __init__(self, journal: Journal, model: ForecastModel, model_hash: str) -> None:
        self.journal, self.model, self.model_hash = journal, model, model_hash
        self.envelope = HardEnvelope(strategies=frozenset({"research_forecasting"}),
                                     max_order_cost=D("10"), max_cluster_exposure=D("30"),
                                     max_total_exposure=D("100"))

    @classmethod
    def load(cls, journal: Journal, path: Path) -> PaperCandidate:
        from edgehunter.research.calibrated import CalibratedFavorite
        document = json.loads(path.read_text(encoding="utf-8"))
        if document.get("policy") != "RESEARCH_PAPER_ONLY":
            raise ValueError("Candidate is not an approved research artifact")
        snapshot = document["snapshot"]
        return cls(journal, CalibratedFavorite.from_snapshot(snapshot), canonical_hash(document))

    def evaluate(self, snapshot: dict[str, Any], *, now: datetime, clock_healthy: bool,
                 execute: bool, executor: tuple[str, int] | None = None) -> dict[str, Any]:
        result: dict[str, Any] = {"strategy": "research_forecasting", "market_id": snapshot["market_id"],
            "mode": "PAPER" if execute else "SHADOW", "synthetic": False,
            "source": "REAL_PUBLIC_BOOK", "model_hash": self.model_hash, "action": "ABSTAIN",
            "financial_validation": "UNPROVEN_RESEARCH_ONLY", "fee_rate_assumption": "0.10",
            "fee_model": "polymarket_curve_sensitivity_not_verified_actual_fee",
            "adverse_ticks": 1, "reason": "NO_NET_EDGE"}
        if not clock_healthy:
            return {**result, "reason": "CLOCK_SKEW"}
        if snapshot["outcomes"] != ["Yes", "No"]:
            return {**result, "reason": "RESEARCH_POLICY_BINARY_YES_NO_ONLY"}
        if not snapshot["contract"].get("resolutionSource"):
            return {**result, "reason": "MISSING_RESOLUTION_SOURCE"}
        try:
            end = datetime.fromisoformat(snapshot["contract"]["endDate"].replace("Z", "+00:00"))
            hours_to_end = (end-now).total_seconds()/3600
        except (KeyError, ValueError, TypeError):
            return {**result, "reason": "UNKNOWN_EVENT_HORIZON"}
        if not 23 <= hours_to_end <= 25:
            return {**result, "reason": "OUTSIDE_24H_RESEARCH_HORIZON"}
        result["hours_to_end"] = hours_to_end
        if snapshot["contract"].get("negRisk"):
            return {**result, "reason": "NEGATIVE_RISK_UNSUPPORTED"}
        if any(OrderPlan.from_json(row["payload"]).market_id == str(snapshot["market_id"])
               for row in self.journal.intents()):
            return {**result, "reason": "MARKET_ALREADY_ATTEMPTED"}
        books: list[Book] = []
        for event in snapshot["books"]:
            raw = event["payload"]
            if not event["event_time"]:
                return {**result, "reason": "UNKNOWN_BOOK_TIME"}
            observed = datetime.fromisoformat(event["event_time"])
            available = datetime.fromisoformat(event["available_at"])
            if not 0 <= (now-observed).total_seconds() < 8 or available > now:
                return {**result, "reason": "STALE_OR_FUTURE_BOOK"}
            books.append(Book(raw["asset_id"],
                tuple(Level(D(x["price"]), D(x["size"])) for x in sorted(raw["bids"], key=lambda r: D(r["price"]), reverse=True)),
                tuple(Level(D(x["price"]), D(x["size"])) for x in sorted(raw["asks"], key=lambda r: D(r["price"]))),
                observed, snapshot["contract_hash"], self.model_hash, available_at=available))
        if any(not book.asks or not book.bids for book in books):
            return {**result, "reason": "EMPTY_BOOK"}
        market_p = (books[0].asks[0].price+books[0].bids[0].price)/2
        from edgehunter.research.calibrated import market_category
        category = market_category(snapshot.get("tags", []))
        result["category"] = category
        result["category_provenance"] = "CURRENT_TAGS" if snapshot.get("tags") else "OTHER_FALLBACK"
        probability, lower, upper = self.model.estimate_yes(market_p, category, int(now.timestamp()))
        # Trade only the favorite, matching the frozen model's research universe.
        side = 0 if market_p >= D("0.5") else 1
        book = books[side]
        conservative_probability = lower if side == 0 else 1-upper
        tick = D(snapshot["books"][side]["payload"]["tick_size"])
        limit = book.asks[0].price+tick
        if limit >= 1:
            return {**result, "reason": "NO_PRICE_ROOM"}
        unit_cost = limit + D("0.1")*limit*(1-limit)
        edge = conservative_probability-unit_cost
        result.update({"p_event_estimate": str(probability), "conservative_bound": str(conservative_probability),
                       "interval_status": "HEURISTIC_NOT_VALIDATED", "edge_per_share": str(edge)})
        if edge < D("0.0025"):
            return result
        # Reserve the maximal curve fee over the full allowed price interval.
        reserve_unit_cost = limit + D("0.1")*min(limit, D("0.5"))*(1-min(limit, D("0.5")))
        quantity = min((D("9.99")/reserve_unit_cost).quantize(D("0.000001"), rounding=ROUND_FLOOR),
                       (book.asks[0].quantity*D("0.8")).quantize(D("0.000001"), rounding=ROUND_FLOOR))
        minimum = D(snapshot["books"][side]["payload"]["min_order_size"])
        if quantity < minimum:
            return {**result, "reason": "MINIMUM_EXCEEDS_BUDGET_OR_DEPTH"}
        result.update({"action": "PROPOSE", "quantity": str(quantity), "limit_price": str(limit),
                       "reason": "RESEARCH_CANDIDATE", "asset_id": book.asset_id})
        if not execute:
            return result
        plan = OrderPlan(book.asset_id, str(snapshot["market_id"]), "research_forecasting", quantity, limit,
            now, now+timedelta(seconds=5), book.contract_hash, book.metadata_version,
            tick_size=tick, fee_rate=D("0.10"), fee_model="polymarket_curve",
            cluster_id=str(snapshot["contract"]["conditionId"]), mode=Mode.PAPER,
            min_quantity=minimum, calibrated=False, research_only=True)
        approval = self.journal.approve_simulated(plan, now=now)
        intent = RiskEngine(self.journal, self.envelope).prepare(plan, book, approval, now=now)
        fills = PaperExecutor(self.journal).execute(intent, book, now=now, executor=executor)
        # IOC simulation: cancel the unfilled remainder after modeled execution.
        PaperExecutor(self.journal).cancel(intent, now=now+timedelta(seconds=1))
        return {**result, "action": "PAPER_FILLED" if fills else "PAPER_NOT_FILLED",
                "intent_id": intent, "fill_count": len(fills)}


def resolution_payout(market: dict[str, Any], asset_id: str) -> Decimal | None:
    """Only terminal official-resolution metadata; never infer a win from last price."""
    if market.get("closed") is not True or market.get("umaResolutionStatus") != "resolved":
        return None
    tokens = decode_list(market.get("clobTokenIds", []))
    prices = decode_list(market.get("outcomePrices", []))
    if len(tokens) != 2 or len(prices) != 2 or asset_id not in tokens:
        return None
    payoffs = [D(str(p)) for p in prices]
    if payoffs not in ([D(1), D(0)], [D(0), D(1)], [D("0.5"), D("0.5")]):
        return None
    return payoffs[tokens.index(asset_id)]
