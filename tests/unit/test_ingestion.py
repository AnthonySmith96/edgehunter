import asyncio
import json
from pathlib import Path

import httpx
import pytest

from edgehunter.ingestion.feeds import parse_feed
from edgehunter.ingestion.http import PublicHTTP, SourceError
from edgehunter.ingestion.provenance import DataEvent
from edgehunter.storage.archive import Archive
from edgehunter.venues.polymarket.public import BookState, PolymarketPublic


def test_private_url_and_redirect_and_oversize_are_blocked():
    async def scenario():
        transport = httpx.MockTransport(lambda request: httpx.Response(302, headers={"Location":"http://127.0.0.1"}))
        client = PublicHTTP({"example.org"}, transport=transport, interval=0)
        with pytest.raises(SourceError, match="URL_NOT_ALLOWED"):
            await client.get("http://127.0.0.1")
        with pytest.raises(SourceError, match="HTTP_302"):
            await client.get("https://example.org")
        await client.close()
        client = PublicHTTP({"example.org"}, max_bytes=5, interval=0,
                            transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"123456")))
        with pytest.raises(SourceError, match="RESPONSE_TOO_LARGE"):
            await client.get("https://example.org")
        await client.close()
    asyncio.run(scenario())


def test_book_identity_and_values_validated():
    async def scenario():
        client = PolymarketPublic(PublicHTTP({"clob.polymarket.com"}, interval=0,
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json={
                "asset_id":"different", "asks":[], "bids":[]}))))
        with pytest.raises(SourceError, match="BOOK_TOKEN_MISMATCH"):
            await client.book("123")
        await client.close()
    asyncio.run(scenario())


def test_feed_external_entities_and_syndication():
    with pytest.raises(SourceError):
        parse_feed(b'<!DOCTYPE x [<!ENTITY a SYSTEM "file:///secrets">]><x/>', "https://example.org", "test")
    content = b"<rss><channel><item><title>Rate</title><description>Hi</description></item><item><title>Rate</title><description>Hi</description></item></channel></rss>"
    events = parse_feed(content, "https://example.org", "test")
    assert len(events) == 1 and events[0].source_publish_time is None
    assert events[0].payload["expected_edge"] is None


def test_disconnect_requires_full_snapshot_and_preserves_provenance(tmp_path: Path):
    event = DataEvent.observed("test", "1", "https://example.org", {"synthetic": True})
    state = BookState()
    state.synchronize(event)
    assert state.valid
    state.on_message({"event_type":"tick_size_change"})
    assert not state.valid
    archive = Archive(tmp_path)
    target = archive.append([event.to_dict()], "test")
    assert target and archive.count() == 1
    manifest = json.loads(target.with_suffix(".manifest.json").read_text())
    assert manifest["records"] == 1
