from __future__ import annotations

import asyncio
import hashlib
import json
import re
import ssl
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import httpx

from edgehunter.ingestion.http import PublicHTTP, SourceError
from edgehunter.research.btc_lag import realized_vol_per_second
from edgehunter.venues.polymarket.public import decode_list

GAMMA_EVENTS = "https://gamma-api.polymarket.com/events/keyset"
DATA_TRADES = "https://data-api.polymarket.com/v2/trades"
CLOB_BATCH_HISTORY = "https://clob.polymarket.com/batch-prices-history"
COINBASE_CANDLES = "https://api.exchange.coinbase.com/products/BTC-USD/candles"
SERIES_ID = "10684"
CATALOG_PAGE_SIZE = 100
SCHEMA_VERSION = 1
MAX_PRICE_AGE_SECONDS = 120
SLUG_PATTERN = re.compile(r"^btc-updown-5m-(\d+)$")


class JsonHTTP(Protocol):
    async def json(self, url: str, params: dict[str, str | int] | None = None) -> Any: ...


class HistoricalHTTP:
    """Bounded public GET client plus one fixed CLOB JSON POST endpoint."""

    def __init__(self, *, interval: float = 0.03, max_bytes: int = 16_000_000) -> None:
        self.get_client = PublicHTTP(
            {"gamma-api.polymarket.com", "data-api.polymarket.com", "api.exchange.coinbase.com"},
            max_bytes=max_bytes, interval=interval,
        )
        self.max_bytes = max_bytes
        self.post_client = httpx.AsyncClient(
            verify=ssl.create_default_context(), timeout=30, follow_redirects=False,
            headers={"User-Agent": "EdgeHunter/0.1 public-historical-research"},
            limits=httpx.Limits(max_connections=8),
        )
        self.post_semaphore = asyncio.Semaphore(8)

    async def close(self) -> None:
        await asyncio.gather(self.get_client.close(), self.post_client.aclose())

    async def json(self, url: str, params: dict[str, str | int] | None = None) -> Any:
        return await self.get_client.json(url, params)

    async def post_json(self, url: str, body: dict[str, Any]) -> Any:
        if url != CLOB_BATCH_HISTORY:
            raise SourceError("URL_NOT_ALLOWED")
        encoded = canonical_bytes(body)
        if len(encoded) > 1_000_000:
            raise SourceError("REQUEST_TOO_LARGE")
        async with self.post_semaphore:
            for attempt in range(3):
                try:
                    async with self.post_client.stream(
                        "POST", url, content=encoded, headers={"Content-Type": "application/json"},
                    ) as response:
                        if response.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                            await asyncio.sleep(0.5 * 2 ** attempt)
                            continue
                        if response.status_code != 200:
                            raise SourceError(f"HTTP_{response.status_code}")
                        payload = bytearray()
                        async for chunk in response.aiter_bytes():
                            payload.extend(chunk)
                            if len(payload) > self.max_bytes:
                                raise SourceError("RESPONSE_TOO_LARGE")
                        try:
                            return json.loads(payload)
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            raise SourceError("INVALID_JSON") from None
                except httpx.HTTPError as exc:
                    if attempt == 2:
                        raise SourceError(type(exc).__name__) from None
                    await asyncio.sleep(0.5 * 2 ** attempt)
        raise SourceError("RETRY_EXHAUSTED")


@dataclass(frozen=True)
class DownloadConfig:
    requested_start: int
    requested_end: int
    as_of: int
    shard_index: int = 0
    shard_count: int = 1
    series_id: str = SERIES_ID
    horizons: tuple[int, ...] = (60, 120, 180)

    def __post_init__(self) -> None:
        if not 0 <= self.shard_index < self.shard_count:
            raise ValueError("shard_index must be in [0, shard_count)")
        if self.requested_start % 300 or self.requested_end % 300 or self.as_of % 300:
            raise ValueError("window bounds and as_of must be aligned to five minutes")
        if not self.requested_start < self.requested_end <= self.as_of:
            raise ValueError("invalid temporal window")
        if not self.horizons or any(not 0 < value < 300 for value in self.horizons):
            raise ValueError("horizons must be between 1 and 299 seconds")

    @property
    def shard_start(self) -> int:
        slots = (self.requested_end - self.requested_start) // 300
        return self.requested_start + (slots * self.shard_index // self.shard_count) * 300

    @property
    def shard_end(self) -> int:
        slots = (self.requested_end - self.requested_start) // 300
        return self.requested_start + (slots * (self.shard_index + 1) // self.shard_count) * 300

    def document(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            **asdict(self),
            "horizons": list(self.horizons),
            "shard_start": self.shard_start,
            "shard_end": self.shard_end,
            "policy": "PUBLIC_HISTORICAL_RESEARCH_ONLY",
            "price_rule": "Only real ticks timestamped at or before the latest decision cutoff are stored",
        }


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, UTC).isoformat().replace("+00:00", "Z")


def parse_epoch(slug: Any) -> int | None:
    match = SLUG_PATTERN.fullmatch(str(slug))
    return int(match.group(1)) if match else None


def _series_matches(event: dict[str, Any], series_id: str) -> bool:
    series = event.get("series")
    return isinstance(series, list) and any(
        isinstance(item, dict) and str(item.get("id")) == series_id for item in series
    )


def select_event(
    event: dict[str, Any], *, start: int, end: int, as_of: int, series_id: str = SERIES_ID,
) -> dict[str, Any] | None:
    """Return a normalized, resolved market inside [start, end), or None.

    The encoded five-minute epoch is used instead of mutable catalog dates. The
    label is accepted only after the interval has ended by the fixed as-of time.
    """
    epoch = parse_epoch(event.get("slug"))
    if epoch is None or not start <= epoch < end or epoch + 300 > as_of:
        return None
    if not _series_matches(event, series_id) or event.get("closed") is not True:
        return None
    markets = event.get("markets")
    if not isinstance(markets, list) or len(markets) != 1 or not isinstance(markets[0], dict):
        return None
    market = markets[0]
    try:
        outcomes = [str(value) for value in decode_list(market.get("outcomes", []))]
        tokens = [str(value) for value in decode_list(market.get("clobTokenIds", []))]
        prices = [str(value) for value in decode_list(market.get("outcomePrices", []))]
    except (SourceError, json.JSONDecodeError, TypeError, ValueError):
        return None
    if outcomes != ["Up", "Down"] or len(tokens) != 2 or not all(token.isdecimal() for token in tokens):
        return None
    if prices not in (["1", "0"], ["0", "1"]):
        return None
    if market.get("closed") is not True:
        return None
    condition_id = str(market.get("conditionId", ""))
    if not condition_id.startswith("0x"):
        return None
    return {
        "schema_version": SCHEMA_VERSION,
        "series_id": series_id,
        "event_id": str(event.get("id", "")),
        "market_id": str(market.get("id", "")),
        "slug": str(event["slug"]),
        "epoch": epoch,
        "interval_end": epoch + 300,
        "event_start_time": event.get("startTime"),
        "created_at": market.get("createdAt"),
        "closed_at": market.get("closedTime") or event.get("closedTime"),
        "condition_id": condition_id,
        "question": market.get("question"),
        "resolution_source": market.get("resolutionSource"),
        "outcomes": outcomes,
        "token_ids": tokens,
        "outcome_up": 1 if prices == ["1", "0"] else 0,
        "label_source": "current resolved Gamma metadata",
        "volume": market.get("volume"),
    }


def append_jsonl(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as handle:
        handle.write(canonical_bytes(value) + b"\n")
        handle.flush()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for raw in path.read_bytes().splitlines():
        try:
            value = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def checkpoint_state(path: Path) -> dict[str, Any]:
    state: dict[str, Any] = {"catalog_cursor": None, "catalog_complete": False}
    for row in read_jsonl(path):
        if row.get("phase") == "catalog":
            state["catalog_cursor"] = row.get("next_cursor")
            state["catalog_complete"] = bool(row.get("complete", False))
        elif row.get("phase") == "prices":
            state["last_price_slug"] = row.get("slug")
        elif row.get("phase") == "complete":
            state["complete"] = True
    return state


def completed_candle_batches(path: Path) -> set[tuple[int, int]]:
    return {
        (int(row["start"]), int(row["end"]))
        for row in read_jsonl(path)
        if row.get("phase") == "candles" and row.get("complete") is True
    }


def initialize_output(output: Path, config: DownloadConfig) -> None:
    output.mkdir(parents=True, exist_ok=True)
    config_path = output / "config.json"
    expected = canonical_bytes(config.document()) + b"\n"
    if config_path.exists():
        if canonical_bytes(json.loads(config_path.read_bytes())) + b"\n" != expected:
            raise ValueError(f"output directory belongs to a different run: {output}")
    else:
        config_path.write_bytes(expected)
        append_jsonl(output / "checkpoint.jsonl", {"phase": "init", "config_sha256": hashlib.sha256(expected).hexdigest()})


async def download_catalog(http: JsonHTTP, config: DownloadConfig, output: Path) -> list[dict[str, Any]]:
    initialize_output(output, config)
    event_path = output / "events.jsonl"
    checkpoint_path = output / "checkpoint.jsonl"
    existing = {str(row.get("slug")): row for row in read_jsonl(event_path)}
    state = checkpoint_state(checkpoint_path)
    if state["catalog_complete"]:
        return sorted(existing.values(), key=lambda row: (int(row["epoch"]), str(row["slug"])))

    cursor = state["catalog_cursor"]
    while True:
        params: dict[str, str | int] = {
            "series_id": config.series_id,
            "closed": "true",
            "limit": CATALOG_PAGE_SIZE,
            "order": "id",
            "ascending": "false",
            "end_date_min": iso(config.shard_start + 300),
            "end_date_max": iso(config.shard_end),
        }
        if cursor is not None:
            params["after_cursor"] = str(cursor)
        raw = await http.json(GAMMA_EVENTS, params)
        if not isinstance(raw, dict) or not isinstance(raw.get("events"), list):
            raise SourceError("INVALID_EVENTS_RESPONSE")
        page = raw["events"]
        for value in page:
            if not isinstance(value, dict):
                continue
            selected = select_event(
                value, start=config.shard_start, end=config.shard_end,
                as_of=config.as_of, series_id=config.series_id,
            )
            if selected is not None and selected["slug"] not in existing:
                append_jsonl(event_path, selected)
                existing[selected["slug"]] = selected
        next_cursor = raw.get("next_cursor")
        complete = not next_cursor
        append_jsonl(checkpoint_path, {
            "phase": "catalog", "next_cursor": next_cursor, "complete": complete,
            "page_rows": len(page), "selected_total": len(existing),
        })
        if complete:
            break
        cursor = str(next_cursor)
    return sorted(existing.values(), key=lambda row: (int(row["epoch"]), str(row["slug"])))


async def fetch_trade_histories(
    http: JsonHTTP, event: dict[str, Any], config: DownloadConfig,
) -> list[list[dict[str, Any]]]:
    epoch = int(event["epoch"])
    earliest_decision = epoch + 300 - max(config.horizons)
    latest_decision = epoch + 300 - min(config.horizons)
    tokens = [str(value) for value in event["token_ids"]]
    histories: dict[str, list[dict[str, Any]]] = {token: [] for token in tokens}
    cursor: str | None = None
    while True:
        params: dict[str, str | int] = {
            "condition": str(event["condition_id"]), "limit": 1000,
        }
        if cursor is not None:
            params["cursor"] = cursor
        raw = await http.json(DATA_TRADES, params)
        if not isinstance(raw, dict) or not isinstance(raw.get("data"), list):
            raise SourceError("INVALID_TRADES_RESPONSE")
        page_timestamps: list[int] = []
        for item in raw["data"]:
            if not isinstance(item, dict):
                continue
            try:
                token = str(item["token_id"])
                timestamp = int(item["timestamp"])
                price = float(item["price"])
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
            page_timestamps.append(timestamp)
            if token in histories and timestamp <= latest_decision and 0 < price < 1:
                histories[token].append({
                    "timestamp": timestamp,
                    "price": price,
                    "resolution_seconds": 0,
                    "size": float(item.get("size", 0)),
                    "side": str(item.get("side", "")),
                    "transaction_hash": str(item.get("transaction_hash", "")),
                })
        pagination = raw.get("pagination")
        next_cursor = pagination.get("next_cursor") if isinstance(pagination, dict) else None
        enough = all(
            any(int(point["timestamp"]) <= earliest_decision for point in histories[token])
            for token in tokens
        )
        passed_earliest = bool(page_timestamps) and min(page_timestamps) <= earliest_decision
        if not next_cursor or (enough and passed_earliest):
            break
        cursor = str(next_cursor)
    return [histories[token] for token in tokens]


def _histories_cover_earliest(
    histories: list[list[dict[str, Any]]], event: dict[str, Any], config: DownloadConfig,
) -> bool:
    earliest_decision = int(event["epoch"]) + 300 - max(config.horizons)
    return len(histories) == 2 and all(
        any(
            int(point["timestamp"]) + int(point.get("resolution_seconds", 0)) <= earliest_decision
            for point in history
        )
        for history in histories
    )


def _make_price_record(
    event: dict[str, Any], config: DownloadConfig, histories: list[list[dict[str, Any]]], source: str,
) -> dict[str, Any]:
    epoch = int(event["epoch"])
    cutoff = epoch + 300 - min(config.horizons)
    return {
        "schema_version": SCHEMA_VERSION,
        "slug": event["slug"],
        "epoch": epoch,
        "feature_cutoff_epoch": cutoff,
        "horizons": list(config.horizons),
        "histories": [
            {"outcome": outcome, "token_id": token, "points": points}
            for outcome, token, points in zip(event["outcomes"], event["token_ids"], histories)
        ],
        "coverage": "AVAILABLE" if _histories_cover_earliest(histories, event, config)
        else "NO_COMPLETE_TWO_SIDED_PREDECISION_HISTORY",
        "source": source,
        "lookahead_guard": "all stored point timestamps <= feature_cutoff_epoch < interval_end",
    }


async def _trade_price_record(
    http: JsonHTTP, event: dict[str, Any], config: DownloadConfig,
) -> dict[str, Any]:
    histories = await fetch_trade_histories(http, event, config)
    return _make_price_record(event, config, histories, "Polymarket Data API condition-scoped trades")


async def _batch_price_records(
    http: JsonHTTP, events: list[dict[str, Any]], config: DownloadConfig,
) -> list[dict[str, Any]]:
    post_json = getattr(http, "post_json", None)
    if post_json is None:
        return list(await asyncio.gather(*(_trade_price_record(http, event, config) for event in events)))
    tokens = [str(token) for event in events for token in event["token_ids"]]
    try:
        raw = await post_json(CLOB_BATCH_HISTORY, {
            "markets": tokens,
            "start_ts": min(int(event["epoch"]) for event in events),
            "end_ts": max(int(event["epoch"]) + 300 for event in events),
            "fidelity": 1,
        })
        if not isinstance(raw, dict) or not isinstance(raw.get("history"), dict):
            raise SourceError("INVALID_BATCH_PRICE_HISTORY")
    except SourceError:
        return list(await asyncio.gather(*(_trade_price_record(http, event, config) for event in events)))

    history_map = raw["history"]
    records: list[dict[str, Any] | None] = []
    fallback_indexes: list[int] = []
    for index, event in enumerate(events):
        cutoff = int(event["epoch"]) + 300 - min(config.horizons)
        histories: list[list[dict[str, Any]]] = []
        for token in event["token_ids"]:
            points = []
            raw_points = history_map.get(str(token), [])
            if isinstance(raw_points, list):
                for item in raw_points:
                    if not isinstance(item, dict):
                        continue
                    try:
                        timestamp, price = int(item["t"]), float(item["p"])
                    except (KeyError, TypeError, ValueError, OverflowError):
                        continue
                    if timestamp <= cutoff and 0 < price < 1:
                        points.append({
                            "timestamp": timestamp, "price": price, "resolution_seconds": 60,
                        })
            histories.append(points)
        if _histories_cover_earliest(histories, event, config):
            records.append(_make_price_record(
                event, config, histories, "Polymarket CLOB batch-prices-history fidelity=1",
            ))
        else:
            records.append(None)
            fallback_indexes.append(index)
    if fallback_indexes:
        fallback_records = await asyncio.gather(*(
            _trade_price_record(http, events[index], config) for index in fallback_indexes
        ))
        for index, record in zip(fallback_indexes, fallback_records):
            records[index] = record
    return [record for record in records if record is not None]


async def download_prices(
    http: JsonHTTP, config: DownloadConfig, output: Path, events: list[dict[str, Any]], *, workers: int,
) -> dict[str, int]:
    if not 1 <= workers <= 32:
        raise ValueError("workers must be between 1 and 32")
    price_path = output / "prices.jsonl"
    checkpoint_path = output / "checkpoint.jsonl"
    completed = {
        str(row.get("slug")) for row in read_jsonl(price_path)
        if row.get("coverage") == "AVAILABLE"
    }
    pending = [event for event in events if str(event["slug"]) not in completed]
    queue: asyncio.Queue[list[dict[str, Any]]] = asyncio.Queue()
    for index in range(0, len(pending), 10):
        queue.put_nowait(pending[index:index + 10])
    counters = {"completed": len(completed), "errors": 0}

    async def worker() -> None:
        while True:
            try:
                batch = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                records = await _batch_price_records(http, batch, config)
            except Exception as exc:  # A failed market remains pending for a later resume.
                counters["errors"] += len(batch)
                for event in batch:
                    append_jsonl(output / "errors.jsonl", {
                        "phase": "prices", "slug": event["slug"], "error": type(exc).__name__,
                        "message": str(exc)[:300],
                    })
            else:
                for record in records:
                    append_jsonl(price_path, record)
                    completed.add(str(record["slug"]))
                    counters["completed"] = len(completed)
                    append_jsonl(checkpoint_path, {
                        "phase": "prices", "slug": record["slug"], "completed": len(completed),
                    })
            finally:
                queue.task_done()

    await asyncio.gather(*(worker() for _ in range(workers)))
    return {**counters, "pending": len(events) - len(completed)}


async def download_candles(http: JsonHTTP, config: DownloadConfig, output: Path) -> dict[int, dict[str, Any]]:
    candle_path = output / "candles.jsonl"
    checkpoint_path = output / "checkpoint.jsonl"
    candles = {int(row["timestamp"]): row for row in read_jsonl(candle_path)}
    completed = completed_candle_batches(checkpoint_path)
    cursor = config.shard_start - 7200
    while cursor < config.shard_end:
        batch_end = min(cursor + 240 * 60, config.shard_end)
        if (cursor, batch_end) not in completed:
            raw = await http.json(COINBASE_CANDLES, {
                "granularity": 60,
                "start": iso(cursor),
                "end": iso(batch_end),
            })
            if not isinstance(raw, list):
                raise SourceError("INVALID_COINBASE_CANDLES")
            for item in raw:
                if not isinstance(item, list) or len(item) < 6:
                    continue
                try:
                    timestamp = int(item[0])
                    row = {
                        "timestamp": timestamp,
                        "low": float(item[1]),
                        "high": float(item[2]),
                        "open": float(item[3]),
                        "close": float(item[4]),
                        "volume": float(item[5]),
                    }
                except (TypeError, ValueError, OverflowError):
                    continue
                if cursor <= timestamp < batch_end and timestamp not in candles:
                    append_jsonl(candle_path, row)
                    candles[timestamp] = row
            append_jsonl(checkpoint_path, {
                "phase": "candles", "start": cursor, "end": batch_end, "complete": True,
            })
        cursor = batch_end
    return candles


def _latest_price(
    history: dict[str, Any], decision: int, *, default_resolution_seconds: int = 0,
) -> tuple[float, int] | None:
    points = history.get("points")
    if not isinstance(points, list):
        return None
    eligible = [
        item for item in points
        if (
            isinstance(item, dict)
            and int(item.get("timestamp", decision + 1))
            + int(item.get("resolution_seconds", default_resolution_seconds)) <= decision
        )
    ]
    if not eligible:
        return None
    latest = max(eligible, key=lambda item: int(item["timestamp"]))
    timestamp = int(latest["timestamp"])
    if decision - timestamp > MAX_PRICE_AGE_SECONDS:
        return None
    return float(latest["price"]), timestamp


def build_observations(output: Path, config: DownloadConfig) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    events = {str(row["slug"]): row for row in read_jsonl(output / "events.jsonl")}
    prices = {str(row["slug"]): row for row in read_jsonl(output / "prices.jsonl")}
    candles = {int(row["timestamp"]): row for row in read_jsonl(output / "candles.jsonl")}
    observations: dict[tuple[str, int], dict[str, Any]] = {}
    exclusions: list[dict[str, Any]] = []
    for slug, event in events.items():
        price_record = prices.get(slug)
        if price_record is None:
            exclusions.append({"slug": slug, "reason": "missing_price_record"})
            continue
        histories = price_record.get("histories")
        if not isinstance(histories, list) or len(histories) != 2:
            exclusions.append({"slug": slug, "reason": "invalid_price_record"})
            continue
        epoch = int(event["epoch"])
        default_resolution = 60 if "CLOB" in str(price_record.get("source", "")) else 0
        opening = candles.get(epoch)
        if opening is None:
            exclusions.append({"slug": slug, "reason": "missing_open_candle"})
            continue
        for horizon in config.horizons:
            decision = epoch + 300 - horizon
            up_point = _latest_price(
                histories[0], decision, default_resolution_seconds=default_resolution,
            )
            down_point = _latest_price(
                histories[1], decision, default_resolution_seconds=default_resolution,
            )
            if up_point is None or down_point is None:
                exclusions.append({
                    "slug": slug, "horizon_seconds": horizon,
                    "reason": "missing_two_sided_price_at_decision",
                })
                continue
            up, up_timestamp = up_point
            down, down_timestamp = down_point
            feature_timestamp = min(up_timestamp, down_timestamp) // 60 * 60
            spot = candles.get(feature_timestamp)
            history_timestamps = list(range(feature_timestamp - 7200, feature_timestamp, 60))
            history = [candles.get(timestamp) for timestamp in history_timestamps]
            if spot is None or any(item is None for item in history):
                exclusions.append({
                    "slug": slug, "horizon_seconds": horizon,
                    "reason": "missing_aligned_spot_or_120_prior_candles",
                })
                continue
            effective_horizon = epoch + 300 - feature_timestamp
            if not 0 < effective_horizon <= 300:
                exclusions.append({
                    "slug": slug, "horizon_seconds": horizon,
                    "reason": "invalid_aligned_feature_time",
                })
                continue
            row = {
                "slug": slug,
                "epoch": epoch,
                "horizon_seconds": horizon,
                "open_price": float(opening["open"]),
                "spot_price": float(spot["open"]),
                "vol_per_second": realized_vol_per_second(
                    float(item["close"]) for item in history if item is not None
                ),
                "up_price": up,
                "down_price": down,
                "outcome_up": int(event["outcome_up"]),
                "up_price_timestamp": up_timestamp,
                "down_price_timestamp": down_timestamp,
                "effective_horizon_seconds": effective_horizon,
            }
            key = (slug, horizon)
            previous = observations.get(key)
            if previous is not None and canonical_bytes(previous) != canonical_bytes(row):
                raise ValueError(f"conflicting observation for {key}")
            observations[key] = row
    rows = sorted(observations.values(), key=lambda row: (int(row["epoch"]), int(row["horizon_seconds"])))
    exclusions.sort(key=lambda row: (str(row["slug"]), int(row.get("horizon_seconds", 0)), str(row["reason"])))
    (output / "observations.jsonl").write_bytes(
        b"".join(canonical_bytes(row) + b"\n" for row in rows)
    )
    (output / "observation_exclusions.jsonl").write_bytes(
        b"".join(canonical_bytes(row) + b"\n" for row in exclusions)
    )
    return rows, exclusions


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_manifest(output: Path, config: DownloadConfig, counts: dict[str, int]) -> dict[str, Any]:
    append_jsonl(output / "checkpoint.jsonl", {
        "phase": "complete" if counts["pending"] == 0 else "run_finished",
        "pending": counts["pending"],
    })
    files = {}
    for name in (
        "config.json", "events.jsonl", "prices.jsonl", "candles.jsonl", "observations.jsonl",
        "observation_exclusions.jsonl", "errors.jsonl", "checkpoint.jsonl",
    ):
        path = output / name
        if path.exists():
            files[name] = {"bytes": path.stat().st_size, "sha256": _sha256(path)}
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "config": config.document(),
        "counts": counts,
        "files": files,
        "limitations": [
            "Fine-grained Polymarket price retention is finite; missing history is preserved as missing",
            "Prices are historical observations, not executable asks or historical order-book depth",
            "Gamma labels and contract metadata are current resolved metadata, not point-in-time snapshots",
        ],
    }
    (output / "manifest.json").write_bytes(canonical_bytes(manifest) + b"\n")
    return manifest


async def run_download(
    http: JsonHTTP, config: DownloadConfig, output: Path, *, workers: int = 8,
) -> dict[str, Any]:
    events = await download_catalog(http, config, output)
    price_counts, _ = await asyncio.gather(
        download_prices(http, config, output, events, workers=workers),
        download_candles(http, config, output),
    )
    observations, exclusions = build_observations(output, config)
    counts = dict(price_counts)
    counts["events"] = len(events)
    counts["observations"] = len(observations)
    counts["observation_exclusions"] = len(exclusions)
    return write_manifest(output, config, counts)


def merge_shards(root: Path, shard_count: int) -> dict[str, Any]:
    if shard_count < 1:
        raise ValueError("shard_count must be positive")
    configs: list[dict[str, Any]] = []
    events: dict[str, dict[str, Any]] = {}
    prices: dict[str, dict[str, Any]] = {}
    candles: dict[int, dict[str, Any]] = {}
    observations: dict[tuple[str, int], dict[str, Any]] = {}
    for index in range(shard_count):
        shard = root / f"shard-{index:03d}-of-{shard_count:03d}"
        config = json.loads((shard / "config.json").read_bytes())
        configs.append(config)
        for row in read_jsonl(shard / "events.jsonl"):
            events[str(row["slug"])] = row
        for row in read_jsonl(shard / "prices.jsonl"):
            prices[str(row["slug"])] = row
        for row in read_jsonl(shard / "candles.jsonl"):
            timestamp = int(row["timestamp"])
            previous = candles.get(timestamp)
            if previous is not None and canonical_bytes(previous) != canonical_bytes(row):
                raise ValueError(f"conflicting Coinbase candle at {timestamp}")
            candles[timestamp] = row
        for row in read_jsonl(shard / "observations.jsonl"):
            key = (str(row["slug"]), int(row["horizon_seconds"]))
            previous = observations.get(key)
            if previous is not None and canonical_bytes(previous) != canonical_bytes(row):
                raise ValueError(f"conflicting observation for {key}")
            observations[key] = row
    identity = ("schema_version", "requested_start", "requested_end", "as_of", "shard_count", "series_id", "horizons")
    reference = {key: configs[0][key] for key in identity}
    if any({key: config[key] for key in identity} != reference for config in configs):
        raise ValueError("shards do not belong to the same run")
    merged = root / "merged"
    merged.mkdir(parents=True, exist_ok=True)
    event_rows = sorted(events.values(), key=lambda row: (int(row["epoch"]), str(row["slug"])))
    price_rows = sorted(prices.values(), key=lambda row: (int(row["epoch"]), str(row["slug"])))
    candle_rows = [candles[key] for key in sorted(candles)]
    observation_rows = sorted(
        observations.values(), key=lambda row: (int(row["epoch"]), int(row["horizon_seconds"])),
    )
    available_price_records = sum(row.get("coverage") == "AVAILABLE" for row in price_rows)
    (merged / "events.jsonl").write_bytes(b"".join(canonical_bytes(row) + b"\n" for row in event_rows))
    (merged / "prices.jsonl").write_bytes(b"".join(canonical_bytes(row) + b"\n" for row in price_rows))
    (merged / "candles.jsonl").write_bytes(b"".join(canonical_bytes(row) + b"\n" for row in candle_rows))
    (merged / "observations.jsonl").write_bytes(
        b"".join(canonical_bytes(row) + b"\n" for row in observation_rows)
    )
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "source_shards": shard_count,
        "config": reference,
        "events": len(event_rows),
        "price_records": len(price_rows),
        "available_price_records": available_price_records,
        "observations": len(observation_rows),
        "complete": len(event_rows) == available_price_records,
        "events_sha256": _sha256(merged / "events.jsonl"),
        "prices_sha256": _sha256(merged / "prices.jsonl"),
        "candles_sha256": _sha256(merged / "candles.jsonl"),
        "observations_sha256": _sha256(merged / "observations.jsonl"),
    }
    (merged / "manifest.json").write_bytes(canonical_bytes(manifest) + b"\n")
    return manifest
