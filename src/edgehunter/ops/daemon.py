from __future__ import annotations

import asyncio
import json
import os
import signal
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from edgehunter.ingestion.feeds import RadarFeeds
from edgehunter.ingestion.http import SourceError
from edgehunter.ingestion.provenance import DataEvent, utcnow
from edgehunter.notifications import FileSink, NotificationOutbox
from edgehunter.ops.common import json_ready, write_json
from edgehunter.ops.lock import InstanceLock
from edgehunter.ops.paper_candidate import PaperCandidate, resolution_payout
from edgehunter.ops.pocketbase import PocketBaseRuntime
from edgehunter.ops.policy import load_policy
from edgehunter.storage.archive import Archive
from edgehunter.storage.journal import Journal
from edgehunter.storage.pocketbase import PocketBaseClient
from edgehunter.strategies import Quote, StrategyContext, StrategyRegistry
from edgehunter.venues.polymarket import PolymarketPublic


def analyze_snapshot(snapshot: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    now = now or utcnow()
    quotes: list[Quote] = []
    reasons = []
    for index, event in enumerate(snapshot["books"]):
        raw = event["payload"]
        asks = sorted(raw["asks"], key=lambda row: Decimal(row["price"]))
        bids = sorted(raw["bids"], key=lambda row: Decimal(row["price"]), reverse=True)
        if not asks or not bids:
            return {"action": "ABSTAIN", "reason": "EMPTY_BOOK", "market_id": snapshot["market_id"]}
        # The base_fee endpoint and documentation may describe different schedules.
        # Only an explicitly fee-free market is unambiguous for this paper adapter.
        fee_rate = None
        if snapshot["contract"].get("feesEnabled") is False and snapshot["fees"][index]["base_fee"] == 0:
            fee_rate = Decimal(0)
        else:
            reasons.append("FEE_SCHEDULE_NOT_CERTIFIED")
        observed = datetime.fromisoformat(event["event_time"]) if event["event_time"] else None
        if observed is None:
            return {"action": "ABSTAIN", "reason": "UNKNOWN_BOOK_TIME", "market_id": snapshot["market_id"]}
        quotes.append(Quote(raw["asset_id"], Decimal(bids[0]["price"]), Decimal(asks[0]["price"]),
                            Decimal(asks[0]["size"]), Decimal(bids[0]["size"]), int(observed.timestamp()),
                            fee_rate, Decimal(raw["tick_size"]), Decimal(raw["min_order_size"])))
    context = StrategyContext(int(now.timestamp()), str(snapshot["market_id"]),
        str(snapshot["contract"]["conditionId"]), tuple(quotes),
        {"contracts_verified": False, "exhaustive_scenarios": False,
         "reason": "Contract scenario matrix and non-atomic unwind require certification"},
        tuple(event["event_id"] for event in snapshot["books"]),
        int(min(datetime.fromisoformat(event["available_at"]).timestamp() for event in snapshot["books"])),
        ttl_seconds=15)
    result = asdict(StrategyRegistry().evaluate("structural", context))
    result.update({"market_id": snapshot["market_id"], "question": snapshot["question"],
                   "best_ask_sum": str(sum((q.ask for q in quotes), Decimal(0))),
                   "source": "REAL_PUBLIC_BOOK", "additional_blockers": sorted(set(reasons)),
                   "synthetic": False})
    return dict(json_ready(result))


async def probe_sources(root: Path) -> dict[str, Any]:
    polymarket, radar = PolymarketPublic(), RadarFeeds()
    result: dict[str, Any] = {"as_of": utcnow().isoformat(), "live_eligible": False, "sources": {}}
    try:
        calls = {"polymarket_catalog": polymarket.markets(3), "polymarket_clock": polymarket.clock(),
                 "polymarket_geoblock": polymarket.geoblock(), "coinbase": radar.crypto(),
                 "federal_reserve": radar.news()}
        values = await asyncio.gather(*calls.values(), return_exceptions=True)
        for name, value in zip(calls, values, strict=True):
            if isinstance(value, BaseException):
                result["sources"][name] = {"status": "BLOCKED", "error": type(value).__name__}
            elif isinstance(value, list):
                result["sources"][name] = {"status": "HEALTHY", "records": len(value)}
            elif isinstance(value, dict):
                result["sources"][name] = value
            elif isinstance(value, DataEvent):
                result["sources"][name] = {"status": "HEALTHY", "event": value.to_dict()}
            else:
                result["sources"][name] = {"status": "BLOCKED", "error": "UNRECOGNIZED_RESPONSE"}
        result["sources"].update({name: {"status": "ACCESS_BLOCKED", "reason": reason} for name, reason in {
            "alphavantage": "No project key or data entitlement", "weathernext": "No approved account or byte budget",
            "jev": "No project key or spending budget", "social_derivatives": "No licensed provider"}.items()})
        write_json(root / "reports" / "sources.json", result)
        return result
    finally:
        await polymarket.close()
        await radar.close()


async def run_daemon(root: Path, *, mode: str, duration: float | None = None, once: bool = False,
                     state_dir: Path | None = None, pocketbase_url: str | None = None,
                     daemon_credentials: Path | None = None, market_limit: int = 12,
                     interval: float = 30) -> dict[str, Any]:
    policy = load_policy(root)
    if mode == "paper" and not policy["paper_trading_enabled"]:
        raise ValueError("Paper trading disabled in config/paper.json")
    state_dir = state_dir or root / ".local" / "runtime"
    state_dir.mkdir(parents=True, exist_ok=True)
    # Acquire before creating journals, depositing capital or touching provider state.
    with InstanceLock(state_dir / "instance.lock"):
        return await _run_daemon(root, mode=mode, duration=duration, once=once,
            state_dir=state_dir, pocketbase_url=pocketbase_url,
            daemon_credentials=daemon_credentials, market_limit=market_limit, interval=interval)


async def _run_daemon(root: Path, *, mode: str, duration: float | None = None, once: bool = False,
                     state_dir: Path | None = None, pocketbase_url: str | None = None,
                     daemon_credentials: Path | None = None, market_limit: int = 12,
                     interval: float = 30) -> dict[str, Any]:
    if mode not in {"paper", "shadow"}:
        raise ValueError("Only paper and shadow modes are available")
    if not 1 <= market_limit <= 100 or interval < 5 or (duration is not None and duration <= 0):
        raise ValueError("Invalid resource or duration limit")
    state_dir = state_dir or root / ".local" / "runtime"
    state_dir.mkdir(parents=True, exist_ok=True)
    stop = asyncio.Event()
    started = time.monotonic()
    journal = Journal(state_dir / "journal.db")
    if mode == "paper":
        journal.deposit(Decimal("1000"), idempotency_key="initial-paper-capital")
    runtime = None if pocketbase_url else PocketBaseRuntime(root)
    client: PocketBaseClient | None = None
    source = PolymarketPublic()
    archive = Archive(state_dir / "archive")
    notifications = NotificationOutbox(state_dir / "notifications.db")
    owner = uuid.uuid4().hex
    executor_epoch: int | None = None
    candidate: PaperCandidate | None = None
    candidate_path = root / "data" / "research_v2" / "frozen_model.json"
    candidate_error = None
    if candidate_path.exists():
        try:
            candidate = PaperCandidate.load(journal, candidate_path)
        except (ValueError, KeyError, TypeError, OSError) as exc:
            candidate_error = type(exc).__name__
    expected_actor = ""
    status: dict[str, Any] = {"schema_version": 1, "mode": mode.upper(), "pid": os.getpid(),
        "launch_id": os.environ.get("EDGEHUNTER_LAUNCH_ID"),
        "started_at": utcnow().isoformat(), "live_capability": False, "state": "STARTING",
        "cycles": 0, "markets_observed": 0, "fills": 0, "strategies": StrategyRegistry().health(),
        "execution_gate": "UNPROVEN_RESEARCH_PAPER" if candidate else "COLLECTING; no fitted candidate",
        "state_dir": str(state_dir), "errors": {}, "task_progress": {}}

    if candidate_error:
        status["errors"]["candidate"] = candidate_error

    def stop_signal(signum: int, frame: Any) -> None:
        stop.set()

    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, stop_signal)

    def save_status() -> None:
        status["as_of"] = utcnow().isoformat()
        status["ledger"] = journal.report()
        control = status["ledger"]["control"]
        progress = status["task_progress"]
        fresh_tasks = all(name in progress and 0 <= (utcnow()-datetime.fromisoformat(progress[name])).total_seconds() < 90
                          for name in ("control", "ingestion"))
        status["ready_for_new_paper_risk"] = bool(mode == "paper" and candidate and fresh_tasks
            and status["state"] == "OBSERVING" and status.get("clock", {}).get("status") == "HEALTHY"
            and control["halted"] == "false" and control["paused"] == "false" and not status["errors"])
        status["financial_edge_proven"] = False
        status["uptime_seconds"] = round(time.monotonic()-started, 2)
        write_json(state_dir / "status.json", status)

    async def wait_stop(seconds: float) -> None:
        try:
            await asyncio.wait_for(stop.wait(), timeout=seconds)
        except TimeoutError:
            pass

    async def control_loop() -> None:
        assert client is not None
        while not stop.is_set():
            try:
                await client.refresh()
                actor = await client.control_identity()
                if actor != expected_actor:
                    journal.halt("CONTROL_IDENTITY_CHANGED")
                    raise RuntimeError("Pinned control identity changed")
                journal.renew_control_lease(now=utcnow(), ttl_seconds=60)
                status["task_progress"]["control"] = utcnow().isoformat()
                status["errors"].pop("control", None)
                approved = await client.list("commands", filter='human_decision = "APPROVED" && execution_status != "APPLIED"')
                for record in approved["items"]:
                    try:
                        journal.consume_control_command(record, expected_actor=expected_actor)
                        await client.update("commands", record["id"], {
                            "execution_status": "APPLIED", "execution_result": {
                                "control": journal.control_state(), "simulated": True}})
                    except Exception as exc:
                        status["errors"]["command"] = type(exc).__name__
                control = journal.control_state()
                if control["paused"] == "true" or control["halted"] == "true":
                    status["state"] = "PAUSED"
                await client.upsert_projection("health", "daemon", {**status, "as_of": utcnow().isoformat()},
                    revision=time.time_ns() // 1_000_000, status=status["state"])
                # PB events are merely projections of durable financial journal entries.
                for event in journal.outbox_pending(30):
                    try:
                        payload = json.loads(event["payload"])
                        if event["topic"] == "control_proposal":
                            found = await client.list("commands", filter="business_id = "+json.dumps(payload["business_id"]))
                            if not found["items"]:
                                await client.create("commands", payload)
                        else:
                            await client.upsert_projection("cashflows", event["event_id"],
                                {"topic": event["topic"], "event": payload}, revision=1, status="SIMULATED")
                        journal.acknowledge_outbox(event["event_id"])
                    except Exception as exc:
                        journal.outbox_failed(event["event_id"], type(exc).__name__)
                        raise
            except Exception as exc:
                status["errors"]["control"] = type(exc).__name__
                notifications.enqueue(f"control-{utcnow().date()}", "CRITICAL", "EdgeHunter: control no disponible",
                                      "Se conserva el estado; el lease vencido bloquea riesgo nuevo.")
            save_status()
            await notifications.deliver(FileSink(state_dir / "mail"))
            await wait_stop(5)

    async def market_loop() -> None:
        assert client is not None
        while not stop.is_set():
            decisions: list[dict[str, Any]] = []
            try:
                clock = await source.clock()
                status["clock"] = clock
                markets = await source.markets(market_limit)
                for position in journal.report()["positions"]:
                    resolved = await source.market(str(position["market_id"]))
                    payoff = resolution_payout(resolved, position["asset_id"])
                    if payoff is not None:
                        await asyncio.to_thread(archive.append, [{"available_at": utcnow().isoformat(),
                            "source_url": f"https://gamma-api.polymarket.com/markets/{position['market_id']}",
                            "payload": resolved}], "resolutions")
                        journal.settle(position["asset_id"], payout_per_share=payoff,
                            settlement_id="public-resolution-"+position["asset_id"])
                for market in markets:
                    if stop.is_set():
                        break
                    try:
                        snapshot = await source.snapshot(market)
                        await asyncio.to_thread(archive.append, snapshot["books"], "polymarket")
                        decision = analyze_snapshot(snapshot)
                        if candidate:
                            epoch = journal.acquire_executor(owner, ttl_seconds=30)
                            try:
                                research_decision = candidate.evaluate(snapshot, now=utcnow(),
                                    clock_healthy=clock["status"] == "HEALTHY", execute=mode == "paper",
                                    executor=(owner, epoch))
                                decisions.append(json_ready(research_decision))
                                status["fills"] += research_decision.get("fill_count", 0)
                            except Exception as exc:
                                decisions.append({"market_id": market["id"], "strategy": "research_forecasting",
                                    "action": "ABSTAIN", "reason": type(exc).__name__})
                        decisions.append(decision)
                        status["markets_observed"] += 1
                    except (SourceError, ValueError, KeyError) as exc:
                        decisions.append({"market_id": market.get("id"), "action": "ABSTAIN",
                                          "reason": type(exc).__name__})
                status["cycles"] += 1
                status["last_decisions"] = decisions
                status["errors"].pop("ingestion", None)
                status["task_progress"]["ingestion"] = utcnow().isoformat()
                control = journal.control_state()
                status["state"] = ("PAUSED" if control["halted"] == "true" or control["paused"] == "true"
                                   else "OBSERVING" if clock["status"] == "HEALTHY" else "DEGRADED_CLOCK")
                write_json(state_dir / "last_decisions.json", decisions)
                for decision in decisions:
                    try:
                        await client.upsert_projection("decisions", f"{decision.get('strategy', 'source')}-{decision['market_id']}", decision,
                            revision=time.time_ns() // 1_000_000, status=str(decision["action"]))
                    except Exception as exc:
                        status["errors"]["projection"] = type(exc).__name__
            except Exception as exc:
                status["errors"]["ingestion"] = type(exc).__name__
                status["state"] = "DEGRADED_SOURCE"
            save_status()
            if once:
                stop.set()
            await wait_stop(interval)

    async def lifecycle_loop() -> None:
        nonlocal executor_epoch
        while not stop.is_set():
            if (state_dir / "STOP").exists():
                (state_dir / "STOP").unlink()
                stop.set()
            if duration is not None and time.monotonic()-started >= duration:
                stop.set()
            executor_epoch = journal.acquire_executor(owner, ttl_seconds=30)
            if shutil_free(state_dir) < 500_000_000:
                journal.halt("LOW_DISK_SPACE")
                stop.set()
            await wait_stop(1)

    try:
        if runtime:
            if (runtime.state / "RESTORE_REQUIRES_RECONCILIATION.json").exists():
                journal.halt("RESTORE_REQUIRES_RECONCILIATION")
            status["admin_url"] = runtime.start() + "/_/"
            status["credentials_file"] = str(runtime.credentials_path)
            client = await runtime.daemon_client()
        else:
            if not pocketbase_url or not daemon_credentials:
                raise ValueError("External PocketBase requires --daemon-credentials file")
            credentials = json.loads(daemon_credentials.read_text(encoding="utf-8"))
            if set(credentials) != {"email", "password"}:
                raise ValueError("Daemon file must contain only email and password")
            client = PocketBaseClient(pocketbase_url)
            await client.login(credentials["email"], credentials["password"])
            status["admin_url"] = pocketbase_url + "/_/"
        status["geoblock"] = await source.geoblock()
        expected_actor = await client.control_identity()
        pin_path = state_dir / "control_identity.json"
        if pin_path.exists():
            pin = json.loads(pin_path.read_text(encoding="utf-8"))
            if pin["actor_id"] != expected_actor:
                journal.halt("CONTROL_IDENTITY_CHANGED")
                raise RuntimeError("Control identity differs from the pinned actor")
        else:
            write_json(pin_path, {"actor_id": expected_actor, "pinned_at": utcnow().isoformat()})
        for kind in ("PAUSE_NEW_RISK", "EMERGENCY_HALT"):
            journal.propose_command(kind, uuid.uuid4().hex, utcnow()+timedelta(days=1),
                {"scope": "edgehunter", "mode": mode.upper(), "action": kind})
        status["state"] = "OBSERVING"
        save_status()
        async with asyncio.TaskGroup() as group:
            group.create_task(control_loop(), name="control")
            group.create_task(market_loop(), name="ingestion")
            group.create_task(lifecycle_loop(), name="lifecycle")
    finally:
        stop.set()
        if executor_epoch is not None:
            journal.release_executor(owner, executor_epoch, now=utcnow())
        status["state"] = "STOPPED"
        save_status()
        await source.close()
        if client:
            await client.close()
        if runtime:
            runtime.stop()
        notifications.close()
    return status


def shutil_free(path: Path) -> int:
    import shutil
    return shutil.disk_usage(path).free
