from __future__ import annotations

import json
import uuid
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from edgehunter.domain import Book, Level, Mode, OrderPlan
from edgehunter.execution import PaperExecutor
from edgehunter.ingestion.provenance import canonical_hash
from edgehunter.notifications import FileSink, NotificationOutbox
from edgehunter.ops.common import json_ready, write_json
from edgehunter.ops.pocketbase import PocketBaseRuntime
from edgehunter.risk import HardEnvelope, RiskEngine
from edgehunter.storage.journal import Journal
from edgehunter.strategies import Quote, StrategyContext, StrategyRegistry

D = Decimal


async def run_demo(root: Path, *, pocketbase: bool = True) -> dict[str, Any]:
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
    directory = root / ".local" / "demo" / run_id
    directory.mkdir(parents=True)
    journal = Journal(directory / "journal.db")
    journal.deposit(D("1000"), idempotency_key="demo-capital")
    runtime = PocketBaseRuntime(root, data_dir=directory / "pocketbase") if pocketbase else None
    client = human = None
    notifications = NotificationOutbox(directory / "notifications.db")
    trades: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    replay_start = datetime.now(UTC)
    try:
        if runtime:
            runtime.start()
            client, human = await runtime.daemon_client(), await runtime.human_client(simulated=True)
        for index in range(12):
            now = replay_start + timedelta(hours=2*index)
            journal.renew_control_lease(now=now)
            # A fixture intentionally containing both executable and rejected opportunities.
            prices = (D("0.43"), D("0.49")) if index % 3 else (D("0.53"), D("0.51"))
            quotes = tuple(Quote(f"demo-{index}-{side}", price-D("0.02"), price,
                                 D("20"), D("20"), int(now.timestamp()), D("0.05"))
                           for side, price in enumerate(prices))
            context = StrategyContext(int(now.timestamp()), f"demo-{index}", f"event-{index}", quotes,
                {"contracts_verified": True, "exhaustive_scenarios": True,
                 "payoff_matrix": [[1, 0], [0, 1], ["0.5", "0.5"]],
                 "conversion_cost_per_share": "0.005", "unwind_loss_per_share": "0.02",
                 "adverse_selection_per_share": "0.02", "atomic_execution_verified": False,
                 "unwind_route_verified": True}, (f"synthetic-fixture-{index}",), int(now.timestamp()))
            decision = StrategyRegistry().evaluate("structural", context)
            decisions.append(asdict(decision))
            if decision.action != "PROPOSE":
                continue
            plans = [OrderPlan(q.token, f"demo-{index}", "structural", D("5"), q.ask+D("0.01"),
                      now, now+timedelta(seconds=60), f"synthetic-contract-{index}", "fixture-v1",
                      fee_rate=D("0.05"), fee_model="polymarket_curve", cluster_id=f"demo-{index}",
                      mode=Mode.REPLAY, min_quantity=D("5")) for q in quotes]
            pb_request = None
            if client and human:
                payload = {"mode": "REPLAY", "simulated": True, "plans": [p.plan_hash for p in plans]}
                pb_request = await client.create("approval_requests", {
                    "business_id": f"demo-{run_id}-{index}", "kind": "ONE_SHOT_TRADE",
                    "summary": "DEMO: aprobación humana simulada, fondos ficticios",
                    "payload": payload, "payload_hash": canonical_hash(payload),
                    "nonce": uuid.uuid4().hex, "revision": 1, "amount": "10", "currency": "SIM_USD",
                    "expires_at": (now+timedelta(minutes=5)).isoformat(),
                    "human_decision": "PENDING", "execution_status": "WAITING", "simulated": True})
                await human.update("approval_requests", pb_request["id"], {"human_decision": "APPROVED"})
                approved = await client.get("approval_requests", pb_request["id"])
                if (approved["human_decision"] != "APPROVED" or not approved["decided_by"]
                        or approved["payload_hash"] != canonical_hash(payload)):
                    raise RuntimeError("Demo approval did not survive authenticated readback")
                for stage in ("VALIDATING", "ACCEPTED", "EXECUTING"):
                    await client.update("approval_requests", pb_request["id"], {"execution_status": stage})
            for plan, quote in zip(plans, quotes, strict=True):
                book = Book(plan.asset_id, (Level(quote.bid, D("20")),), (Level(quote.ask, D("20")),),
                            now, plan.contract_hash, plan.metadata_version, available_at=now)
                aid = journal.approve_simulated(plan, now=now)
                intent = RiskEngine(journal, HardEnvelope()).prepare(plan, book, aid, now=now)
                fills = PaperExecutor(journal).execute(intent, book, now=now)
                trades.extend({**asdict(fill), "market_id": plan.market_id, "synthetic": True} for fill in fills)
            # Resolution is a separate future simulator event, not available to the entry strategy.
            resolution_time = now+timedelta(hours=1)
            for side, plan in enumerate(plans):
                journal.settle(plan.asset_id, payout_per_share=D(int(side == index % 2)),
                               settlement_id=f"demo-settlement-{index}-{side}", now=resolution_time)
            journal.record_operating_cost(D("0.025"), idempotency_key=f"demo-settlement-cost-{index}", now=resolution_time)
            if client and pb_request:
                await client.update("approval_requests", pb_request["id"], {
                    "execution_status": "APPLIED", "execution_result": {"simulated": True, "ledger": journal.report()}})
        result = {"run_id": run_id, "data_kind": "SYNTHETIC_ENGINEERING_FIXTURE",
                  "financial_validation": "NOT_REAL_MARKET_PROFITABILITY",
                  "mode": "REPLAY", "real_money_enabled": False, "report": journal.report(),
                  "proposals": sum(d["action"] == "PROPOSE" for d in decisions),
                  "rejections": sum(d["action"] == "ABSTAIN" for d in decisions),
                  "fills": len(trades), "decisions": decisions,
                  "pocketbase_verified": client is not None, "directory": str(directory)}
        if client:
            await client.upsert_projection("daily_pnl", run_id, json_ready(result), status="REPLAY_SYNTHETIC")
        notifications.enqueue(run_id, "DAILY_DIGEST", "EdgeHunter: demo con fondos ficticios",
                              json.dumps(result["report"], indent=2))
        result["notification_sink"] = await notifications.deliver(FileSink(directory / "mail"))
        write_json(directory / "trades.json", trades)
        write_json(directory / "result.json", result)
        write_json(root / "reports" / "demo.json", result)
        return result
    finally:
        notifications.close()
        if client:
            await client.close()
        if human:
            await human.close()
        if runtime:
            runtime.stop()
