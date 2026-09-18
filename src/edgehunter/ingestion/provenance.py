from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any


def utcnow() -> datetime:
    return datetime.now(UTC)


def canonical_hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                     default=str, allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class DataEvent:
    event_id: str
    source_id: str
    source_record_id: str
    source_url: str
    content_hash: str
    event_time: str | None
    source_publish_time: str | None
    first_seen_at: str
    ingested_at: str
    processed_at: str
    available_at: str
    payload: dict[str, Any]
    temporal_quality: str = "OBSERVED_NOW"
    schema_version: int = 1
    provider_version: str = "public-rest-observed"

    @classmethod
    def observed(cls, source: str, record: str, url: str, payload: dict[str, Any],
                 event_time: str | None = None, source_publish_time: str | None = None) -> DataEvent:
        seen = utcnow().isoformat()
        digest = canonical_hash(payload)
        return cls(canonical_hash([source, record, digest, seen]), source, record, url,
                   digest, event_time, source_publish_time, seen, seen, seen, seen, payload)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
