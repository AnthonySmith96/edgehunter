from __future__ import annotations

import csv
import sqlite3
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from edgehunter.storage.journal import Journal


def period_report(journal: Journal, *, period: str, now: datetime | None = None,
                  timezone: str = "America/Mexico_City") -> dict[str, Any]:
    if period not in {"daily", "all"}:
        raise ValueError("period must be daily or all")
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("report time must be timezone aware")
    local = now.astimezone(ZoneInfo(timezone))
    start = datetime.combine(local.date(), time.min, tzinfo=local.tzinfo).astimezone(UTC)
    end = (datetime.combine(local.date(), time.min, tzinfo=local.tzinfo)+timedelta(days=1)).astimezone(UTC)
    accounts = {key: Decimal(0) for key in ("realized_income", "fees", "operating_cost", "contributed_capital")}
    with sqlite3.connect(journal.path.resolve().as_uri()+"?mode=ro", uri=True) as db:
        for account, amount, timestamp in db.execute(
            "SELECT l.account,l.amount,t.created_at FROM ledger l JOIN ledger_transactions t ON t.tx_id=l.tx_id"):
            when = datetime.fromisoformat(timestamp)
            if account in accounts and (period == "all" or start <= when < min(end, now)):
                accounts[account] += Decimal(amount)
    net = -accounts["realized_income"]-accounts["fees"]
    return {"period": period, "timezone": timezone, "as_of": now.isoformat(),
            "start_utc": start.isoformat() if period == "daily" else None,
            "end_utc": end.isoformat() if period == "daily" else None,
            "realized_pnl_before_fees": str(-accounts["realized_income"]), "fees": str(accounts["fees"]),
            "net_trading_pnl": str(net), "operating_cost": str(accounts["operating_cost"]),
            "net_business_pnl": str(net-accounts["operating_cost"]),
            "deposits": str(-accounts["contributed_capital"]), "collateral": journal.collateral,
            "valuation": "REALIZED_ONLY; positions not marked as guaranteed cash", "journal": journal.report()}


def export_ledger(journal: Journal, destination: Path) -> int:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(journal.path.resolve().as_uri()+"?mode=ro", uri=True) as db:
        rows = db.execute("SELECT l.id,l.tx_id,t.kind,t.created_at,l.account,l.asset,l.amount "
                          "FROM ledger l JOIN ledger_transactions t ON t.tx_id=l.tx_id ORDER BY l.id").fetchall()
    with destination.open("w", encoding="utf-8", newline="") as output:
        writer = csv.writer(output)
        writer.writerow(["entry_id", "transaction_id", "kind", "created_at_utc", "account", "asset", "amount"])
        writer.writerows(rows)
    return len(rows)
