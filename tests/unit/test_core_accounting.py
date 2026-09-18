from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from edgehunter.domain import Book, Fill, Level, Mode, OrderPlan, OrderState, Side, ValidationError
from edgehunter.domain.models import decimal, money, proceeds
from edgehunter.execution.paper import PaperExecutor
from edgehunter.risk.engine import HardEnvelope, RiskEngine, RiskRejected
from edgehunter.storage.journal import Journal, JournalError
from edgehunter.treasury import TreasuryAction, TreasuryPolicy, recommend

D = Decimal
NOW = datetime(2026, 1, 2, 12, tzinfo=timezone.utc)


@pytest.fixture
def journal(tmp_path: Path) -> Journal:
    journal = Journal(tmp_path / "journal.sqlite")
    journal.deposit(D("1000"), idempotency_key="initial", now=NOW)
    journal.renew_control_lease(now=NOW)
    return journal


def plan(**changes: object) -> OrderPlan:
    values: dict[str, object] = {
        "asset_id": "yes", "market_id": "market", "strategy": "demo",
        "quantity": D("10"), "limit_price": D("0.50"), "created_at": NOW,
        "expires_at": NOW + timedelta(seconds=30), "contract_hash": "contract",
        "metadata_version": "v1",
    }
    values.update(changes)
    return OrderPlan(**values)  # type: ignore[arg-type]


def book(**changes: object) -> Book:
    values: dict[str, object] = {
        "asset_id": "yes", "bids": (Level(D("0.39"), D("100")),),
        "asks": (Level(D("0.40"), D("100")),), "observed_at": NOW,
        "contract_hash": "contract", "metadata_version": "v1",
    }
    values.update(changes)
    return Book(**values)  # type: ignore[arg-type]


def prepare(journal: Journal, order: OrderPlan | None = None, snapshot: Book | None = None) -> str:
    order = order or plan()
    approval = journal.approve_simulated(order, now=NOW)
    return RiskEngine(journal).prepare(order, snapshot or book(), approval, now=NOW)


def open_intent(journal: Journal, order: OrderPlan | None = None, snapshot: Book | None = None) -> str:
    intent_id = prepare(journal, order, snapshot)
    journal.transition(intent_id, OrderState.SUBMITTING, now=NOW)
    journal.transition(intent_id, OrderState.ACKNOWLEDGED, now=NOW)
    journal.transition(intent_id, OrderState.OPEN, now=NOW)
    return intent_id


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1", "1000000000001", "0.0000000000001", True, 0.1])
def test_invalid_economic_decimals(value: object) -> None:
    with pytest.raises(ValidationError):
        decimal(value)  # type: ignore[arg-type]


def test_internal_products_round_conservatively_without_precision_rejection() -> None:
    assert money(D("0.000000123456789123")) == D("0.000001")
    assert proceeds(D("0.000000999999999999")) == 0
    order = plan(fee_model="polymarket_curve", fee_rate=D("0.02"), limit_price=D("0.9"))
    assert order.cost_cap >= D("9") + order.fee_for(order.quantity, D("0.5"))


def test_balanced_realized_profit_includes_fees_not_deposits(journal: Journal) -> None:
    order = plan(fee_rate=D("0.02"))
    intent_id = prepare(journal, order)
    fills = PaperExecutor(journal, latency_ms=0, adverse_ticks=0).execute(intent_id, book(), now=NOW)
    assert sum((fill.quantity for fill in fills), D(0)) == 10
    assert journal.balance("cash") == D("995.92")
    assert journal.balance("reserved") == 0
    assert journal.report()["net_trading_pnl"] == "-0.080000"
    journal.settle("yes", payout_per_share=D("1"), settlement_id="winner", now=NOW)
    assert D(journal.report()["net_trading_pnl"]) == D("5.92")
    journal.deposit(D("500"), idempotency_key="external", now=NOW)
    assert D(journal.report()["net_trading_pnl"]) == D("5.92")
    assert D(journal.report()["realized_high_water"]) == D("5.92")
    journal.record_operating_cost(D("2"), idempotency_key="data", now=NOW)
    assert D(journal.report()["net_business_pnl"]) == D("3.92")
    assert journal.validate_ledger()
    assert not journal.settle("yes", payout_per_share=D("1"), settlement_id="winner", now=NOW)
    with pytest.raises(JournalError, match="conflict"):
        journal.settle("yes", payout_per_share=D("0"), settlement_id="winner", now=NOW)


def test_idempotency_checks_economics_and_not_available_cash(tmp_path: Path) -> None:
    journal = Journal(tmp_path / "account.db")
    assert journal.deposit(D("1"), idempotency_key="fund")
    assert not journal.deposit(D("1"), idempotency_key="fund")
    with pytest.raises(JournalError, match="different transaction"):
        journal.deposit(D("2"), idempotency_key="fund")
    journal.record_operating_cost(D("1"), idempotency_key="cost")
    journal.record_operating_cost(D("1"), idempotency_key="cost")
    assert journal.balance("cash") == 0
    assert journal.balance("operating_cost") == 1
    with pytest.raises(JournalError, match="different transaction"):
        journal.record_operating_cost(D("2"), idempotency_key="cost")


def test_cancel_waits_for_partial_fill_reconciliation(journal: Journal) -> None:
    intent_id = open_intent(journal)
    journal.apply_fill(Fill("first", intent_id, D("3"), D("0.40"), D(0), NOW))
    journal.transition(intent_id, OrderState.CANCEL_PENDING, now=NOW)
    journal.apply_fill(Fill("racing", intent_id, D("2"), D("0.40"), D(0), NOW))
    assert journal.intent(intent_id)["state"] == "CANCEL_PENDING"
    assert journal.balance("reserved") > 0
    with pytest.raises(JournalError, match="mismatch"):
        journal.confirm_cancel(intent_id, reconciled_filled_quantity=D("3"), now=NOW)
    assert journal.balance("reserved") > 0
    journal.confirm_cancel(intent_id, reconciled_filled_quantity=D("5"), now=NOW)
    assert journal.balance("reserved") == 0
    assert journal.balance("cash") == D("998")
    assert journal.balance("shares", asset="yes") == 5
    assert journal.validate_ledger()


def test_fill_replay_does_not_duplicate_positions_or_fees(journal: Journal) -> None:
    intent_id = open_intent(journal)
    fill = Fill("trade", intent_id, D("2"), D("0.40"), D(0), NOW)
    assert journal.apply_fill(fill)
    assert not Journal(journal.path).apply_fill(fill)
    with pytest.raises(JournalError, match="different economics"):
        journal.apply_fill(replace(fill, quantity=D("3")))
    assert journal.balance("shares", asset="yes") == 2


@pytest.mark.parametrize("quantity,price,fee", [("11", "0.40", "0"), ("1", "0.51", "0"), ("1", "0.40", "0.01")])
def test_unapproved_fills_rollback_every_account(journal: Journal, quantity: str, price: str, fee: str) -> None:
    intent_id = open_intent(journal)
    before = journal.report()
    with pytest.raises(JournalError):
        journal.apply_fill(Fill("invalid", intent_id, D(quantity), D(price), D(fee), NOW))
    assert journal.report() == before
    assert not journal.has_fill("invalid")


def test_no_credit_for_unsettled_or_real_fill() -> None:
    with pytest.raises(ValidationError, match="simulated"):
        Fill("real", "intent", D(1), D("0.5"), D(0), NOW, settlement_status="PENDING")


def test_sell_proceeds_round_down_and_update_high_water(journal: Journal) -> None:
    buy = plan(quantity=D("0.000003"), limit_price=D("0.40"))
    PaperExecutor(journal, latency_ms=0, adverse_ticks=0, depth_haircut=D(1)).execute(prepare(journal, buy), book(), now=NOW)
    sell = plan(quantity=D("0.000003"), limit_price=D("0.80"), side=Side.SELL, reduce_only=True)
    sell_book = book(bids=(Level(D("0.80"), D(1)),), asks=(Level(D("0.81"), D(1)),))
    PaperExecutor(journal, latency_ms=0, adverse_ticks=0, depth_haircut=D(1)).execute(prepare(journal, sell, sell_book), sell_book, now=NOW)
    assert journal.balance("cash") == D("1000.000000")
    assert D(journal.report()["net_trading_pnl"]) == 0
    assert journal.validate_ledger()


def test_sell_profit_high_water_survives_deposit(journal: Journal) -> None:
    executor = PaperExecutor(journal, latency_ms=0, adverse_ticks=0)
    executor.execute(prepare(journal), book(), now=NOW)
    sell = plan(limit_price=D("0.80"), side=Side.SELL, reduce_only=True)
    sell_book = book(bids=(Level(D("0.80"), D(100)),), asks=(Level(D("0.81"), D(100)),))
    executor.execute(prepare(journal, sell, sell_book), sell_book, now=NOW)
    assert D(journal.report()["realized_high_water"]) == 4
    journal.deposit(D(500), idempotency_key="extra", now=NOW)
    assert D(journal.report()["realized_high_water"]) == 4


def test_durable_partial_snapshot_replay_cannot_reuse_depth(journal: Journal) -> None:
    snapshot = book(asks=(Level(D("0.40"), D(5)),))
    first = prepare(journal, snapshot=snapshot)
    executor = PaperExecutor(journal, latency_ms=0, adverse_ticks=0)
    assert executor.execute(first, snapshot, now=NOW)[0].quantity == 4
    restored = Journal(journal.path)
    assert PaperExecutor(restored, latency_ms=0, adverse_ticks=0).execute(first, snapshot, now=NOW+timedelta(seconds=1)) == []
    second = prepare(restored, snapshot=snapshot)
    assert executor.execute(second, snapshot, now=NOW+timedelta(seconds=1)) == []
    assert restored.balance("shares", asset="yes") == 4


def test_unknown_retains_reserve_and_blocks_new_risk_after_restart(journal: Journal) -> None:
    intent_id = open_intent(journal)
    journal.transition(intent_id, OrderState.UNKNOWN, now=NOW)
    restored = Journal(journal.path)
    assert restored.balance("reserved") == plan().cost_cap
    with pytest.raises(JournalError, match="reconcile"):
        PaperExecutor(restored).execute(intent_id, book(), now=NOW)
    with pytest.raises(RiskRejected, match="UNRESOLVED_UNKNOWN"):
        prepare(restored)
    with pytest.raises(JournalError, match="reconciled"):
        restored.settle("yes", payout_per_share=D(1), settlement_id="premature", now=NOW)


def test_approval_bound_to_payload_and_cannot_be_replayed(journal: Journal) -> None:
    original = plan()
    approval = journal.approve_simulated(original, now=NOW)
    with pytest.raises(JournalError, match="plan changed"):
        RiskEngine(journal).prepare(replace(original, quantity=D(11)), book(), approval, now=NOW)
    intent = RiskEngine(journal).prepare(original, book(), approval, now=NOW)
    PaperExecutor(journal).cancel(intent, now=NOW)
    assert RiskEngine(Journal(journal.path)).prepare(original, book(), approval, now=NOW) == intent
    assert len(journal.intents()) == 1
    assert PaperExecutor(journal).execute(intent, book(), now=NOW) == []


def test_approval_expiry_is_strict(journal: Journal) -> None:
    original = plan()
    approval = journal.approve_simulated(original, now=NOW)
    with pytest.raises(JournalError, match="expired"):
        RiskEngine(journal).prepare(original, book(), approval, now=original.expires_at)
    assert journal.intents() == []
    assert journal.balance("reserved") == 0


@pytest.mark.parametrize("changes,reason", [
    ({"observed_at": NOW-timedelta(seconds=11)}, "STALE"),
    ({"observed_at": NOW+timedelta(seconds=1)}, "FUTURE"),
    ({"available_at": NOW+timedelta(seconds=1)}, "FUTURE"),
    ({"metadata_version": "v2"}, "METADATA"),
    ({"contract_hash": "changed"}, "CONTRACT"),
    ({"valid": False}, "INVALID"),
])
def test_market_data_gates(journal: Journal, changes: dict[str, object], reason: str) -> None:
    with pytest.raises(RiskRejected, match=reason):
        prepare(journal, snapshot=book(**changes))
    assert journal.balance("reserved") == 0


def test_live_mode_and_identity_never_authorized(journal: Journal) -> None:
    with pytest.raises(JournalError, match="real trading"):
        journal.approve_simulated(plan(mode=Mode.LIVE), now=NOW)
    with pytest.raises(ValueError, match="live"):
        HardEnvelope(modes=frozenset({Mode.LIVE}))
    with pytest.raises(JournalError, match="identity"):
        Journal(journal.path, collateral="OTHER")


def test_control_lease_checked_again_at_dispatch(journal: Journal) -> None:
    intent = prepare(journal)
    journal.renew_control_lease(now=NOW, ttl_seconds=1)
    with pytest.raises(JournalError, match="lease"):
        PaperExecutor(journal, latency_ms=1500).execute(intent, book(), now=NOW)
    assert journal.intent(intent)["state"] == "PREPARED"
    assert journal.balance("reserved") > 0


def test_loss_halt_persists_and_new_deposit_cannot_reset(journal: Journal) -> None:
    executor = PaperExecutor(journal, latency_ms=0, adverse_ticks=0)
    executor.execute(prepare(journal), book(), now=NOW)
    journal.settle("yes", payout_per_share=D(0), settlement_id="loss", now=NOW)
    journal.deposit(D(1000), idempotency_key="rescue", now=NOW)
    risk = RiskEngine(journal, HardEnvelope(max_realized_loss=D(3)))
    next_plan, next_book = plan(asset_id="next"), book(asset_id="next")
    approval = journal.approve_simulated(next_plan, now=NOW)
    with pytest.raises(RiskRejected, match="REALIZED_LOSS"):
        risk.prepare(next_plan, next_book, approval, now=NOW)
    assert Journal(journal.path).control_state()["halted"] == "true"
    with pytest.raises(RiskRejected, match="PERSISTED_HALT"):
        prepare(Journal(journal.path))


def test_reduce_only_reserves_shares_and_rejects_cluster_reclassification(journal: Journal) -> None:
    PaperExecutor(journal, latency_ms=0, adverse_ticks=0).execute(prepare(journal), book(), now=NOW)
    sell = plan(quantity=D(7), limit_price=D("0.39"), side=Side.SELL, reduce_only=True)
    prepare(journal, sell)
    with pytest.raises(RiskRejected, match="OVERSELL"):
        prepare(journal, sell)
    with pytest.raises(RiskRejected, match="POSITION_IDENTITY"):
        prepare(journal, plan(cluster_id="new-risk-budget"))


def test_backup_snapshot_preserves_consumed_approvals(journal: Journal, tmp_path: Path) -> None:
    prepare(journal)
    backup = journal.backup(tmp_path / "snapshot.db")
    inspected = Journal.inspect_backup(backup["path"])
    assert inspected["integrity"] == "ok"
    assert inspected["consumed_approvals"] == 1
    assert inspected["live_enabled"] is False
    assert Journal(backup["path"]).validate_ledger()
    with pytest.raises(JournalError, match="already exists"):
        journal.backup(backup["path"])


def test_control_command_consumed_once_and_immutable(journal: Journal) -> None:
    record = journal.propose_command("EMERGENCY_HALT", "nonce", NOW+timedelta(seconds=30), {"reason": "operator"}, now=NOW)
    approved = {**record, "human_decision": "APPROVED", "decided_by": "human-id", "decided_at": NOW.isoformat()}
    with pytest.raises(JournalError, match="designated human"):
        journal.consume_control_command(approved, expected_actor="other", now=NOW)
    with pytest.raises(JournalError, match="immutable"):
        journal.consume_control_command({**approved, "payload": {"reason": "changed"}}, expected_actor="human-id", now=NOW)
    assert journal.consume_control_command(approved, expected_actor="human-id", now=NOW)
    assert not Journal(journal.path).consume_control_command(approved, expected_actor="human-id", now=NOW)
    assert journal.control_state()["halted"] == "true"
    with pytest.raises(JournalError, match="invalid stop"):
        journal.propose_command("RESUME", "nonce2", NOW+timedelta(seconds=30), {}, now=NOW)


def test_expired_control_command_does_not_apply(journal: Journal) -> None:
    record = journal.propose_command("PAUSE_NEW_RISK", "nonce", NOW+timedelta(seconds=1), {}, now=NOW)
    approved = {**record, "human_decision": "APPROVED", "decided_by": "human", "decided_at": NOW.isoformat()}
    with pytest.raises(JournalError, match="expired"):
        journal.consume_control_command(approved, expected_actor="human", now=NOW+timedelta(seconds=1))
    assert journal.control_state()["paused"] == "false"


def test_ledger_detects_position_projection_corruption(journal: Journal) -> None:
    PaperExecutor(journal, latency_ms=0, adverse_ticks=0).execute(prepare(journal), book(), now=NOW)
    with journal.transaction() as connection:
        connection.execute("UPDATE positions SET quantity='999'")
    assert not journal.validate_ledger()
    with pytest.raises(RiskRejected, match="LEDGER_INVALID"):
        prepare(journal)
    assert journal.control_state()["halted"] == "true"


def test_research_requires_explicit_simulated_opt_in(journal: Journal) -> None:
    with pytest.raises(RiskRejected, match="MODEL_UNCALIBRATED"):
        prepare(journal, plan(calibrated=False))
    intent = prepare(journal, plan(calibrated=False, research_only=True, mode=Mode.PAPER))
    assert journal.intent(intent)["plan"].calibrated is False
    with pytest.raises(JournalError, match="real trading"):
        journal.approve_simulated(plan(mode=Mode.LIVE, calibrated=False, research_only=True), now=NOW)


def test_executor_can_reuse_daemon_lease_without_releasing_it(journal: Journal) -> None:
    intent = prepare(journal)
    epoch = journal.acquire_executor("daemon", now=NOW)
    fills = PaperExecutor(journal, latency_ms=0).execute(intent, book(), now=NOW, executor=("daemon", epoch))
    assert fills
    journal.check_executor("daemon", epoch, now=NOW+timedelta(seconds=1))
    with pytest.raises(JournalError, match="fenced"):
        PaperExecutor(journal).execute(intent, book(), now=NOW, executor=("intruder", epoch))


def test_acknowledged_crash_recovery_never_creates_second_order(journal: Journal) -> None:
    intent = prepare(journal)
    journal.transition(intent, OrderState.SUBMITTING, now=NOW)
    journal.transition(intent, OrderState.ACKNOWLEDGED, now=NOW)
    restored = Journal(journal.path)
    fills = PaperExecutor(restored, latency_ms=0, adverse_ticks=0).execute(intent, book(), now=NOW)
    assert sum((fill.quantity for fill in fills), D(0)) == 10
    assert len(restored.intents()) == 1
    assert restored.intent(intent)["state"] == "FILLED"


def test_timestamp_regressions_cannot_resolve_or_dispatch_future_order(journal: Journal) -> None:
    intent = open_intent(journal)
    later = NOW+timedelta(seconds=2)
    journal.transition(intent, OrderState.CANCEL_PENDING, now=later)
    with pytest.raises(JournalError, match="regressed"):
        journal.confirm_cancel(intent, reconciled_filled_quantity=D(0), now=NOW)
    with pytest.raises(JournalError, match="regressed"):
        journal.transition(intent, OrderState.UNKNOWN, now=NOW)
    with pytest.raises(JournalError, match="regressed"):
        PaperExecutor(journal).execute(intent, book(), now=NOW)
    # A late reconciliation may report an earlier fill, but cannot rewind state time.
    journal.apply_fill(Fill("late-report", intent, D(1), D("0.4"), D(0), NOW))
    assert journal.intent(intent)["updated_at"] == later.isoformat()


def test_settlement_cannot_precede_trade(journal: Journal) -> None:
    intent = prepare(journal)
    PaperExecutor(journal, latency_ms=250).execute(intent, book(), now=NOW)
    with pytest.raises(JournalError, match="precedes"):
        journal.settle("yes", payout_per_share=D(1), settlement_id="too-early", now=NOW)
    assert not journal.report()["net_trading_pnl"].startswith("5")
    assert journal.settle("yes", payout_per_share=D(1), settlement_id="resolved", now=NOW+timedelta(seconds=1))


def test_off_tick_fill_rejected_atomically(journal: Journal) -> None:
    intent = open_intent(journal)
    before = journal.report()
    with pytest.raises(JournalError, match="tick"):
        journal.apply_fill(Fill("bad-tick", intent, D(1), D("0.405"), D(0), NOW))
    assert journal.report() == before


def test_business_cost_halt_survives_new_deposit(journal: Journal) -> None:
    journal.record_operating_cost(D(25), idempotency_key="data", now=NOW)
    journal.deposit(D(1000), idempotency_key="rescue", now=NOW)
    with pytest.raises(RiskRejected, match="BUSINESS_LOSS"):
        prepare(journal)
    assert Journal(journal.path).control_state()["halted"] == "true"


def test_business_high_water_and_drawdown_exclude_deposits(journal: Journal) -> None:
    PaperExecutor(journal, latency_ms=0, adverse_ticks=0).execute(prepare(journal), book(), now=NOW)
    journal.settle("yes", payout_per_share=D(1), settlement_id="win", now=NOW)
    assert D(journal.report()["business_high_water"]) == 6
    journal.record_operating_cost(D(5), idempotency_key="operations", now=NOW)
    journal.deposit(D(1000), idempotency_key="additional", now=NOW)
    assert D(journal.report()["business_high_water"]) == 6
    proposal = plan(asset_id="next")
    approval = journal.approve_simulated(proposal, now=NOW)
    with pytest.raises(RiskRejected, match="BUSINESS_DRAWDOWN"):
        RiskEngine(journal, HardEnvelope(max_business_drawdown=D(5))).prepare(
            proposal, book(asset_id="next"), approval, now=NOW)


def test_control_date_precision_matches_pocketbase(journal: Journal) -> None:
    timestamp = NOW.replace(microsecond=123456)
    record = journal.propose_command("PAUSE_NEW_RISK", "ms-nonce", timestamp+timedelta(seconds=30), {}, now=timestamp)
    approved = {**record, "human_decision": "APPROVED", "decided_by": "human",
                "decided_at": timestamp.replace(microsecond=123000).isoformat(),
                "expires_at": (timestamp+timedelta(seconds=30)).isoformat(timespec="milliseconds")}
    assert journal.consume_control_command(approved, expected_actor="human", now=timestamp)


def test_treasury_profit_recommendation_is_deterministic_and_read_only(journal: Journal) -> None:
    PaperExecutor(journal, latency_ms=0, adverse_ticks=0).execute(prepare(journal), book(), now=NOW)
    journal.settle("yes", payout_per_share=D(1), settlement_id="win", now=NOW)
    journal.deposit(D(2000), idempotency_key="more", now=NOW)
    before = journal.report()
    policy = TreasuryPolicy(estimated_tax_reserve=D(0), estimated_transfer_fee=D(0))
    advice = recommend(journal, policy, now=NOW)
    assert advice == recommend(journal, policy, now=NOW)
    assert advice.action == TreasuryAction.WITHDRAW
    assert advice.amount == 3  # Only half the realized profit, never contributed capital.
    assert advice.contributed_capital == 3000
    assert advice.expires_at == NOW+timedelta(seconds=300)
    assert not advice.execution_allowed
    assert journal.report() == before


def test_treasury_does_not_invent_taxes_or_recover_losses_with_deposits(journal: Journal) -> None:
    advice = recommend(journal, now=NOW, validated_capacity=D(100), evidence_validated=True)
    assert advice.action == TreasuryAction.HOLD
    assert advice.estimated_tax_reserve is None
    journal.record_operating_cost(D(1), idempotency_key="cost", now=NOW)
    policy = TreasuryPolicy(estimated_tax_reserve=D(0), estimated_transfer_fee=D(0))
    advice = recommend(journal, policy, now=NOW, validated_capacity=D(100), evidence_validated=True)
    assert advice.action == TreasuryAction.HOLD
    assert "LOSSES" in advice.reasons[0]


def test_treasury_reinvestment_obeys_evidence_capital_and_commitments(journal: Journal) -> None:
    policy = TreasuryPolicy(authorized_capital=D(50), estimated_tax_reserve=D(0), estimated_transfer_fee=D(0))
    assert recommend(journal, policy, now=NOW, validated_capacity=D(100)).action == TreasuryAction.HOLD
    advice = recommend(journal, policy, now=NOW, validated_capacity=D(100), evidence_validated=True)
    assert advice.action == TreasuryAction.REINVEST
    assert advice.amount == 50
    prepare(journal)
    advice = recommend(journal, policy, now=NOW, validated_capacity=D(100), evidence_validated=True)
    assert advice.action == TreasuryAction.HOLD
    assert advice.reserved_collateral > 0


@pytest.mark.parametrize("breach", ["trade", "operating_cost"])
def test_prepared_dispatch_rechecks_original_loss_bounds_after_restart(journal: Journal, breach: str) -> None:
    order = plan()
    approval = journal.approve_simulated(order, now=NOW)
    tight = HardEnvelope(max_realized_loss=D(3), max_business_loss=D(3))
    pending = RiskEngine(journal, tight).prepare(order, book(), approval, now=NOW)
    if breach == "trade":
        other = plan(asset_id="other")
        PaperExecutor(journal, latency_ms=0, adverse_ticks=0).execute(
            prepare(journal, other, book(asset_id="other")), book(asset_id="other"), now=NOW)
        journal.settle("other", payout_per_share=D(0), settlement_id="loss", now=NOW)
    else:
        journal.record_operating_cost(D(4), idempotency_key="cost", now=NOW)
    journal.deposit(D(1000), idempotency_key="new-capital", now=NOW)
    restored = Journal(journal.path)
    # Reusing the approval with a looser RiskEngine cannot replace its original envelope.
    assert RiskEngine(restored).prepare(order, book(), approval, now=NOW) == pending
    with pytest.raises(JournalError, match="dispatch risk breach"):
        PaperExecutor(restored, latency_ms=0).execute(pending, book(), now=NOW)
    assert restored.intent(pending)["state"] == "PREPARED"
    assert restored.balance("reserved") == order.cost_cap
    assert Journal(journal.path).control_state()["halted"] == "true"
    assert not restored.report()["positions"]


def test_original_risk_envelope_is_immutable(journal: Journal) -> None:
    import sqlite3

    intent = prepare(journal)
    with journal.transaction() as connection:
        stored = connection.execute("SELECT plan_hash,envelope_hash,payload FROM intent_risk_bounds WHERE intent_id=?", (intent,)).fetchone()
        assert stored is not None and stored[0] == plan().plan_hash and len(stored[1]) == 64
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("UPDATE intent_risk_bounds SET payload='{}'")
