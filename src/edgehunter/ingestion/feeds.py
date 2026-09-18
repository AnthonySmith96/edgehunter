from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit

from edgehunter.ingestion.http import PublicHTTP, SourceError
from edgehunter.ingestion.provenance import DataEvent, canonical_hash
from edgehunter.venues.polymarket.public import decimal_value


def parse_feed(content: bytes, source_url: str, source_id: str) -> list[DataEvent]:
    if len(content) > 1_000_000 or b"<!ENTITY" in content.upper() or b"<!DOCTYPE" in content.upper():
        raise SourceError("UNSAFE_OR_OVERSIZE_XML")
    root = ET.fromstring(content)
    atom = "{http://www.w3.org/2005/Atom}"
    nodes = root.findall(".//item") + root.findall(f".//{atom}entry")
    events: list[DataEvent] = []
    seen: set[str] = set()
    for entry in nodes[:100]:
        title = entry.findtext("title") or entry.findtext(f"{atom}title") or ""
        description = entry.findtext("description") or entry.findtext(f"{atom}summary") or ""
        # Strip HTML as text only; extracted URLs are never fetched.
        description = re.sub(r"<[^>]*>", "", description)[:8000]
        identity = canonical_hash([" ".join(title.lower().split()), " ".join(description.lower().split())])
        if identity in seen:
            continue
        seen.add(identity)
        link = entry.findtext("link")
        atom_link = entry.find(f"{atom}link")
        if not link and atom_link is not None:
            link = atom_link.get("href")
        if link and urlsplit(link).scheme not in ("https", "http"):
            link = None
        published = entry.findtext("pubDate") or entry.findtext(f"{atom}published")
        publish_time = None
        if published:
            try:
                parsed = (datetime.fromisoformat(published.replace("Z", "+00:00"))
                          if "T" in published else parsedate_to_datetime(published))
                if parsed.tzinfo:
                    publish_time = parsed.astimezone(UTC).isoformat()
            except (ValueError, TypeError):
                pass
        events.append(DataEvent.observed(source_id, identity, source_url,
                      {"title": title[:1000], "text": description, "link": link,
                       "expected_edge": None, "action": "INFORMATION_ONLY"},
                      source_publish_time=publish_time))
    return events


class RadarFeeds:
    def __init__(self, http: PublicHTTP | None = None) -> None:
        self.http = http or PublicHTTP({"api.exchange.coinbase.com", "www.federalreserve.gov",
                                       "www.alphavantage.co", "data.sec.gov", "api.weather.gov"}, max_bytes=1_000_000)

    async def close(self) -> None:
        await self.http.close()

    async def crypto(self, product: str = "BTC-USD") -> DataEvent:
        if not re.fullmatch(r"[A-Z0-9]{2,10}-USD", product):
            raise SourceError("INVALID_PRODUCT")
        url = f"https://api.exchange.coinbase.com/products/{product}/ticker"
        raw = await self.http.json(url)
        if not isinstance(raw, dict):
            raise SourceError("INVALID_QUOTE")
        for key in ("price", "bid", "ask", "volume"):
            decimal_value(raw[key])
        return DataEvent.observed("coinbase", product, url, raw, event_time=raw.get("time"))

    async def news(self) -> list[DataEvent]:
        url = "https://www.federalreserve.gov/feeds/press_all.xml"
        return parse_feed(await self.http.get(url), url, "federal-reserve-rss")

    async def equity(self, symbol: str, *, api_key: str, access_authorized: bool) -> DataEvent:
        if not api_key or not access_authorized:
            raise SourceError("EQUITY_ACCESS_BLOCKED")
        if not re.fullmatch(r"[A-Z0-9.\-]{1,12}", symbol):
            raise SourceError("INVALID_SYMBOL")
        url = "https://www.alphavantage.co/query"
        raw = await self.http.json(url, {"function": "GLOBAL_QUOTE", "symbol": symbol, "apikey": api_key})
        quote = raw.get("Global Quote") if isinstance(raw, dict) else None
        if not quote or "05. price" not in quote:
            raise SourceError("EQUITY_QUOTA_OR_COVERAGE_BLOCKED")
        decimal_value(quote["05. price"])
        return DataEvent.observed("alphavantage", symbol, url,
                   {"quote": quote, "delay": "END_OF_DAY_OR_ENTITLEMENT_DEPENDENT",
                    "intraday_edge_eligible": False})

    async def sec_filings(self, cik: str, *, user_agent: str) -> DataEvent:
        if not cik.isdigit() or len(cik) > 10 or "@" not in user_agent:
            raise SourceError("SEC_IDENTIFIED_CLIENT_REQUIRED")
        # Identification is supplied by project configuration, never inferred.
        self.http.client.headers["User-Agent"] = user_agent
        url = f"https://data.sec.gov/submissions/CIK{cik.zfill(10)}.json"
        raw: Any = await self.http.json(url)
        if not isinstance(raw, dict) or "filings" not in raw:
            raise SourceError("INVALID_SEC_RESPONSE")
        return DataEvent.observed("sec", cik, url, raw)

    async def station(self, station: str) -> DataEvent:
        if not re.fullmatch(r"[A-Z0-9]{3,8}", station):
            raise SourceError("INVALID_STATION")
        url = f"https://api.weather.gov/stations/{station}/observations/latest"
        raw = await self.http.json(url)
        if not isinstance(raw, dict) or "properties" not in raw:
            raise SourceError("INVALID_STATION_OBSERVATION")
        return DataEvent.observed("noaa", station, url, raw,
                                  event_time=raw["properties"].get("timestamp"))
