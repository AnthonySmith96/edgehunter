from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from edgehunter.notifications import BrevoTransport, FileSink, NotificationOutbox


def test_sink_deduplicates_and_escapes_hostile_text(tmp_path):
    outbox = NotificationOutbox(tmp_path / "outbox.db")
    key = outbox.enqueue("cause:1", "CRITICAL", "Revisar", '<script>steal("TEST_SECRET")</script>', secrets_to_redact=("TEST_SECRET",))
    assert outbox.enqueue("cause:1", "CRITICAL", "duplicate", "duplicate") == key
    assert asyncio.run(outbox.deliver(FileSink(tmp_path / "sink")))["delivered"] == 1
    assert asyncio.run(outbox.deliver(FileSink(tmp_path / "sink")))["delivered"] == 0
    contents = (tmp_path / "sink" / f"{key}.json").read_text(encoding="utf-8")
    assert "TEST_SECRET" not in contents
    assert "&lt;script&gt;" in json.loads(contents)["html"]
    outbox.acknowledge(key)
    assert outbox.records()[0]["acknowledged"] == 1 and outbox.records()[0]["resolved"] == 0
    outbox.close()


def test_failure_retry_survives_restart_and_sanitizes_errors(tmp_path):
    class Broken:
        async def send(self, message):
            raise RuntimeError("API-KEY-MUST-NOT-LEAK")
    outbox = NotificationOutbox(tmp_path / "outbox.db")
    outbox.enqueue("retry", "ACTION_REQUIRED", "Review", "Review")
    assert asyncio.run(outbox.deliver(Broken(), now=100))["failed"] == 1
    assert outbox.records()[0]["last_error"] == "RuntimeError"
    outbox.close()
    outbox = NotificationOutbox(tmp_path / "outbox.db")
    assert asyncio.run(outbox.deliver(FileSink(tmp_path / "sink"), now=103))["delivered"] == 1
    outbox.close()


def test_brevo_adapter_distinguishes_accepted_from_delivered(tmp_path):
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(201, json={"messageId": "fake-provider-id"})
    async def exercise():
        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        adapter = BrevoTransport(api_key="SYNTHETIC", sender="sender@example.invalid", recipient="recipient@example.invalid",
                                 sending_authorized=True, recipient_confirmed=True, client=client)
        box = NotificationOutbox(tmp_path / "outbox.db")
        box.enqueue("brevo-mock", "CRITICAL", "Test", "Synthetic contract test")
        assert (await box.deliver(adapter))["sent"] == 1
        assert box.records()[0]["state"] == "SENT"
        with pytest.raises(PermissionError):
            box.delivery_event("fake-provider-id", "DELIVERED", authenticated=False)
        box.delivery_event("fake-provider-id", "BOUNCED", authenticated=True)
        assert box.records()[0]["state"] == "BOUNCED"
        assert len(requests) == 1
        await client.aclose()
        box.close()
    asyncio.run(exercise())


def test_brevo_not_authorized_and_link_tokens_rejected(tmp_path):
    with pytest.raises(PermissionError):
        BrevoTransport(api_key="fake", sender="a", recipient="b")
    box = NotificationOutbox(tmp_path / "outbox.db")
    with pytest.raises(ValueError):
        box.enqueue("token", "CRITICAL", "Test", "Body", record_url="https://example.invalid/?token=SECRET")
    with pytest.raises(ValueError):
        box.enqueue("fragment", "CRITICAL", "Test", "Body", record_url="https://example.invalid/#access_token=SECRET")
    box.close()


@pytest.mark.parametrize("failure", ["timeout", "malformed", "missing-id"])
def test_ambiguous_delivery_never_automatically_retries(tmp_path, failure):
    calls = []
    def respond(request):
        calls.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("synthetic-provider-secret", request=request)
        if failure == "malformed":
            return httpx.Response(201, text="invalid JSON")
        return httpx.Response(201, json={"status": "accepted"})
    async def exercise():
        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        adapter = BrevoTransport(api_key="SYNTHETIC", sender="a@example.invalid", recipient="b@example.invalid",
                                 sending_authorized=True, recipient_confirmed=True, client=client)
        box = NotificationOutbox(tmp_path / "outbox.db")
        box.enqueue("ambiguous", "CRITICAL", "Review", "Uncertain transport evidence")
        try:
            assert (await box.deliver(adapter))["unknown"] == 1
            assert box.records()[0]["state"] == "UNKNOWN"
            assert box.records()[0]["last_error"] == "DeliveryUnknown"
            assert (await box.deliver(adapter, now=1e12))["sent"] == 0
            assert len(calls) == 1
        finally:
            box.close()
            await adapter.close()
    asyncio.run(exercise())


def test_sink_rejects_arbitrary_paths(tmp_path):
    with pytest.raises(ValueError):
        asyncio.run(FileSink(tmp_path / "sink").send({"message_key": "../outside"}))
    assert not (tmp_path / "outside.json").exists()


def test_crash_during_sending_is_recovered_as_unknown(tmp_path):
    box = NotificationOutbox(tmp_path / "outbox.db")
    key = box.enqueue("crash", "CRITICAL", "Review", "Crash during delivery")
    with box.connection:
        box.connection.execute("UPDATE notifications SET state='SENDING' WHERE message_key=?", (key,))
    box.close()
    recovered = NotificationOutbox(tmp_path / "outbox.db")
    assert recovered.records()[0]["state"] == "UNKNOWN"
    assert asyncio.run(recovered.deliver(FileSink(tmp_path / "sink")))["delivered"] == 0
    recovered.close()
