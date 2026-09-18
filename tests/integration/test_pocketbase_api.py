"""Real loopback PocketBase process; no external services, wallets or emails."""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from edgehunter.ops.pocketbase import PocketBaseRuntime
from edgehunter.storage.pocketbase import PocketBaseClient

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def runtime(tmp_path):
    value = PocketBaseRuntime(ROOT, data_dir=tmp_path / "dedicated-control")
    # Provision exactly twice verifies migration history/idempotence on a fresh dedicated DB.
    value.provision()
    value.provision()
    value.start()
    yield value
    value.stop()


def request_payload(business_id="simulated-mandate-1"):
    payload = {"mode": "REPLAY", "wallet": "SIMULATED", "maximum": "25.00", "strategy": "structural@1"}
    return {"business_id": business_id, "kind": "ACTIVATE_MANDATE", "summary": "SIMULATED HUMAN APPROVAL - NO LIVE FUNDS",
            "payload": payload, "payload_hash": hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(),
            "nonce": business_id + "-nonce", "revision": 1, "amount": "25.00", "currency": "SIMULATED_USD",
            "expires_at": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
            "human_decision": "PENDING", "execution_status": "WAITING", "simulated": True}


def test_actual_api_permissions_approval_audit_and_projection(runtime):
    async def exercise():
        daemon = await runtime.daemon_client()
        human = await runtime.human_client(simulated=True)
        anonymous = PocketBaseClient(runtime.url)
        try:
            await daemon.refresh()
            await human.refresh(collection="_superusers")
            created = await daemon.create("approval_requests", request_payload())
            rid = created["id"]
            for patch in ({"human_decision": "APPROVED"}, {"decided_by": "forged-human"}, {"decided_at": datetime.now(UTC).isoformat()},
                          {"amount": "250000.00"}, {"nonce": "other"}, {"payload": {"maximum": "999999"}}, {"revision": 2}, {"execution_status": "APPLIED"}):
                with pytest.raises(httpx.HTTPStatusError):
                    await daemon.update("approval_requests", rid, patch)
            account = await daemon.refresh()
            with pytest.raises(httpx.HTTPStatusError):
                await daemon.update("service_accounts", account["id"], {"role": "human"})
            with pytest.raises(httpx.HTTPStatusError):
                await daemon.create("_superusers", {"email": "attacker@example.invalid", "password": "fake-long-enough-password"})
            with pytest.raises(httpx.HTTPStatusError):
                await daemon.create("approval_audit", {"business_id": "forged"})
            with pytest.raises(httpx.HTTPStatusError):
                await anonymous.get("approval_requests", rid)
            with pytest.raises(httpx.HTTPStatusError):
                await human.update("approval_requests", rid, {"payload": {"maximum": "999"}, "human_decision": "APPROVED"})
            approved = await human.update("approval_requests", rid, {"human_decision": "APPROVED"})
            assert approved["decided_by"] and approved["decided_at"]
            assert approved["human_decision"] == "APPROVED"
            applied = await daemon.update("approval_requests", rid, {"execution_status": "APPLIED", "execution_result": {"mode": "REPLAY", "simulated": True}})
            assert applied["human_decision"] == "APPROVED" and applied["execution_status"] == "APPLIED"
            with pytest.raises(httpx.HTTPStatusError):
                await human.update("approval_requests", rid, {"human_decision": "APPROVED"})
            audit = (await daemon.list("approval_audit"))["items"]
            assert len(audit) == 1 and audit[0]["actor_id"] == approved["decided_by"] and audit[0]["simulated"]
            with pytest.raises(httpx.HTTPStatusError):
                await human.update("approval_audit", audit[0]["id"], {"actor_id": "tampered"})
            first = await daemon.upsert_projection("daily_pnl", "day-1", {"net": "1.25"}, revision=1)
            second = await daemon.upsert_projection("daily_pnl", "day-1", {"net": "1.50"}, revision=2)
            stale = await daemon.upsert_projection("daily_pnl", "day-1", {"net": "999.00"}, revision=1)
            assert first["id"] == second["id"] == stale["id"]
            assert stale["payload"]["net"] == "1.50"
            assert len((await daemon.list("daily_pnl"))["items"]) == 1
            assert "token" not in daemon.record_url("approval_requests", rid)
        finally:
            await daemon.close()
            await human.close()
            await anonymous.close()
    asyncio.run(exercise())


def test_concurrent_human_decision_is_single_transition(runtime):
    async def exercise():
        daemon = await runtime.daemon_client()
        human = await runtime.human_client(simulated=True)
        try:
            created = await daemon.create("commands", request_payload("concurrent"))
            results = await asyncio.gather(*(human.update("commands", created["id"], {"human_decision": "APPROVED"}) for _ in range(2)), return_exceptions=True)
            assert sum(isinstance(item, dict) for item in results) == 1
            assert len((await daemon.list("approval_audit"))["items"]) == 1
        finally:
            await daemon.close()
            await human.close()
    asyncio.run(exercise())


def test_projection_survives_control_plane_restart(runtime):
    async def exercise():
        daemon = await runtime.daemon_client()
        try:
            first = await daemon.upsert_projection("orders", "durable-1", {"state": "UNKNOWN"})
            runtime.stop()
            with pytest.raises(httpx.HTTPError):
                await daemon.upsert_projection("orders", "durable-1", {"state": "FILLED"}, revision=2)
            runtime.start()
            recovered = await daemon.upsert_projection("orders", "durable-1", {"state": "FILLED"}, revision=2)
            assert recovered["id"] == first["id"]
            assert len((await daemon.list("orders"))["items"]) == 1
        finally:
            await daemon.close()
    asyncio.run(exercise())


def test_actor_separation_terminal_states_and_revocation(runtime):
    async def exercise():
        daemon = await runtime.daemon_client()
        human = await runtime.human_client(simulated=True)
        other = PocketBaseClient(runtime.url)
        try:
            with pytest.raises(httpx.HTTPStatusError):
                await human.create("commands", request_payload("human-proposal"))
            with pytest.raises(httpx.HTTPStatusError):
                await daemon.create("commands", {**request_payload("forged-create"), "human_decision": "APPROVED"})
            created = await daemon.create("commands", request_payload("terminal"))
            await human.create("_superusers", {"email": "other@example.invalid", "password": "synthetic-unrelated-owner-password", "passwordConfirm": "synthetic-unrelated-owner-password"})
            await other.login("other@example.invalid", "synthetic-unrelated-owner-password", collection="_superusers")
            with pytest.raises(httpx.HTTPStatusError):
                await other.update("commands", created["id"], {"human_decision": "APPROVED"})
            await human.update("commands", created["id"], {"human_decision": "APPROVED"})
            await daemon.update("commands", created["id"], {"execution_status": "APPLIED", "execution_result": {"simulated": True}})
            # Terminal retransmission is idempotent, but neither state nor result can regress.
            await daemon.update("commands", created["id"], {"execution_status": "APPLIED", "execution_result": {"simulated": True}})
            for patch in ({"execution_status": "WAITING"}, {"execution_result": {"simulated": False}}):
                with pytest.raises(httpx.HTTPStatusError):
                    await daemon.update("commands", created["id"], patch)
            with pytest.raises(httpx.HTTPStatusError):
                await human.request("DELETE", f"/api/collections/commands/records/{created['id']}")
            invalid = await daemon.create("config_requests", request_payload("invalidated"))
            await daemon.update("config_requests", invalid["id"], {"execution_status": "INVALIDATED"})
            with pytest.raises(httpx.HTTPStatusError):
                await human.update("config_requests", invalid["id"], {"human_decision": "APPROVED"})
            identity = await daemon.refresh()
            await human.update("service_accounts", identity["id"], {"revoked": True})
            with pytest.raises(httpx.HTTPStatusError):
                await daemon.create("commands", request_payload("revoked"))
            with pytest.raises(httpx.HTTPStatusError):
                await daemon.refresh()
        finally:
            await daemon.close()
            await human.close()
            await other.close()
    asyncio.run(exercise())


def test_expired_decision_cannot_be_approved(runtime):
    async def exercise():
        daemon = await runtime.daemon_client()
        human = await runtime.human_client(simulated=True)
        try:
            payload = request_payload("expired")
            payload["expires_at"] = (datetime.now(UTC) + timedelta(seconds=1)).isoformat()
            created = await daemon.create("approval_requests", payload)
            await asyncio.sleep(1.1)
            with pytest.raises(httpx.HTTPStatusError):
                await human.update("approval_requests", created["id"], {"human_decision": "APPROVED"})
            assert not (await daemon.list("approval_audit"))["items"]
        finally:
            await daemon.close()
            await human.close()
    asyncio.run(exercise())


def test_backup_restore_retains_approved_audit_without_reapproval(runtime, tmp_path):
    async def seed():
        daemon = await runtime.daemon_client()
        human = await runtime.human_client(simulated=True)
        try:
            created = await daemon.create("commands", request_payload("preserved-approval"))
            await human.update("commands", created["id"], {"human_decision": "APPROVED"})
            await daemon.update("commands", created["id"], {"execution_status": "APPLIED"})
            return created["id"]
        finally:
            await daemon.close()
            await human.close()
    record_id = asyncio.run(seed())
    with pytest.raises(RuntimeError, match="Stop"):
        runtime.backup(tmp_path / "running-backup")
    runtime.stop()
    start = time.monotonic()
    snapshot = tmp_path / "snapshot"
    report = runtime.backup(snapshot)
    assert not report["credentials_included"]
    assert not (snapshot / "credentials.json").exists()
    isolated = tmp_path / "isolated-restore"
    assert runtime.restore(snapshot, isolated)["status"] == "DRY_RUN_VERIFIED"
    assert not isolated.exists()
    runtime.restore(snapshot, isolated, dry_run=False, credentials_source=runtime.credentials_path)
    assert (isolated / "RESTORE_REQUIRES_RECONCILIATION.json").exists()
    restored = PocketBaseRuntime(ROOT, data_dir=isolated)
    restored.start()
    async def inspect():
        daemon = await restored.daemon_client()
        human = await restored.human_client(simulated=True)
        try:
            record = await daemon.get("commands", record_id)
            assert record["execution_status"] == "APPLIED"
            assert len((await daemon.list("approval_audit"))["items"]) == 1
            with pytest.raises(httpx.HTTPStatusError):
                await human.update("commands", record_id, {"human_decision": "APPROVED"})
        finally:
            await daemon.close()
            await human.close()
    try:
        asyncio.run(inspect())
    finally:
        restored.stop()
    assert time.monotonic() - start < 60  # Local measured recovery exercise, not a remote RTO guarantee.
    with pytest.raises(FileExistsError):
        runtime.restore(snapshot, isolated, dry_run=False)
    with (snapshot / "data.db").open("ab") as stream:
        stream.write(b"tamper")
    with pytest.raises(ValueError, match="checksum"):
        runtime.inspect_backup(snapshot)


def test_record_url_and_unencrypted_remote_origin_rejected():
    with pytest.raises(ValueError, match="HTTPS"):
        PocketBaseClient("http://example.invalid")


def test_control_identity_uses_only_authenticated_active_daemon(runtime):
    async def exercise():
        daemon = await runtime.daemon_client()
        human = await runtime.human_client(simulated=True)
        anonymous = PocketBaseClient(runtime.url)
        try:
            identity = await human.refresh(collection="_superusers")
            assert await daemon.control_identity() == identity["id"]
            response = await daemon.request("GET", "/api/edgehunter/control-identity")
            assert set(response) == {"actor_id"}
            for client in (anonymous, human):
                with pytest.raises(httpx.HTTPStatusError):
                    await client.control_identity()
            service = await daemon.refresh()
            await human.update("service_accounts", service["id"], {"revoked": True})
            with pytest.raises(httpx.HTTPStatusError):
                await daemon.control_identity()
        finally:
            await daemon.close()
            await human.close()
            await anonymous.close()
    asyncio.run(exercise())
