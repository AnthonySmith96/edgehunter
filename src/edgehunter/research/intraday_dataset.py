from __future__ import annotations

import json
from dataclasses import asdict
from math import isfinite
from pathlib import Path
from typing import Any, Iterable, Mapping

from edgehunter.research.btc_intraday import IntradayObservation

REQUIRED_FIELDS = (
    "slug",
    "epoch",
    "horizon_seconds",
    "open_price",
    "spot_price",
    "vol_per_second",
    "up_price",
    "down_price",
    "outcome_up",
)


def observation_from_mapping(value: Mapping[str, Any]) -> IntradayObservation:
    missing = [field for field in REQUIRED_FIELDS if field not in value]
    if missing:
        raise ValueError(f"missing observation fields: {', '.join(missing)}")
    if not isinstance(value["slug"], str) or not value["slug"]:
        raise ValueError("slug must be a nonempty string")
    integer_fields = ("epoch", "horizon_seconds", "outcome_up")
    for field in integer_fields:
        raw = value[field]
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise ValueError(f"{field} must be an integer")
    for name in ("up_price_timestamp", "down_price_timestamp", "effective_horizon_seconds"):
        raw = value.get(name)
        if raw is not None and (isinstance(raw, bool) or not isinstance(raw, int)):
            raise ValueError(f"{name} must be an integer or null")
    numeric_fields = ("open_price", "spot_price", "vol_per_second", "up_price", "down_price")
    for field in numeric_fields:
        raw = value[field]
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ValueError(f"{field} must be numeric")
    try:
        observation = IntradayObservation(
            slug=str(value["slug"]),
            epoch=int(value["epoch"]),
            horizon_seconds=int(value["horizon_seconds"]),
            open_price=float(value["open_price"]),
            spot_price=float(value["spot_price"]),
            vol_per_second=float(value["vol_per_second"]),
            up_price=float(value["up_price"]),
            down_price=float(value["down_price"]),
            outcome_up=int(value["outcome_up"]),
            up_price_timestamp=(
                int(value["up_price_timestamp"]) if value.get("up_price_timestamp") is not None else None
            ),
            down_price_timestamp=(
                int(value["down_price_timestamp"]) if value.get("down_price_timestamp") is not None else None
            ),
            effective_horizon_seconds=(
                int(value["effective_horizon_seconds"])
                if value.get("effective_horizon_seconds") is not None else None
            ),
        )
    except (TypeError, ValueError) as error:
        raise ValueError("invalid observation field type") from error
    numeric = (
        observation.open_price,
        observation.spot_price,
        observation.vol_per_second,
        observation.up_price,
        observation.down_price,
    )
    if not observation.slug or observation.epoch < 0:
        raise ValueError("invalid observation identity")
    if not 0 < observation.horizon_seconds <= 300:
        raise ValueError("horizon must be between one and 300 seconds")
    if not all(isfinite(number) for number in numeric):
        raise ValueError("non-finite observation value")
    if min(observation.open_price, observation.spot_price, observation.vol_per_second) <= 0:
        raise ValueError("BTC price and volatility must be positive")
    if not 0 < observation.up_price < 1 or not 0 < observation.down_price < 1:
        raise ValueError("market prices must be between zero and one")
    if observation.outcome_up not in (0, 1):
        raise ValueError("outcome_up must be zero or one")
    decision_at = observation.epoch + 300 - observation.horizon_seconds
    for name, timestamp in (
        ("up", observation.up_price_timestamp), ("down", observation.down_price_timestamp),
    ):
        if timestamp is not None and not observation.epoch <= timestamp <= decision_at:
            raise ValueError(f"{name} price timestamp must be within market start and decision time")
    if observation.effective_horizon_seconds is not None and not 0 < observation.effective_horizon_seconds <= 300:
        raise ValueError("effective horizon must be between one and 300 seconds")
    if (
        observation.effective_horizon_seconds is not None
        and observation.effective_horizon_seconds < observation.horizon_seconds
    ):
        raise ValueError("effective feature time exceeds decision time")
    return observation


def load_observation_shards(paths: Iterable[Path]) -> list[IntradayObservation]:
    """Merge JSONL shards, accepting identical overlaps and rejecting conflicts."""
    merged: dict[tuple[str, int], IntradayObservation] = {}
    read_any = False
    for path in paths:
        read_any = True
        with path.open(encoding="utf-8") as handle:
            for line_number, raw in enumerate(handle, start=1):
                if not raw.strip():
                    continue
                try:
                    value = json.loads(raw)
                    if not isinstance(value, dict):
                        raise ValueError("JSON value must be an object")
                    observation = observation_from_mapping(value)
                except (json.JSONDecodeError, ValueError) as error:
                    raise ValueError(f"{path}:{line_number}: {error}") from error
                key = (observation.slug, observation.horizon_seconds)
                previous = merged.get(key)
                if previous is not None and previous != observation:
                    raise ValueError(f"{path}:{line_number}: conflicting duplicate {key}")
                merged[key] = observation
    if not read_any:
        raise ValueError("at least one shard path required")
    return sorted(merged.values(), key=lambda row: (row.epoch, row.slug, row.horizon_seconds))


def write_observation_jsonl(path: Path, observations: Iterable[IntradayObservation]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for observation in observations:
            handle.write(json.dumps(asdict(observation), sort_keys=True, separators=(",", ":")) + "\n")


def compact_evaluation_report(result: Mapping[str, Any]) -> dict[str, Any]:
    """Remove trade-level rows while retaining selection and risk evidence."""
    def compact(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {str(key): compact(item) for key, item in value.items() if key != "trade_log"}
        if isinstance(value, list):
            return [compact(item) for item in value]
        if isinstance(value, tuple):
            return [compact(item) for item in value]
        return value

    report = compact(result)
    if not isinstance(report, dict):
        raise ValueError("evaluation result must be a mapping")
    return {"report_schema": "edgehunter.btc_intraday_evaluation.v1", **report}


def write_compact_report(path: Path, result: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(compact_evaluation_report(result), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
