from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb


class Archive:
    """Immutable, checksummed Parquet batches. No shared DuckDB database writer."""

    def __init__(self, root: Path, *, max_bytes: int = 512_000_000) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max_bytes
        self.used_bytes = sum(p.stat().st_size for p in root.rglob("*") if p.is_file())

    def append(self, events: list[dict[str, Any]], source: str) -> Path | None:
        if not events:
            return None
        estimated = len(json.dumps(events, default=str).encode())
        if self.used_bytes + estimated > self.max_bytes:
            raise RuntimeError("ARCHIVE_QUOTA_EXHAUSTED; preserve existing evidence")
        if not source.replace("-", "").replace("_", "").isalnum():
            raise ValueError("Invalid source partition")
        folder = self.root / source / datetime.now(UTC).date().isoformat()
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"{uuid.uuid4().hex}.parquet"
        temporary = target.with_suffix(".tmp")
        connection = duckdb.connect(":memory:")
        try:
            connection.execute("SET memory_limit='128MB'")
            connection.execute("SET threads=1")
            connection.execute("CREATE TABLE events(event_id VARCHAR, available_at VARCHAR, payload VARCHAR)")
            connection.executemany("INSERT INTO events VALUES (?, ?, ?)", [
                (str(e.get("event_id", uuid.uuid4().hex)), str(e.get("available_at", datetime.now(UTC).isoformat())),
                 json.dumps(e, sort_keys=True, allow_nan=False, default=str)) for e in events
            ])
            connection.execute("COPY events TO ? (FORMAT PARQUET, COMPRESSION ZSTD)", [str(temporary)])
            os.replace(temporary, target)
        finally:
            connection.close()
        manifest = {"schema_version": 1, "file": target.name, "records": len(events),
                    "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                    "created_at": datetime.now(UTC).isoformat(), "source": source}
        target.with_suffix(".manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        self.used_bytes += target.stat().st_size + target.with_suffix(".manifest.json").stat().st_size
        return target

    def count(self) -> int:
        total = 0
        with duckdb.connect(":memory:") as connection:
            for file in self.root.rglob("*.parquet"):
                row = connection.execute("SELECT count(*) FROM read_parquet(?)", [str(file)]).fetchone()
                if row:
                    total += int(row[0])
        return total
