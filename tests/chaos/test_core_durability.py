from __future__ import annotations

import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from edgehunter.domain import Book, Fill, Level, OrderPlan, OrderState
from edgehunter.risk.engine import HardEnvelope, RiskEngine
from edgehunter.storage.journal import Journal, JournalError

D = Decimal
NOW = datetime(2026, 1, 2, 12, tzinfo=timezone.utc)


def proposal() -> OrderPlan:
    return OrderPlan("yes", "market", "demo", D(10), D("0.5"), NOW,
                     NOW+timedelta(seconds=30), "contract", "v1")


def market() -> Book:
    return Book("yes", (Level(D("0.39"), D(100)),), (Level(D("0.40"), D(100)),), NOW, "contract", "v1")


def funded(path: Path, amount: str = "1000") -> Journal:
    journal = Journal(path)
    journal.deposit(D(amount), idempotency_key="initial", now=NOW)
    journal.renew_control_lease(now=NOW)
    return journal


@pytest.mark.parametrize("capital,limit,expected", [("7", "100", 1), ("1000", "15", 2)])
def test_concurrent_reservations_cannot_overspend_cash_or_budget(tmp_path: Path, capital: str, limit: str, expected: int) -> None:
    journal = funded(tmp_path / "concurrent.db", capital)
    approvals = [journal.approve_simulated(proposal(), now=NOW) for _ in range(8)]
    envelope = HardEnvelope(max_cluster_exposure=D(limit), max_total_exposure=D(limit))

    def worker(approval: str) -> str | None:
        try:
            return RiskEngine(Journal(journal.path), envelope).prepare(proposal(), market(), approval, now=NOW)
        except JournalError:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(worker, approvals))
    assert sum(value is not None for value in results) == expected
    assert journal.balance("reserved") == expected * proposal().cost_cap
    assert journal.validate_ledger()


def test_concurrent_approval_replay_consumes_one_reservation(tmp_path: Path) -> None:
    journal = funded(tmp_path / "replay.db")
    approval = journal.approve_simulated(proposal(), now=NOW)

    def worker(_: int) -> str:
        return RiskEngine(Journal(journal.path)).prepare(proposal(), market(), approval, now=NOW)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(worker, range(8)))
    assert len(set(results)) == 1
    assert journal.balance("reserved") == proposal().cost_cap


def test_disk_write_failure_rolls_back_intent_approval_and_reserve(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    journal = funded(tmp_path / "rollback.db")
    approval = journal.approve_simulated(proposal(), now=NOW)
    before = journal.report()

    def fail(*args: Any, **kwargs: Any) -> None:
        raise OSError("simulated disk full during transactional outbox")

    with monkeypatch.context() as patch:
        patch.setattr(journal, "_outbox", fail)
        with pytest.raises(OSError, match="disk full"):
            RiskEngine(journal).prepare(proposal(), market(), approval, now=NOW)
    assert journal.intents() == []
    assert journal.report() == before
    assert RiskEngine(journal).prepare(proposal(), market(), approval, now=NOW)


def test_process_crash_before_commit_does_not_create_money(tmp_path: Path) -> None:
    path = tmp_path / "crash.db"
    journal = funded(path)
    source = """
import os, sys
from datetime import datetime, timezone
from decimal import Decimal
from edgehunter.storage.journal import Journal
j = Journal(sys.argv[1])
with j.transaction() as db:
    j._post(db, 'uncommitted', 'TEST', [('cash','SIM_USD',Decimal('500')),('contributed_capital','SIM_USD',Decimal('-500'))], datetime.now(timezone.utc))
    os._exit(91)
"""
    result = subprocess.run([sys.executable, "-c", source, str(path)], timeout=15, check=False)
    assert result.returncode == 91
    restored = Journal(path)
    assert restored.balance("cash") == 1000
    assert restored.validate_ledger()
    assert all(event["event_id"] != "ledger:uncommitted" for event in restored.outbox_pending())
    assert journal.balance("cash") == 1000


def test_stale_executor_cannot_apply_fill_after_takeover(tmp_path: Path) -> None:
    journal = funded(tmp_path / "fence.db")
    approval = journal.approve_simulated(proposal(), now=NOW)
    intent = RiskEngine(journal).prepare(proposal(), market(), approval, now=NOW)
    epoch = journal.acquire_executor("first", now=NOW, ttl_seconds=1)
    journal.transition(intent, OrderState.SUBMITTING, now=NOW, executor=("first", epoch))
    with pytest.raises(JournalError, match="another executor"):
        Journal(journal.path).acquire_executor("second", now=NOW)
    takeover = NOW+timedelta(seconds=2)
    new_epoch = Journal(journal.path).acquire_executor("second", now=takeover)
    assert new_epoch > epoch
    with pytest.raises(JournalError, match="fenced"):
        journal.apply_fill(Fill("stale", intent, D(1), D("0.4"), D(0), takeover), executor=("first", epoch))
    assert not journal.has_fill("stale")
    journal.apply_fill(Fill("reconciled", intent, D(1), D("0.4"), D(0), takeover), executor=("second", new_epoch))
    assert journal.balance("shares", asset="yes") == 1


def test_expired_owner_name_cannot_reuse_old_epoch(tmp_path: Path) -> None:
    journal = funded(tmp_path / "epoch.db")
    first = journal.acquire_executor("same-name", now=NOW, ttl_seconds=1)
    second = journal.acquire_executor("same-name", now=NOW+timedelta(seconds=1))
    assert second > first
    with pytest.raises(JournalError, match="fenced"):
        journal.check_executor("same-name", first, now=NOW+timedelta(seconds=1))


def test_append_only_journal_rejects_history_edits(tmp_path: Path) -> None:
    import sqlite3

    journal = funded(tmp_path / "immutable.db")
    for statement in ("UPDATE ledger SET amount='0'", "DELETE FROM ledger", "UPDATE ledger_transactions SET kind='PROFIT'"):
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            with journal.transaction() as connection:
                connection.execute(statement)
    assert journal.validate_ledger()
