from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta
from decimal import Decimal

from edgehunter.domain import Book, Fill, Mode, OrderPlan, OrderState, Side, decimal, require_utc
from edgehunter.storage.journal import Journal, JournalError


class PaperExecutor:
    """Taker-only simulator: depth walk, fee curve, latency and adverse movement.

    No maker fill is inferred from a price touch. Pass a fresh post-latency book
    when available; otherwise the explicit adverse-tick/depth model is used.
    Idempotency is scoped to durable intent + immutable snapshot + level.
    """
    def __init__(self, journal: Journal, *, latency_ms: int = 250,
                 depth_haircut: Decimal = Decimal("0.8"), adverse_ticks: int = 1) -> None:
        self.journal = journal
        self.latency_ms = latency_ms
        self.depth_haircut = decimal(depth_haircut)
        self.adverse_ticks = adverse_ticks
        if not 0 <= latency_ms <= 60000 or not 0 < self.depth_haircut <= 1 or adverse_ticks < 0:
            raise ValueError("invalid paper execution parameters")

    def execute(self, intent_id: str, book: Book, *, now: datetime,
                post_latency_book: Book | None = None,
                executor: tuple[str, int] | None = None) -> list[Fill]:
        now = require_utc(now)
        if executor is not None:
            self.journal.check_executor(*executor, now=now)
            return self._execute(intent_id, book, now=now, post_latency_book=post_latency_book,
                                 executor=executor)
        owner = str(uuid.uuid4())
        epoch = self.journal.acquire_executor(owner, now=now, ttl_seconds=60)
        try:
            return self._execute(intent_id, book, now=now, post_latency_book=post_latency_book,
                                 executor=(owner, epoch))
        finally:
            self.journal.release_executor(owner, epoch, now=now)

    def _execute(self, intent_id: str, book: Book, *, now: datetime,
                 post_latency_book: Book | None, executor: tuple[str, int]) -> list[Fill]:
        now = require_utc(now)
        intent = self.journal.intent(intent_id)
        plan: OrderPlan = intent["plan"]
        if now < plan.created_at:
            raise JournalError("dispatch precedes approved plan")
        if now < datetime.fromisoformat(intent["updated_at"]):
            raise JournalError("dispatch timestamp regressed")
        if plan.mode not in (Mode.REPLAY, Mode.PAPER):
            raise JournalError("real execution is blocked")
        if intent["state"] in ("FILLED", "CANCELLED", "REJECTED", "EXPIRED"):
            return []
        if intent["state"] in ("UNKNOWN", "SUBMITTING", "CANCEL_PENDING"):
            raise JournalError("uncertain or cancelling order must reconcile, not resubmit")
        effective_time = now+timedelta(milliseconds=self.latency_ms)
        active_book = post_latency_book or book
        if not active_book.valid or active_book.asset_id != plan.asset_id or active_book.contract_hash != plan.contract_hash or active_book.metadata_version != plan.metadata_version:
            raise JournalError("book invalid or contract changed")
        if active_book.observed_at > effective_time or (active_book.available_at and active_book.available_at > effective_time):
            raise JournalError("future book cannot execute in historical replay")
        if (effective_time-active_book.observed_at).total_seconds() > 10:
            raise JournalError("stale execution book")
        if effective_time >= plan.expires_at:
            if intent["state"] == "PREPARED":
                self.journal.transition(intent_id, OrderState.EXPIRED, now=effective_time, executor=executor)
                return []
            raise JournalError("expired open order requires cancellation reconciliation")
        if self.journal.control_state()["halted"] == "true":
            raise JournalError("persisted halt blocks dispatch")
        if self.journal.control_state()["paused"] == "true" and plan.side == Side.BUY:
            raise JournalError("new risk is paused")
        lease = self.journal.control_state()["lease_expires_at"]
        if not lease or datetime.fromisoformat(lease) <= effective_time:
            raise JournalError("control lease expired before dispatch")
        if intent["state"] == "PREPARED":
            self.journal.transition(intent_id, OrderState.SUBMITTING, now=now, executor=executor)
            self.journal.transition(intent_id, OrderState.ACKNOWLEDGED, now=effective_time, executor=executor)
            self.journal.transition(intent_id, OrderState.OPEN, now=effective_time, executor=executor)
        elif intent["state"] == "ACKNOWLEDGED":
            self.journal.transition(intent_id, OrderState.OPEN, now=effective_time, executor=executor)
        remaining = plan.quantity-Decimal(intent["filled_quantity"])
        levels = active_book.asks if plan.side == Side.BUY else active_book.bids
        fills: list[Fill] = []
        fingerprint = hashlib.sha256((active_book.asset_id+active_book.observed_at.isoformat()+str(levels)).encode()).hexdigest()[:24]
        for index, level in enumerate(levels):
            if remaining <= 0:
                break
            shift = Decimal(0) if post_latency_book is not None else plan.tick_size*self.adverse_ticks
            price = level.price+shift if plan.side == Side.BUY else level.price-shift
            if not 0 < price <= 1:
                continue
            if (plan.side == Side.BUY and price > plan.limit_price) or (plan.side == Side.SELL and price < plan.limit_price):
                break
            trade_id = f"paper:{intent_id}:{fingerprint}:{index}"
            if self.journal.has_fill(trade_id):
                continue
            capacity = (level.quantity*self.depth_haircut).quantize(Decimal("0.000001"), rounding="ROUND_FLOOR")
            quantity = min(remaining, self.journal.available_paper_depth(fingerprint, plan.side, index, capacity))
            if quantity <= 0:
                continue
            fill = Fill(trade_id, intent_id, quantity, price,
                        plan.fee_for(quantity, price), effective_time)
            if self.journal.apply_fill(fill, executor=executor, liquidity=(fingerprint, index, capacity)):
                fills.append(fill)
                remaining -= quantity
        return fills

    def cancel(self, intent_id: str, *, now: datetime | None = None) -> None:
        intent = self.journal.intent(intent_id)
        if intent["state"] in ("FILLED", "CANCELLED", "EXPIRED", "REJECTED"):
            return
        self.journal.transition(intent_id, OrderState.CANCEL_PENDING, now=now)
        self.journal.confirm_cancel(intent_id, reconciled_filled_quantity=Decimal(intent["filled_quantity"]), now=now)
