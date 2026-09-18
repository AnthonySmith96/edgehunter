from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from edgehunter.ingestion.http import PublicHTTP, SourceError
from edgehunter.ingestion.provenance import DataEvent, canonical_hash

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"


def decode_list(value: Any) -> list[Any]:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, list):
        raise SourceError("INVALID_LIST")
    return value


def decimal_value(value: Any) -> Decimal:
    result = Decimal(str(value))
    if not result.is_finite() or result < 0:
        raise SourceError("INVALID_NONNEGATIVE_DECIMAL")
    return result


class PolymarketPublic:
    def __init__(self, http: PublicHTTP | None = None) -> None:
        self.http = http or PublicHTTP({"gamma-api.polymarket.com", "clob.polymarket.com",
                                       "polymarket.com", "data-api.polymarket.com"})

    async def close(self) -> None:
        await self.http.close()

    async def markets(self, limit: int = 20, offset: int = 0) -> list[dict[str, Any]]:
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("Invalid pagination bound")
        raw = await self.http.json(f"{GAMMA}/markets", {
            "active": "true", "closed": "false", "limit": limit, "offset": offset,
            "order": "volume24hr", "ascending": "false",
        })
        if not isinstance(raw, list):
            raise SourceError("INVALID_MARKETS_RESPONSE")
        return [m for m in raw if isinstance(m, dict) and m.get("enableOrderBook")
                and m.get("acceptingOrders") and not m.get("closed")]

    async def market(self, market_id: str) -> dict[str, Any]:
        if not market_id.isdecimal():
            raise SourceError("INVALID_MARKET_ID")
        raw = await self.http.json(f"{GAMMA}/markets/{market_id}")
        if not isinstance(raw, dict):
            raise SourceError("INVALID_MARKET")
        return raw

    async def book(self, token_id: str) -> DataEvent:
        if not token_id.isdecimal():
            raise SourceError("INVALID_TOKEN_ID")
        payload = await self.http.json(f"{CLOB}/book", {"token_id": token_id})
        if not isinstance(payload, dict) or payload.get("asset_id") != token_id:
            raise SourceError("BOOK_TOKEN_MISMATCH")
        for side in ("bids", "asks"):
            if not isinstance(payload.get(side), list):
                raise SourceError("MALFORMED_BOOK")
            for level in payload[side]:
                if not isinstance(level, dict) or not 0 < decimal_value(level["price"]) < 1:
                    raise SourceError("INVALID_BOOK_PRICE")
                if decimal_value(level["size"]) <= 0:
                    raise SourceError("INVALID_BOOK_SIZE")
        event_time = None
        if payload.get("timestamp"):
            event_time = datetime.fromtimestamp(int(payload["timestamp"]) / 1000, UTC).isoformat()
        return DataEvent.observed("polymarket-clob", token_id, f"{CLOB}/book", payload,
                                  event_time=event_time)

    async def fees(self, token_id: str) -> dict[str, Any]:
        raw = await self.http.json(f"{CLOB}/fee-rate", {"token_id": token_id})
        if not isinstance(raw, dict) or "base_fee" not in raw:
            raise SourceError("FEE_METADATA_UNAVAILABLE")
        decimal_value(raw["base_fee"])
        return raw

    async def geoblock(self) -> dict[str, Any]:
        raw = await self.http.json("https://polymarket.com/api/geoblock")
        if not isinstance(raw, dict) or type(raw.get("blocked")) is not bool:
            raise SourceError("INVALID_GEOBLOCK")
        return {"blocked": raw["blocked"], "country": raw.get("country"),
                "region": raw.get("region"), "eligibility": "NOT_LEGAL_OR_IDENTITY_VERIFICATION"}

    async def clock(self) -> dict[str, Any]:
        before = datetime.now(UTC).timestamp()
        raw = await self.http.json(f"{CLOB}/time")
        after = datetime.now(UTC).timestamp()
        remote = float(raw)
        skew = remote - (before + after) / 2
        return {"roundtrip_seconds": after - before, "skew_seconds": skew,
                "status": "HEALTHY" if abs(skew) < 5 else "CLOCK_SKEW"}

    async def snapshot(self, market: dict[str, Any]) -> dict[str, Any]:
        tokens = decode_list(market.get("clobTokenIds", []))
        outcomes = decode_list(market.get("outcomes", []))
        if len(tokens) != 2 or len(outcomes) != 2:
            raise SourceError("BINARY_ONLY")
        books = await asyncio.gather(*(self.book(str(token)) for token in tokens))
        fees = await asyncio.gather(*(self.fees(str(token)) for token in tokens))
        contract = {key: market.get(key) for key in (
            "id", "conditionId", "question", "description", "resolutionSource", "outcomes",
            "clobTokenIds", "endDate", "negRisk", "orderPriceMinTickSize", "orderMinSize", "feesEnabled",
        )}
        return {"market_id": market["id"], "question": market.get("question"),
                "contract": contract, "contract_hash": canonical_hash(contract),
                "books": [b.to_dict() for b in books], "fees": fees,
                "outcomes": outcomes, "synthetic": False,
                "tags": sorted({str(tag.get("slug", "")) for event in market.get("events", [])
                                for tag in event.get("tags", [])}),
                "execution_class": "STRUCTURAL_NONATOMIC"}


class BookState:
    """A reconnect/gap/tick change invalidates the book until a full REST snapshot."""

    def __init__(self) -> None:
        self.valid = False
        self.reason = "NOT_INITIALIZED"
        self.snapshot: DataEvent | None = None

    def invalidate(self, reason: str) -> None:
        self.valid, self.reason = False, reason

    def synchronize(self, snapshot: DataEvent) -> None:
        self.snapshot, self.valid, self.reason = snapshot, True, "SNAPSHOT"

    def on_message(self, message: dict[str, Any]) -> None:
        # The public feed does not guarantee a gap-free sequence counter.
        # Any incremental update forces a complete REST reconstruction in this build.
        self.invalidate(str(message.get("event_type", "UNKNOWN_UPDATE")))
