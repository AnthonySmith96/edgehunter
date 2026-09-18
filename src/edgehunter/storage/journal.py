"""Durable simulated financial journal; SQLite is the authority, never the UI.

Amounts are stored as decimal strings. Every mutation uses BEGIN IMMEDIATE,
WAL and synchronous=FULL; the outbox is committed in the same transaction.
No function in this module submits an order or transfers real funds.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import TracebackType
from typing import Any, Callable, Iterator, Literal

from edgehunter.domain import (
    Fill,
    Mode,
    OrderPlan,
    OrderState,
    Side,
    ValidationError,
    canonical_json,
    decimal,
    money,
    require_utc,
    utc_now,
)
from edgehunter.domain.models import proceeds


class JournalError(RuntimeError):
    pass


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None,
                 tb: TracebackType | None, /) -> Literal[False]:
        try:
            return super().__exit__(exc_type, exc, tb)
        finally:
            self.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY);
INSERT OR IGNORE INTO schema_version VALUES(1);
CREATE TABLE IF NOT EXISTS control(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS control_commands(
    business_id TEXT PRIMARY KEY, nonce TEXT NOT NULL UNIQUE, record TEXT NOT NULL,
    consumed_at TEXT, decided_by TEXT);
CREATE TABLE IF NOT EXISTS ledger_transactions(
    tx_id TEXT PRIMARY KEY, kind TEXT NOT NULL, payload_hash TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ledger(
    id INTEGER PRIMARY KEY, tx_id TEXT NOT NULL REFERENCES ledger_transactions(tx_id),
    account TEXT NOT NULL, asset TEXT NOT NULL, amount TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ledger_account ON ledger(account,asset);
CREATE TRIGGER IF NOT EXISTS ledger_immutable_update BEFORE UPDATE ON ledger BEGIN
    SELECT RAISE(ABORT,'ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS ledger_immutable_delete BEFORE DELETE ON ledger BEGIN
    SELECT RAISE(ABORT,'ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS transaction_immutable_update BEFORE UPDATE ON ledger_transactions BEGIN
    SELECT RAISE(ABORT,'ledger transaction is append-only'); END;
CREATE TRIGGER IF NOT EXISTS transaction_immutable_delete BEFORE DELETE ON ledger_transactions BEGIN
    SELECT RAISE(ABORT,'ledger transaction is append-only'); END;
CREATE TABLE IF NOT EXISTS approvals(
    approval_id TEXT PRIMARY KEY, nonce TEXT NOT NULL UNIQUE, plan_hash TEXT NOT NULL,
    payload TEXT NOT NULL, expires_at TEXT NOT NULL, actor TEXT NOT NULL,
    human_decision TEXT NOT NULL, execution_status TEXT NOT NULL,
    consumed_by TEXT UNIQUE, simulated INTEGER NOT NULL CHECK(simulated=1));
CREATE TRIGGER IF NOT EXISTS approval_payload_immutable BEFORE UPDATE OF nonce,plan_hash,payload,
    expires_at,actor,simulated ON approvals BEGIN SELECT RAISE(ABORT,'approval is immutable'); END;
CREATE TABLE IF NOT EXISTS intents(
    intent_id TEXT PRIMARY KEY, approval_id TEXT NOT NULL UNIQUE REFERENCES approvals(approval_id),
    plan_hash TEXT NOT NULL, payload TEXT NOT NULL, state TEXT NOT NULL,
    reserved TEXT NOT NULL, filled_quantity TEXT NOT NULL DEFAULT '0',
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, provider_order_id TEXT);
CREATE TABLE IF NOT EXISTS intent_risk_bounds(
    intent_id TEXT PRIMARY KEY REFERENCES intents(intent_id), plan_hash TEXT NOT NULL,
    envelope_hash TEXT NOT NULL, payload TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS risk_bounds_immutable_update BEFORE UPDATE ON intent_risk_bounds BEGIN
    SELECT RAISE(ABORT,'risk bounds are immutable'); END;
CREATE TRIGGER IF NOT EXISTS risk_bounds_immutable_delete BEFORE DELETE ON intent_risk_bounds BEGIN
    SELECT RAISE(ABORT,'risk bounds are immutable'); END;
CREATE TABLE IF NOT EXISTS fills(
    trade_id TEXT PRIMARY KEY, intent_id TEXT NOT NULL REFERENCES intents(intent_id),
    payload TEXT NOT NULL, payload_hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS positions(
    asset_id TEXT PRIMARY KEY, market_id TEXT NOT NULL, cluster_id TEXT NOT NULL,
    collateral TEXT NOT NULL, quantity TEXT NOT NULL, cost_basis TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS settled_assets(
    asset_id TEXT PRIMARY KEY, settlement_id TEXT NOT NULL, payout_per_share TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS outbox(
    sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE,
    topic TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL,
    delivered_at TEXT, attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT);
CREATE TABLE IF NOT EXISTS executor_lease(
    singleton INTEGER PRIMARY KEY CHECK(singleton=1), owner TEXT NOT NULL,
    epoch INTEGER NOT NULL, expires_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS paper_liquidity(
    snapshot TEXT NOT NULL, side TEXT NOT NULL, level_index INTEGER NOT NULL,
    consumed TEXT NOT NULL, PRIMARY KEY(snapshot,side,level_index));
"""


class Journal:
    def __init__(self, path: str | Path, *, collateral: str = "SIM_USD") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.collateral = collateral
        with self._connect() as connection:
            connection.executescript(SCHEMA)
            versions = connection.execute("SELECT version FROM schema_version").fetchall()
            if [row[0] for row in versions] != [1]:
                raise JournalError("unsupported journal schema")
            existing_collateral = connection.execute("SELECT value FROM control WHERE key='collateral'").fetchone()
            if existing_collateral and existing_collateral[0] != collateral:
                raise JournalError("journal collateral identity cannot change")
            for key, value in (("halted", "false"), ("halt_reason", ""), ("paused", "false"),
                               ("collateral", collateral),
                               ("lease_expires_at", ""), ("realized_high_water", "0"), ("business_high_water", "0")):
                connection.execute("INSERT OR IGNORE INTO control VALUES(?,?)", (key, value))

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None, factory=ClosingConnection)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.execute("COMMIT")
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def _outbox(self, connection: sqlite3.Connection, topic: str, payload: Any,
                now: datetime, event_id: str | None = None) -> None:
        connection.execute("INSERT INTO outbox(event_id,topic,payload,created_at) VALUES(?,?,?,?)",
                           (event_id or str(uuid.uuid4()), topic, canonical_json(payload), require_utc(now).isoformat()))

    def _post(self, connection: sqlite3.Connection, tx_id: str, kind: str,
              entries: list[tuple[str, str, Decimal]], now: datetime) -> bool:
        balances: dict[str, Decimal] = {}
        for _, asset, amount in entries:
            if not amount.is_finite():
                raise JournalError("non-finite ledger amount")
            balances[asset] = balances.get(asset, Decimal(0)) + amount
        if any(value != 0 for value in balances.values()):
            raise JournalError("unbalanced ledger transaction")
        payload = canonical_json({"kind": kind, "entries": entries})
        digest = hashlib.sha256(payload.encode()).hexdigest()
        previous = connection.execute("SELECT payload_hash FROM ledger_transactions WHERE tx_id=?", (tx_id,)).fetchone()
        if previous:
            if previous[0] != digest:
                raise JournalError("idempotency key reused with a different transaction")
            return False
        connection.execute("INSERT INTO ledger_transactions VALUES(?,?,?,?)",
                           (tx_id, kind, digest, require_utc(now).isoformat()))
        connection.executemany("INSERT INTO ledger(tx_id,account,asset,amount) VALUES(?,?,?,?)",
                               [(tx_id, account, asset, str(amount)) for account, asset, amount in entries])
        self._outbox(connection, "ledger", {"tx_id": tx_id, "kind": kind}, now, f"ledger:{tx_id}")
        return True

    def balance(self, account: str, *, asset: str | None = None,
                connection: sqlite3.Connection | None = None) -> Decimal:
        if connection is None:
            with self._connect() as db:
                return self.balance(account, asset=asset, connection=db)
        rows = connection.execute("SELECT amount FROM ledger WHERE account=? AND asset=?",
                                  (account, asset or self.collateral))
        return sum((Decimal(row[0]) for row in rows), Decimal(0))

    def deposit(self, amount: Decimal, *, idempotency_key: str, now: datetime | None = None) -> bool:
        amount = decimal(amount, positive=True)
        with self.transaction() as connection:
            return self._post(connection, f"deposit:{idempotency_key}", "SIMULATED_DEPOSIT",
                              [("cash", self.collateral, amount), ("contributed_capital", self.collateral, -amount)], now or utc_now())

    def record_operating_cost(self, amount: Decimal, *, idempotency_key: str,
                              now: datetime | None = None) -> None:
        amount = decimal(amount, positive=True)
        with self.transaction() as connection:
            existing = connection.execute("SELECT 1 FROM ledger_transactions WHERE tx_id=?", (f"expense:{idempotency_key}",)).fetchone()
            if not existing and self.balance("cash", connection=connection) < amount:
                raise JournalError("insufficient cash")
            self._post(connection, f"expense:{idempotency_key}", "SIMULATED_EXPENSE",
                       [("cash", self.collateral, -amount), ("operating_cost", self.collateral, amount)], now or utc_now())
            self._update_high_water(connection)

    def renew_control_lease(self, *, now: datetime | None = None, ttl_seconds: int = 60) -> None:
        if not 1 <= ttl_seconds <= 60:
            raise ValidationError("control lease must be between 1 and 60 seconds")
        now = require_utc(now or utc_now())
        with self.transaction() as connection:
            connection.execute("UPDATE control SET value=? WHERE key='lease_expires_at'",
                               ((now + timedelta(seconds=ttl_seconds)).isoformat(),))

    def halt(self, reason: str, *, now: datetime | None = None) -> None:
        if not reason.strip():
            raise ValidationError("halt requires a reason")
        with self.transaction() as connection:
            connection.execute("UPDATE control SET value='true' WHERE key='halted'")
            connection.execute("UPDATE control SET value=? WHERE key='halt_reason'", (reason,))
            self._outbox(connection, "halt", {"reason": reason}, now or utc_now())

    def control_state(self, connection: sqlite3.Connection | None = None) -> dict[str, str]:
        if connection is None:
            with self._connect() as db:
                return self.control_state(db)
        return dict(connection.execute("SELECT key,value FROM control").fetchall())

    def propose_command(self, kind: str, nonce: str, expires_at: datetime,
                        payload: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
        """Persist a stop-only request before projecting it into PocketBase."""
        timestamp = require_utc(now or utc_now())
        expiry = require_utc(expires_at)
        # PocketBase serializes DateField values with millisecond precision.
        # Bind the proposal to that same precision before hashing/persisting.
        timestamp = timestamp.replace(microsecond=timestamp.microsecond // 1000 * 1000)
        expiry = expiry.replace(microsecond=expiry.microsecond // 1000 * 1000)
        if kind not in ("PAUSE_NEW_RISK", "EMERGENCY_HALT") or not nonce or expiry <= timestamp:
            raise JournalError("invalid stop command")
        record = {"business_id": str(uuid.uuid4()), "kind": kind,
                  "summary": f"SIMULATED {kind}", "payload": payload,
                  "payload_hash": hashlib.sha256(canonical_json(payload).encode()).hexdigest(),
                  "nonce": nonce, "revision": 1, "amount": "0", "currency": self.collateral,
                  "expires_at": expiry.isoformat(), "human_decision": "PENDING",
                  "execution_status": "WAITING", "simulated": True}
        stored = {"request": record, "created_at": timestamp.isoformat()}
        with self.transaction() as connection:
            connection.execute("INSERT INTO control_commands VALUES(?,?,?,NULL,NULL)",
                               (record["business_id"], nonce, canonical_json(stored)))
            self._outbox(connection, "control_proposal", record, timestamp)
        return record

    def consume_control_command(self, record: dict[str, Any], *, expected_actor: str,
                                now: datetime | None = None) -> bool:
        """Consume an authenticated PB response. Caller pins the designated actor.

        The dict alone is not proof of authentication: callers must fetch it
        over their authenticated, trusted control-plane connection.
        """
        timestamp = require_utc(now or utc_now())
        with self.transaction() as connection:
            row = connection.execute("SELECT * FROM control_commands WHERE business_id=?",
                                     (record.get("business_id"),)).fetchone()
            if row is None:
                raise JournalError("control command was not proposed locally")
            stored = json.loads(row["record"])
            original = stored["request"]
            mutable = {"human_decision", "execution_status"}
            for key, value in original.items():
                if key == "expires_at":
                    try:
                        if require_utc(datetime.fromisoformat(record[key])) != datetime.fromisoformat(value):
                            raise JournalError("immutable control expiry changed")
                    except (ValueError, TypeError, KeyError) as exc:
                        raise JournalError("invalid control expiry") from exc
                    continue
                if key not in mutable and canonical_json(record.get(key)) != canonical_json(value):
                    raise JournalError(f"immutable control field changed: {key}")
            if not expected_actor or record.get("decided_by") != expected_actor:
                raise JournalError("control actor is not the designated human")
            if record.get("human_decision") != "APPROVED":
                raise JournalError("control decision not approved")
            try:
                decision_time = require_utc(datetime.fromisoformat(record["decided_at"]))
            except (KeyError, ValueError, TypeError) as exc:
                raise JournalError("invalid control decision timestamp") from exc
            if not datetime.fromisoformat(stored["created_at"]) <= decision_time <= timestamp:
                raise JournalError("control decision is outside the proposal timeline")
            if row["consumed_at"]:
                if row["decided_by"] != expected_actor:
                    raise JournalError("control command actor changed after consumption")
                return False
            if datetime.fromisoformat(original["expires_at"]) <= timestamp:
                raise JournalError("control command expired")
            if record.get("execution_status") not in ("WAITING", "VALIDATING", "ACCEPTED"):
                raise JournalError("invalid unconsumed execution state")
            connection.execute("UPDATE control_commands SET consumed_at=?,decided_by=? WHERE business_id=?",
                               (timestamp.isoformat(), expected_actor, original["business_id"]))
            connection.execute("UPDATE control SET value='true' WHERE key='paused'")
            if original["kind"] == "EMERGENCY_HALT":
                connection.execute("UPDATE control SET value='true' WHERE key='halted'")
                connection.execute("UPDATE control SET value=? WHERE key='halt_reason'",
                                   (f"CONTROL_COMMAND:{original['business_id']}",))
            self._outbox(connection, "control_applied", {"business_id": original["business_id"],
                         "kind": original["kind"], "decided_by": expected_actor, "simulated": True}, timestamp)
            return True

    def approve_simulated(self, plan: OrderPlan, *, now: datetime | None = None,
                          approval_id: str | None = None) -> str:
        """Fixture/paper approval, visibly NOT a human authorization for real trading."""
        now = require_utc(now or utc_now())
        if plan.mode not in (Mode.PAPER, Mode.REPLAY) or plan.wallet != "paper" or plan.venue != "paper" or plan.collateral != self.collateral:
            raise JournalError("simulated approvals cannot authorize real trading")
        if not plan.created_at <= now < plan.expires_at:
            raise JournalError("plan not currently valid")
        approval_id = approval_id or str(uuid.uuid4())
        with self.transaction() as connection:
            connection.execute("INSERT INTO approvals VALUES(?,?,?,?,?,?,?,?,?,?)",
                               (approval_id, str(uuid.uuid4()), plan.plan_hash, canonical_json(plan.as_dict()),
                                require_utc(plan.expires_at).isoformat(), "SIMULATED_RESEARCH_ACTOR",
                                "APPROVED", "WAITING", None, 1))
            self._outbox(connection, "approval", {"approval_id": approval_id, "simulated": True,
                                                  "plan_hash": plan.plan_hash}, now)
        return approval_id

    def reserve_intent(self, plan: OrderPlan, approval_id: str, *, now: datetime,
                       risk_bounds: dict[str, Any],
                       validate: Callable[[sqlite3.Connection], None]) -> str:
        now = require_utc(now)
        with self.transaction() as connection:
            existing = connection.execute("SELECT intent_id,plan_hash FROM intents WHERE approval_id=?", (approval_id,)).fetchone()
            if existing:
                if existing[1] != plan.plan_hash:
                    raise JournalError("approval already consumed by another plan")
                return str(existing[0])
            approval = connection.execute("SELECT * FROM approvals WHERE approval_id=?", (approval_id,)).fetchone()
            if not approval or approval["human_decision"] != "APPROVED" or approval["consumed_by"]:
                raise JournalError("approval missing or consumed")
            if approval["plan_hash"] != plan.plan_hash or approval["payload"] != canonical_json(plan.as_dict()) or datetime.fromisoformat(approval["expires_at"]) <= now:
                raise JournalError("approval expired or plan changed")
            validate(connection)
            reserve = plan.cost_cap if plan.side == Side.BUY else Decimal(0)
            if self.balance("cash", connection=connection) < reserve:
                raise JournalError("insufficient available collateral")
            intent_id = str(uuid.uuid4())
            connection.execute("INSERT INTO intents VALUES(?,?,?,?,?,?,?,?,?,?)",
                               (intent_id, approval_id, plan.plan_hash, canonical_json(plan.as_dict()),
                                OrderState.PREPARED, str(reserve), "0", now.isoformat(), now.isoformat(), None))
            bounds_payload = canonical_json(risk_bounds)
            connection.execute("INSERT INTO intent_risk_bounds VALUES(?,?,?,?)",
                               (intent_id, plan.plan_hash, hashlib.sha256(bounds_payload.encode()).hexdigest(), bounds_payload))
            connection.execute("UPDATE approvals SET consumed_by=?,execution_status='ACCEPTED' WHERE approval_id=? AND consumed_by IS NULL",
                               (intent_id, approval_id))
            self._post(connection, f"reserve:{intent_id}", "RESERVE",
                       [("cash", self.collateral, -reserve), ("reserved", self.collateral, reserve)], now)
            self._outbox(connection, "intent", {"intent_id": intent_id, "state": "PREPARED",
                                                "plan_hash": plan.plan_hash}, now)
            return intent_id

    def intent(self, intent_id: str, connection: sqlite3.Connection | None = None) -> dict[str, Any]:
        if connection is None:
            with self._connect() as db:
                return self.intent(intent_id, db)
        row = connection.execute("SELECT * FROM intents WHERE intent_id=?", (intent_id,)).fetchone()
        if row is None:
            raise JournalError("unknown intent")
        result = dict(row)
        result["plan"] = OrderPlan.from_json(result["payload"])
        return result

    def intents(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM intents ORDER BY created_at")]

    def transition(self, intent_id: str, state: OrderState, *, now: datetime | None = None,
                   executor: tuple[str, int] | None = None) -> None:
        state = OrderState(state)
        allowed = {
            "PREPARED": {"SUBMITTING", "CANCEL_PENDING", "REJECTED", "EXPIRED"},
            "SUBMITTING": {"ACKNOWLEDGED", "UNKNOWN", "REJECTED", "CANCEL_PENDING"},
            "ACKNOWLEDGED": {"OPEN", "CANCEL_PENDING", "UNKNOWN"},
            "OPEN": {"CANCEL_PENDING", "UNKNOWN"},
            "PARTIALLY_FILLED": {"CANCEL_PENDING", "UNKNOWN"},
            "UNKNOWN": {"ACKNOWLEDGED", "OPEN", "CANCEL_PENDING"},
            "CANCEL_PENDING": {"UNKNOWN"},
        }
        rejected: str | None = None
        with self.transaction() as connection:
            timestamp = require_utc(now or utc_now())
            if executor is not None:
                self.check_executor(*executor, now=timestamp, connection=connection)
            intent = self.intent(intent_id, connection)
            if intent["state"] == state:
                return
            if timestamp < datetime.fromisoformat(intent["updated_at"]):
                raise JournalError("order state timestamp regressed")
            if state not in allowed.get(intent["state"], set()):
                raise JournalError(f"invalid order transition {intent['state']} -> {state}")
            if state == OrderState.SUBMITTING:
                control = self.control_state(connection)
                if control["halted"] == "true" or (control["paused"] == "true" and intent["plan"].side == Side.BUY):
                    raise JournalError("persisted stop blocks dispatch")
                if not control["lease_expires_at"] or datetime.fromisoformat(control["lease_expires_at"]) <= timestamp:
                    raise JournalError("control lease expired before dispatch")
                if intent["plan"].side == Side.BUY:
                    rejected = self._dispatch_risk_breach(connection, intent)
            if rejected:
                # Commit the stop while retaining PREPARED and its reservation.
                # Raising inside this transaction would roll the stop back.
                connection.execute("UPDATE control SET value='true' WHERE key='halted'")
                connection.execute("UPDATE control SET value=? WHERE key='halt_reason'", (rejected,))
                self._outbox(connection, "halt", {"reason": rejected, "intent_id": intent_id}, timestamp)
            else:
                connection.execute("UPDATE intents SET state=?,updated_at=? WHERE intent_id=?", (state, timestamp.isoformat(), intent_id))
                if state in (OrderState.REJECTED, OrderState.EXPIRED):
                    self._release(connection, intent_id, timestamp)
                self._outbox(connection, "order_state", {"intent_id": intent_id, "state": state}, timestamp)
        if rejected:
            raise JournalError(f"dispatch risk breach: {rejected}")

    def _dispatch_risk_breach(self, connection: sqlite3.Connection, intent: dict[str, Any]) -> str | None:
        row = connection.execute("SELECT * FROM intent_risk_bounds WHERE intent_id=?", (intent["intent_id"],)).fetchone()
        if not row or row["plan_hash"] != intent["plan"].plan_hash:
            return "APPROVED_RISK_ENVELOPE_MISSING"
        if hashlib.sha256(row["payload"].encode()).hexdigest() != row["envelope_hash"]:
            return "APPROVED_RISK_ENVELOPE_CORRUPT"
        if not self.validate_ledger(connection):
            return "LEDGER_INVALID"
        bounds = json.loads(row["payload"])
        control = self.control_state(connection)
        realized = -self.balance("realized_income", connection=connection)-self.balance("fees", connection=connection)
        business = realized-self.balance("operating_cost", connection=connection)
        if realized <= -Decimal(bounds["max_realized_loss"]):
            return "REALIZED_LOSS_BREACH"
        if Decimal(control["realized_high_water"])-realized >= Decimal(bounds["max_drawdown"]):
            return "DRAWDOWN_BREACH"
        if business <= -Decimal(bounds["max_business_loss"]):
            return "BUSINESS_LOSS_BREACH"
        if Decimal(control["business_high_water"])-business >= Decimal(bounds["max_business_drawdown"]):
            return "BUSINESS_DRAWDOWN_BREACH"
        return None

    def _release(self, connection: sqlite3.Connection, intent_id: str, now: datetime) -> None:
        intent = self.intent(intent_id, connection)
        reserve = Decimal(intent["reserved"])
        if reserve:
            self._post(connection, f"release:{intent_id}", "RELEASE",
                       [("reserved", self.collateral, -reserve), ("cash", self.collateral, reserve)], now)
            connection.execute("UPDATE intents SET reserved='0' WHERE intent_id=?", (intent_id,))

    def confirm_cancel(self, intent_id: str, *, reconciled_filled_quantity: Decimal,
                       now: datetime | None = None) -> None:
        """Only release after final fill reconciliation, not on cancel request/timeout."""
        quantity = decimal(reconciled_filled_quantity)
        timestamp = require_utc(now or utc_now())
        with self.transaction() as connection:
            intent = self.intent(intent_id, connection)
            if quantity != Decimal(intent["filled_quantity"]):
                raise JournalError("cancel reconciliation mismatch; retain reserve")
            if intent["state"] in ("FILLED", "CANCELLED"):
                return
            if timestamp < datetime.fromisoformat(intent["updated_at"]):
                raise JournalError("cancel timestamp regressed")
            if intent["state"] != "CANCEL_PENDING":
                raise JournalError("cancellation must be requested first")
            self._release(connection, intent_id, timestamp)
            connection.execute("UPDATE intents SET state='CANCELLED',updated_at=? WHERE intent_id=?", (timestamp.isoformat(), intent_id))
            self._outbox(connection, "order_state", {"intent_id": intent_id, "state": "CANCELLED"}, timestamp)

    def has_fill(self, trade_id: str) -> bool:
        with self._connect() as connection:
            return connection.execute("SELECT 1 FROM fills WHERE trade_id=?", (trade_id,)).fetchone() is not None

    def available_paper_depth(self, snapshot: str, side: Side, level_index: int, capacity: Decimal) -> Decimal:
        with self._connect() as connection:
            row = connection.execute("SELECT consumed FROM paper_liquidity WHERE snapshot=? AND side=? AND level_index=?",
                                     (snapshot, side, level_index)).fetchone()
            return max(Decimal(0), capacity - (Decimal(row[0]) if row else Decimal(0)))

    def apply_fill(self, fill: Fill, *, executor: tuple[str, int] | None = None,
                   liquidity: tuple[str, int, Decimal] | None = None) -> bool:
        payload = canonical_json({"trade_id": fill.trade_id, "intent_id": fill.intent_id,
                                  "quantity": fill.quantity, "price": fill.price, "fee": fill.fee,
                                  "timestamp": fill.timestamp, "settlement_status": fill.settlement_status})
        digest = hashlib.sha256(payload.encode()).hexdigest()
        with self.transaction() as connection:
            if executor is not None:
                self.check_executor(*executor, now=fill.timestamp, connection=connection)
            previous = connection.execute("SELECT payload_hash FROM fills WHERE trade_id=?", (fill.trade_id,)).fetchone()
            if previous:
                if previous[0] != digest:
                    raise JournalError("fill identity reused with different economics")
                return False
            intent = self.intent(fill.intent_id, connection)
            plan: OrderPlan = intent["plan"]
            if fill.timestamp < plan.created_at:
                raise JournalError("fill predates approved plan")
            if fill.price % plan.tick_size != 0:
                raise JournalError("fill price violates approved tick")
            if intent["state"] in ("PREPARED", "CANCELLED", "REJECTED", "EXPIRED", "FILLED"):
                raise JournalError("fill for non-executable order; requires incident reconciliation")
            total_qty = Decimal(intent["filled_quantity"]) + fill.quantity
            if total_qty > plan.quantity:
                raise JournalError("fill exceeds approved size")
            position = connection.execute("SELECT * FROM positions WHERE asset_id=?", (plan.asset_id,)).fetchone()
            if position and (position["market_id"] != plan.market_id or position["cluster_id"] != plan.cluster_id or position["collateral"] != plan.collateral):
                raise JournalError("position identity changed")
            if liquidity is not None:
                snapshot, level_index, capacity = liquidity
                row = connection.execute("SELECT consumed FROM paper_liquidity WHERE snapshot=? AND side=? AND level_index=?",
                                         (snapshot, plan.side, level_index)).fetchone()
                consumed = Decimal(row[0]) if row else Decimal(0)
                if consumed + fill.quantity > capacity:
                    raise JournalError("paper snapshot depth exhausted")
                connection.execute("INSERT INTO paper_liquidity VALUES(?,?,?,?) ON CONFLICT(snapshot,side,level_index) DO UPDATE SET consumed=excluded.consumed",
                                   (snapshot, plan.side, level_index, str(consumed+fill.quantity)))
            qty = Decimal(position["quantity"]) if position else Decimal(0)
            basis = Decimal(position["cost_basis"]) if position else Decimal(0)
            gross = money(fill.quantity * fill.price) if plan.side == Side.BUY else proceeds(fill.quantity * fill.price)
            if fill.fee > plan.fee_for(fill.quantity, fill.price):
                raise JournalError("fee exceeds approved fee model")
            if plan.side == Side.BUY:
                if fill.price > plan.limit_price or gross + fill.fee > Decimal(intent["reserved"]):
                    raise JournalError("fill breaches price or collateral reserve")
                entries = [("reserved", self.collateral, -gross-fill.fee),
                           ("inventory_cost", self.collateral, gross), ("fees", self.collateral, fill.fee),
                           ("shares", plan.asset_id, fill.quantity), ("external_shares", plan.asset_id, -fill.quantity)]
                qty += fill.quantity
                basis += gross
                reserve = Decimal(intent["reserved"]) - gross - fill.fee
            else:
                if fill.price < plan.limit_price or fill.quantity > qty or fill.fee > gross:
                    raise JournalError("invalid reducing fill")
                removed_basis = basis if fill.quantity == qty else proceeds(basis * fill.quantity / qty)
                entries = [("cash", self.collateral, gross-fill.fee),
                           ("inventory_cost", self.collateral, -removed_basis),
                           ("realized_income", self.collateral, -(gross-removed_basis)),
                           ("fees", self.collateral, fill.fee),
                           ("shares", plan.asset_id, -fill.quantity), ("external_shares", plan.asset_id, fill.quantity)]
                qty -= fill.quantity
                basis -= removed_basis
                reserve = Decimal(0)
            self._post(connection, f"fill:{fill.trade_id}", "SIMULATED_FILL", entries, fill.timestamp)
            connection.execute("INSERT INTO fills VALUES(?,?,?,?)", (fill.trade_id, fill.intent_id, payload, digest))
            connection.execute("INSERT INTO positions VALUES(?,?,?,?,?,?) ON CONFLICT(asset_id) DO UPDATE SET quantity=excluded.quantity,cost_basis=excluded.cost_basis",
                               (plan.asset_id, plan.market_id, plan.cluster_id, self.collateral, str(qty), str(basis)))
            state = "FILLED" if total_qty == plan.quantity else ("CANCEL_PENDING" if intent["state"] == "CANCEL_PENDING" else "PARTIALLY_FILLED")
            updated_at = max(fill.timestamp, datetime.fromisoformat(intent["updated_at"]))
            connection.execute("UPDATE intents SET reserved=?,filled_quantity=?,state=?,updated_at=? WHERE intent_id=?",
                               (str(reserve), str(total_qty), state, updated_at.isoformat(), fill.intent_id))
            if state == "FILLED":
                self._release(connection, fill.intent_id, fill.timestamp)
            self._outbox(connection, "fill", json.loads(payload), fill.timestamp, f"fill:{fill.trade_id}")
            self._update_high_water(connection)
            return True

    def _update_high_water(self, connection: sqlite3.Connection) -> None:
        pnl = -self.balance("realized_income", connection=connection)-self.balance("fees", connection=connection)
        high = Decimal(self.control_state(connection)["realized_high_water"])
        connection.execute("UPDATE control SET value=? WHERE key='realized_high_water'", (str(max(high, pnl)),))
        business = pnl-self.balance("operating_cost", connection=connection)
        business_high = Decimal(self.control_state(connection)["business_high_water"])
        connection.execute("UPDATE control SET value=? WHERE key='business_high_water'", (str(max(business_high, business)),))

    def settle(self, asset_id: str, *, payout_per_share: Decimal, settlement_id: str,
               now: datetime | None = None) -> bool:
        payout = decimal(payout_per_share)
        if payout > 1:
            raise ValidationError("prediction payout outside [0,1]")
        timestamp = require_utc(now or utc_now())
        with self.transaction() as connection:
            previous = connection.execute("SELECT payload FROM outbox WHERE event_id=?", (f"settlement:{settlement_id}",)).fetchone()
            if previous:
                if json.loads(previous[0]) != {"asset_id": asset_id, "payout_per_share": str(payout)}:
                    raise JournalError("settlement ID conflict")
                return False
            for row in connection.execute("SELECT payload,updated_at FROM intents"):
                if OrderPlan.from_json(row[0]).asset_id == asset_id and datetime.fromisoformat(row[1]) > timestamp:
                    raise JournalError("settlement precedes known order activity")
            for row in connection.execute("SELECT payload FROM intents WHERE state NOT IN ('FILLED','CANCELLED','REJECTED','EXPIRED')"):
                if OrderPlan.from_json(row[0]).asset_id == asset_id:
                    raise JournalError("settlement requires all orders reconciled")
            position = connection.execute("SELECT * FROM positions WHERE asset_id=?", (asset_id,)).fetchone()
            if not position or Decimal(position["quantity"]) <= 0:
                raise JournalError("no position to settle")
            qty, basis = Decimal(position["quantity"]), Decimal(position["cost_basis"])
            credit = proceeds(qty * payout)
            self._post(connection, f"settle:{settlement_id}", "SIMULATED_SETTLEMENT",
                       [("cash", self.collateral, credit), ("inventory_cost", self.collateral, -basis),
                        ("realized_income", self.collateral, -(credit-basis)),
                        ("shares", asset_id, -qty), ("external_shares", asset_id, qty)], timestamp)
            connection.execute("UPDATE positions SET quantity='0',cost_basis='0' WHERE asset_id=?", (asset_id,))
            connection.execute("INSERT INTO settled_assets VALUES(?,?,?)", (asset_id, settlement_id, str(payout)))
            self._update_high_water(connection)
            self._outbox(connection, "settlement", {"asset_id": asset_id, "payout_per_share": str(payout)}, timestamp, f"settlement:{settlement_id}")
            return True

    def report(self) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute("BEGIN")
            accounts = {name: self.balance(name, connection=connection) for name in
                        ("cash", "reserved", "inventory_cost", "fees", "realized_income", "operating_cost", "contributed_capital")}
            net = -accounts["realized_income"] - accounts["fees"]
            return {"collateral": self.collateral, "cash": str(accounts["cash"]),
                    "reserved": str(accounts["reserved"]), "inventory_cost": str(accounts["inventory_cost"]),
                    "deposits": str(-accounts["contributed_capital"]), "fees": str(accounts["fees"]),
                    "realized_pnl_before_fees": str(-accounts["realized_income"]),
                    "net_trading_pnl": str(net), "operating_cost": str(accounts["operating_cost"]),
                    "net_business_pnl": str(net-accounts["operating_cost"]),
                    "realized_high_water": self.control_state(connection)["realized_high_water"],
                    "business_high_water": self.control_state(connection)["business_high_water"],
                    "positions": [dict(row) for row in connection.execute("SELECT * FROM positions") if Decimal(row["quantity"]) > 0],
                    "control": self.control_state(connection), "ledger_valid": self.validate_ledger(connection)}

    def validate_ledger(self, connection: sqlite3.Connection | None = None) -> bool:
        if connection is None:
            with self._connect() as db:
                db.execute("BEGIN")
                return self.validate_ledger(db)
        balances: dict[tuple[str, str], Decimal] = {}
        for row in connection.execute("SELECT tx_id,asset,amount FROM ledger"):
            key = (row["tx_id"], row["asset"])
            balances[key] = balances.get(key, Decimal(0)) + Decimal(row["amount"])
        if any(value != 0 for value in balances.values()):
            return False
        reserve = sum((Decimal(row[0]) for row in connection.execute("SELECT reserved FROM intents")), Decimal(0))
        if reserve != self.balance("reserved", connection=connection) or self.balance("cash", connection=connection) < 0:
            return False
        inventory = Decimal(0)
        for row in connection.execute("SELECT asset_id,quantity,cost_basis FROM positions"):
            quantity, basis = Decimal(row["quantity"]), Decimal(row["cost_basis"])
            if quantity < 0 or basis < 0 or quantity != self.balance("shares", asset=row["asset_id"], connection=connection):
                return False
            if quantity == 0 and basis != 0:
                return False
            inventory += basis
        if inventory != self.balance("inventory_cost", connection=connection):
            return False
        for row in connection.execute("SELECT reserved,state FROM intents"):
            if Decimal(row["reserved"]) < 0 or (row["state"] in ("FILLED", "CANCELLED", "REJECTED", "EXPIRED") and Decimal(row["reserved"]) != 0):
                return False
        return True

    def outbox_pending(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM outbox WHERE delivered_at IS NULL ORDER BY sequence LIMIT ?", (limit,))]

    def acknowledge_outbox(self, event_id: str, *, now: datetime | None = None) -> None:
        with self.transaction() as connection:
            connection.execute("UPDATE outbox SET delivered_at=COALESCE(delivered_at,?) WHERE event_id=?", ((now or utc_now()).isoformat(), event_id))

    def outbox_failed(self, event_id: str, error_code: str) -> None:
        with self.transaction() as connection:
            connection.execute("UPDATE outbox SET attempts=attempts+1,last_error=? WHERE event_id=?", (error_code[:120], event_id))

    def acquire_executor(self, owner: str, *, now: datetime | None = None, ttl_seconds: int = 30) -> int:
        """Fencing for processes sharing this journal. Does NOT fence another host/signer."""
        if not owner or not 1 <= ttl_seconds <= 60:
            raise ValidationError("invalid executor lease")
        timestamp = require_utc(now or utc_now())
        with self.transaction() as connection:
            current = connection.execute("SELECT * FROM executor_lease WHERE singleton=1").fetchone()
            if current and current["owner"] != owner and datetime.fromisoformat(current["expires_at"]) > timestamp:
                raise JournalError("another executor owns this journal")
            epoch = current["epoch"] if current and current["owner"] == owner and datetime.fromisoformat(current["expires_at"]) > timestamp else (current["epoch"]+1 if current else 1)
            connection.execute("INSERT INTO executor_lease VALUES(1,?,?,?) ON CONFLICT(singleton) DO UPDATE SET owner=excluded.owner,epoch=excluded.epoch,expires_at=excluded.expires_at",
                               (owner, epoch, (timestamp+timedelta(seconds=ttl_seconds)).isoformat()))
            return int(epoch)

    def check_executor(self, owner: str, epoch: int, *, now: datetime | None = None,
                       connection: sqlite3.Connection | None = None) -> None:
        if connection is None:
            with self._connect() as db:
                self.check_executor(owner, epoch, now=now, connection=db)
                return
        row = connection.execute("SELECT * FROM executor_lease WHERE singleton=1").fetchone()
        if not row or row["owner"] != owner or row["epoch"] != epoch or datetime.fromisoformat(row["expires_at"]) <= require_utc(now or utc_now()):
            raise JournalError("executor fenced or expired")

    def release_executor(self, owner: str, epoch: int, *, now: datetime) -> None:
        with self.transaction() as connection:
            connection.execute("UPDATE executor_lease SET expires_at=? WHERE singleton=1 AND owner=? AND epoch=?",
                               (require_utc(now).isoformat(), owner, epoch))

    def backup(self, destination: str | Path) -> dict[str, str]:
        target = Path(destination)
        if target.resolve() == self.path.resolve():
            raise JournalError("backup destination must differ from journal")
        if target.exists():
            raise JournalError("backup destination already exists")
        target.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as source, sqlite3.connect(target) as destination_db:
            source.backup(destination_db)
        return {"path": str(target), "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}

    @staticmethod
    def inspect_backup(path: str | Path) -> dict[str, Any]:
        resolved = Path(path).resolve()
        with sqlite3.connect(resolved.as_uri()+"?mode=ro", uri=True) as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            version = connection.execute("SELECT version FROM schema_version").fetchone()[0]
            approvals = connection.execute("SELECT COUNT(*) FROM approvals WHERE consumed_by IS NOT NULL").fetchone()[0]
        return {"integrity": integrity, "schema_version": version, "consumed_approvals": approvals,
                "live_enabled": False, "restore_requires_reconciliation": True,
                "sha256": hashlib.sha256(resolved.read_bytes()).hexdigest()}
