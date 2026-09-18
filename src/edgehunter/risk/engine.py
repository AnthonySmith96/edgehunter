from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal

from edgehunter.domain import Book, Mode, OrderPlan, Side, decimal, require_utc
from edgehunter.storage.journal import Journal, JournalError


class RiskRejected(JournalError):
    pass


@dataclass(frozen=True)
class HardEnvelope:
    """An immutable in-process research envelope; live host policy is not installed."""
    capital_limit: Decimal = Decimal("1000")
    max_order_cost: Decimal = Decimal("10")
    max_cluster_exposure: Decimal = Decimal("30")
    max_total_exposure: Decimal = Decimal("100")
    max_realized_loss: Decimal = Decimal("20")
    max_drawdown: Decimal = Decimal("50")
    max_business_loss: Decimal = Decimal("25")
    max_business_drawdown: Decimal = Decimal("60")
    freshness_seconds: int = 10
    strategies: frozenset[str] = frozenset({"structural", "structural_arbitrage", "demo"})
    modes: frozenset[Mode] = frozenset({Mode.REPLAY, Mode.PAPER})

    def __post_init__(self) -> None:
        for name in ("capital_limit", "max_order_cost", "max_cluster_exposure", "max_total_exposure", "max_realized_loss", "max_drawdown", "max_business_loss", "max_business_drawdown"):
            object.__setattr__(self, name, decimal(getattr(self, name), positive=True))
        if self.freshness_seconds < 1 or self.freshness_seconds > 300:
            raise ValueError("invalid freshness bound")
        if any(mode in (Mode.LIVE, Mode.MICRO_LIVE) for mode in self.modes):
            raise ValueError("live execution is not available in this build")


class RiskEngine:
    def __init__(self, journal: Journal, envelope: HardEnvelope | None = None) -> None:
        self.journal = journal
        self.envelope = envelope or HardEnvelope()

    def prepare(self, plan: OrderPlan, book: Book, approval_id: str, *, now: datetime) -> str:
        now = require_utc(now)
        bounds = {key: sorted(str(item) for item in value) if isinstance(value, frozenset) else value
                  for key, value in asdict(self.envelope).items()}
        try:
            return self.journal.reserve_intent(plan, approval_id, now=now, risk_bounds=bounds,
                validate=lambda connection: self.validate(plan, book, now=now, connection=connection))
        except RiskRejected as exc:
            if str(exc) in ("REALIZED_LOSS_BREACH", "DRAWDOWN_BREACH", "BUSINESS_LOSS_BREACH", "BUSINESS_DRAWDOWN_BREACH", "LEDGER_INVALID"):
                self.journal.halt(str(exc), now=now)
            raise

    def validate(self, plan: OrderPlan, book: Book, *, now: datetime, connection: sqlite3.Connection) -> None:
        limits = self.envelope
        if plan.mode not in limits.modes or plan.mode not in (Mode.REPLAY, Mode.PAPER):
            raise RiskRejected("MODE_NOT_AUTHORIZED")
        if plan.wallet != "paper" or plan.venue != "paper" or plan.collateral != self.journal.collateral:
            raise RiskRejected("SIMULATION_IDENTITY_MISMATCH")
        control = self.journal.control_state(connection)
        if control["halted"] == "true":
            raise RiskRejected("PERSISTED_HALT")
        if control["paused"] == "true" and plan.side == Side.BUY:
            raise RiskRejected("NEW_RISK_PAUSED")
        if not control["lease_expires_at"] or datetime.fromisoformat(control["lease_expires_at"]) <= now:
            raise RiskRejected("CONTROL_LEASE_EXPIRED")
        if not self.journal.validate_ledger(connection):
            raise RiskRejected("LEDGER_INVALID")
        if connection.execute("SELECT 1 FROM settled_assets WHERE asset_id=?", (plan.asset_id,)).fetchone():
            raise RiskRejected("ASSET_ALREADY_SETTLED")
        if connection.execute("SELECT 1 FROM intents WHERE state='UNKNOWN' LIMIT 1").fetchone():
            raise RiskRejected("UNRESOLVED_UNKNOWN_ORDER")
        if plan.strategy not in limits.strategies:
            raise RiskRejected("STRATEGY_NOT_AUTHORIZED")
        if not plan.calibrated and not plan.research_only:
            raise RiskRejected("MODEL_UNCALIBRATED")
        if not plan.created_at <= now < plan.expires_at:
            raise RiskRejected("PLAN_EXPIRED_OR_FUTURE")
        if not book.valid or (now-book.observed_at).total_seconds() > limits.freshness_seconds:
            raise RiskRejected("STALE_OR_INVALID_BOOK")
        if book.observed_at > now or (book.available_at is not None and book.available_at > now):
            raise RiskRejected("FUTURE_INFORMATION")
        if book.asset_id != plan.asset_id or book.contract_hash != plan.contract_hash or book.metadata_version != plan.metadata_version:
            raise RiskRejected("CONTRACT_OR_METADATA_CHANGED")
        existing = connection.execute("SELECT market_id,cluster_id,collateral FROM positions WHERE asset_id=?", (plan.asset_id,)).fetchone()
        if existing and tuple(existing) != (plan.market_id, plan.cluster_id, plan.collateral):
            raise RiskRejected("POSITION_IDENTITY_CHANGED")
        if plan.side == Side.BUY and not any(level.price <= plan.limit_price for level in book.asks):
            raise RiskRejected("NO_EXECUTABLE_ASK")
        if plan.side == Side.SELL:
            if not plan.reduce_only:
                raise RiskRejected("SELL_REQUIRES_REDUCE_ONLY")
            position = connection.execute("SELECT quantity FROM positions WHERE asset_id=?", (plan.asset_id,)).fetchone()
            owned = Decimal(position[0]) if position else Decimal(0)
            committed = Decimal(0)
            for row in connection.execute("SELECT payload,filled_quantity FROM intents WHERE state NOT IN ('FILLED','CANCELLED','REJECTED','EXPIRED')"):
                other = OrderPlan.from_json(row[0])
                if other.asset_id == plan.asset_id and other.side == Side.SELL:
                    committed += other.quantity-Decimal(row[1])
            if plan.quantity > owned-committed:
                raise RiskRejected("REDUCE_ONLY_WOULD_OVERSELL")
            if not any(level.price >= plan.limit_price for level in book.bids):
                raise RiskRejected("NO_EXECUTABLE_BID")
            return
        if plan.reduce_only:
            raise RiskRejected("BUY_CANNOT_BE_REDUCE_ONLY_WITHOUT_PAYOFF_PROOF")
        realized = -self.journal.balance("realized_income", connection=connection)-self.journal.balance("fees", connection=connection)
        if realized <= -limits.max_realized_loss:
            raise RiskRejected("REALIZED_LOSS_BREACH")
        if Decimal(control["realized_high_water"])-realized >= limits.max_drawdown:
            raise RiskRejected("DRAWDOWN_BREACH")
        business = realized-self.journal.balance("operating_cost", connection=connection)
        if business <= -limits.max_business_loss:
            raise RiskRejected("BUSINESS_LOSS_BREACH")
        if Decimal(control["business_high_water"])-business >= limits.max_business_drawdown:
            raise RiskRejected("BUSINESS_DRAWDOWN_BREACH")
        if plan.cost_cap > limits.max_order_cost:
            raise RiskRejected("ORDER_COST_LIMIT")
        total = self.journal.balance("inventory_cost", connection=connection)+self.journal.balance("reserved", connection=connection)
        if total+plan.cost_cap > min(limits.capital_limit, limits.max_total_exposure):
            raise RiskRejected("AGGREGATE_EXPOSURE_LIMIT")
        cluster = sum((Decimal(row[0]) for row in connection.execute("SELECT cost_basis FROM positions WHERE cluster_id=?", (plan.cluster_id,))), Decimal(0))
        for row in connection.execute("SELECT payload,reserved FROM intents WHERE reserved!='0'"):
            other = OrderPlan.from_json(row[0])
            if other.cluster_id == plan.cluster_id:
                cluster += Decimal(row[1])
        if cluster+plan.cost_cap > limits.max_cluster_exposure:
            raise RiskRejected("CLUSTER_EXPOSURE_LIMIT")
