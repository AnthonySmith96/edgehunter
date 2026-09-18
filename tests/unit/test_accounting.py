import csv
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from edgehunter.ops.accounting import export_ledger, period_report
from edgehunter.ops.policy import PAPER_POLICY, load_policy
from edgehunter.storage.journal import Journal


def test_daily_report_uses_mexico_day_and_never_calls_deposit_profit(tmp_path):
    journal = Journal(tmp_path / "journal.db")
    now = datetime(2026, 9, 17, 7, tzinfo=UTC)
    journal.deposit(Decimal("1000"), idempotency_key="before-midnight", now=now-timedelta(hours=2))
    journal.deposit(Decimal("20"), idempotency_key="today", now=now-timedelta(minutes=30))
    journal.record_operating_cost(Decimal("2"), idempotency_key="yesterday", now=now-timedelta(hours=2))
    journal.record_operating_cost(Decimal("3"), idempotency_key="today-cost", now=now-timedelta(minutes=15))
    today = period_report(journal, period="daily", now=now)
    assert today["start_utc"] == "2026-09-17T06:00:00+00:00"
    assert Decimal(today["deposits"]) == 20
    assert Decimal(today["net_trading_pnl"]) == 0
    assert Decimal(today["net_business_pnl"]) == -3
    total = period_report(journal, period="all", now=now)
    assert Decimal(total["deposits"]) == 1020
    assert Decimal(total["net_business_pnl"]) == -5
    destination = tmp_path / "ledger.csv"
    assert export_ledger(journal, destination) == 8
    with destination.open(newline="", encoding="utf-8") as source:
        assert len(list(csv.DictReader(source))) == 8


def test_policy_refuses_widening_or_live_activation(tmp_path):
    import json
    (tmp_path / "config").mkdir()
    path = tmp_path / "config" / "paper.json"
    for change in ({"live_trading_enabled": True}, {"max_loss_per_idea": "100.00"},
                   {"paper_trading_enabled": "true"}):
        path.write_text(json.dumps({**PAPER_POLICY, **change}), encoding="utf-8")
        with pytest.raises(ValueError):
            load_policy(tmp_path)
    path.write_text(json.dumps({**PAPER_POLICY, "paper_trading_enabled": False}), encoding="utf-8")
    assert load_policy(tmp_path)["paper_trading_enabled"] is False
