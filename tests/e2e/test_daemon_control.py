"""Actual loopback PocketBase and daemon; market source is explicitly a local fixture."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from edgehunter.ops import daemon
from edgehunter.ops.pocketbase import PocketBaseRuntime
from edgehunter.storage.journal import Journal

ROOT = Path(__file__).resolve().parents[2]


class LocalMarketFixture:
    async def clock(self):
        return {"status": "HEALTHY", "source": "LOCAL_E2E_FIXTURE"}

    async def markets(self, limit):
        return []

    async def geoblock(self):
        return {"blocked": False, "source": "LOCAL_E2E_FIXTURE"}

    async def close(self):
        pass


def test_authenticated_pause_applies_and_survives_daemon_restart(tmp_path, monkeypatch):
    runtime = PocketBaseRuntime(ROOT, data_dir=tmp_path / "dedicated-control")
    state = tmp_path / "runtime"
    monkeypatch.setattr(daemon, "PocketBaseRuntime", lambda root: runtime)
    monkeypatch.setattr(daemon, "PolymarketPublic", LocalMarketFixture)
    # The test drives STOP itself and must not replace pytest's process signal handlers.
    monkeypatch.setattr(daemon.signal, "signal", lambda *args: None)

    async def exercise():
        task = asyncio.create_task(daemon.run_daemon(tmp_path, mode="shadow", state_dir=state,
                                                    duration=15, interval=5))
        service = human = None
        try:
            async with asyncio.timeout(8):
                while not runtime.url:
                    if task.done():
                        await task
                    await asyncio.sleep(0.025)
                service = await runtime.daemon_client()
                human = await runtime.human_client(simulated=True)
                while True:
                    commands = (await service.list("commands", filter='kind = "PAUSE_NEW_RISK"'))["items"]
                    if commands:
                        break
                    if task.done():
                        await task
                    await asyncio.sleep(0.05)
            command = commands[0]
            assert command["simulated"] is True and command["human_decision"] == "PENDING"
            actor = await service.control_identity()
            assert json.loads((state / "control_identity.json").read_text())["actor_id"] == actor
            started = time.monotonic()
            approved = await human.update("commands", command["id"], {"human_decision": "APPROVED"})
            assert approved["decided_by"] == actor and approved["decided_at"]
            async with asyncio.timeout(6.5):
                while True:
                    applied = await service.get("commands", command["id"])
                    if applied["execution_status"] == "APPLIED":
                        break
                    if task.done():
                        await task
                    await asyncio.sleep(0.05)
            latency_ms = (time.monotonic() - started) * 1000
            assert latency_ms <= 6500
            journal = Journal(state / "journal.db")
            assert journal.control_state()["paused"] == "true"
            assert journal.control_state()["halted"] == "false"
            assert applied["execution_result"]["simulated"] is True
            assert not journal.consume_control_command(applied, expected_actor=actor)
            assert len((await service.list("approval_audit"))["items"]) == 1
            async with asyncio.timeout(2):
                while json.loads((state / "status.json").read_text())["state"] != "PAUSED":
                    await asyncio.sleep(0.025)
            before_restart = json.loads((state / "status.json").read_text())
            assert before_restart["task_progress"]["control"]
            assert before_restart["task_progress"]["ingestion"]
            assert before_restart["live_capability"] is False
            (state / "STOP").write_text("local E2E fixture stop", encoding="utf-8")
            async with asyncio.timeout(4):
                stopped = await task
            assert stopped["state"] == "STOPPED" and stopped["fills"] == 0
            await service.close()
            await human.close()
            service = human = None
            # A clean immediate restart must preserve both the pause and consumed command.
            async with asyncio.timeout(8):
                restarted = await daemon.run_daemon(tmp_path, mode="shadow", state_dir=state,
                                                    once=True, duration=4, interval=5)
            assert restarted["state"] == "STOPPED"
            assert Journal(state / "journal.db").control_state()["paused"] == "true"
            assert not Journal(state / "journal.db").consume_control_command(applied, expected_actor=actor)
        finally:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            if service:
                await service.close()
            if human:
                await human.close()
            runtime.stop()
    asyncio.run(exercise())
