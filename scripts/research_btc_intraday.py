"""Temporal BTC 5m research using public point-in-time Polymarket prices."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from edgehunter.ingestion.http import PublicHTTP, SourceError  # noqa: E402
from edgehunter.research.btc_intraday import (  # noqa: E402
    IntradayObservation,
    IntradaySpec,
    evaluate_intraday,
)
from edgehunter.research.btc_lag import realized_vol_per_second  # noqa: E402
from edgehunter.venues.polymarket.public import decode_list  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "research_btc_intraday"
DATASET = DATA_DIR / "dataset.json"
REGISTRY = DATA_DIR / "registry.json"
REPORT_JSON = ROOT / "reports" / "research_btc_intraday.json"
REPORT_MD = ROOT / "reports" / "research_btc_intraday.md"
GAMMA = "https://gamma-api.polymarket.com"
DATA_API = "https://data-api.polymarket.com"
COINBASE = "https://api.exchange.coinbase.com"
HORIZONS = (60, 120, 180)
SPECS = tuple(
    IntradaySpec(horizon, min_edge, max_ask, vol_scale)
    for horizon in HORIZONS
    for min_edge in (0.02, 0.05, 0.08, 0.10)
    for max_ask in (0.80, 0.90)
    for vol_scale in (0.8, 1.0, 1.2)
)


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, UTC).isoformat().replace("+00:00", "Z")


async def fetch_event(http: PublicHTTP, epoch: int) -> dict[str, Any] | None:
    slug = f"btc-updown-5m-{epoch}"
    try:
        event = await http.json(f"{GAMMA}/events/slug/{slug}")
    except SourceError:
        return None
    if not isinstance(event, dict) or not isinstance(event.get("markets"), list) or len(event["markets"]) != 1:
        return None
    market = event["markets"][0]
    if not isinstance(market, dict) or market.get("closed") is not True:
        return None
    prices = decode_list(market.get("outcomePrices", []))
    if prices not in (["1", "0"], ["0", "1"]):
        return None
    return market


async def fetch_candles(http: PublicHTTP, start: int, end: int) -> dict[int, list[Any]]:
    rows: dict[int, list[Any]] = {}
    cursor = start
    while cursor < end:
        batch_end = min(cursor + 240 * 60, end)
        raw = await http.json(f"{COINBASE}/products/BTC-USD/candles", {
            "granularity": 60, "start": iso(cursor), "end": iso(batch_end),
        })
        if not isinstance(raw, list):
            raise SourceError("INVALID_COINBASE_CANDLES")
        for row in raw:
            if isinstance(row, list) and len(row) >= 5:
                rows[int(row[0])] = row
        cursor = batch_end
    return rows


async def price_at(http: PublicHTTP, token: str, timestamp: int) -> float | None:
    try:
        response = await http.json(f"{DATA_API}/v2/prices-history", {
            "token_id": token, "as_of": timestamp,
        })
    except SourceError:
        return None
    data = response.get("data") if isinstance(response, dict) else None
    if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
        return None
    price = float(data[0].get("price", 0))
    return price if 0 < price < 1 else None


async def build_dataset(hours: int) -> dict[str, Any]:
    now_epoch = int(datetime.now(UTC).timestamp())
    current = now_epoch // 300 * 300
    epochs = list(range(current - hours * 3600, current, 300))
    http = PublicHTTP({"gamma-api.polymarket.com", "data-api.polymarket.com",
                       "api.exchange.coinbase.com"}, interval=0.06)
    try:
        markets_raw = await asyncio.gather(*(fetch_event(http, epoch) for epoch in epochs))
        markets = [(epoch, market) for epoch, market in zip(epochs, markets_raw) if market is not None]
        candles = await fetch_candles(http, epochs[0] - 3 * 3600, current)

        async def build_market(epoch: int, market: dict[str, Any]) -> list[IntradayObservation]:
            tokens = [str(token) for token in decode_list(market.get("clobTokenIds", []))]
            outcome_prices = decode_list(market.get("outcomePrices", []))
            if len(tokens) != 2 or len(outcome_prices) != 2:
                return []
            outcome_up = 1 if outcome_prices == ["1", "0"] else 0
            opening = candles.get(epoch)
            if opening is None:
                return []
            result = []
            for horizon in HORIZONS:
                decision = epoch + 300 - horizon
                spot = candles.get(decision)
                history = [candles[timestamp][4] for timestamp in sorted(candles)
                           if decision - 7200 <= timestamp < decision]
                if spot is None or len(history) < 10:
                    continue
                up, down = await asyncio.gather(
                    price_at(http, tokens[0], decision), price_at(http, tokens[1], decision),
                )
                if up is None or down is None:
                    continue
                result.append(IntradayObservation(
                    str(market["slug"]), epoch, horizon, float(opening[3]), float(spot[3]),
                    realized_vol_per_second(float(value) for value in history[-120:]),
                    up, down, outcome_up,
                ))
            return result

        nested = await asyncio.gather(*(build_market(epoch, market) for epoch, market in markets))
        observations = [asdict(observation) for group in nested for observation in group]
        document = {
            "retrieved_at": datetime.now(UTC).isoformat(),
            "hours_requested": hours,
            "epochs_requested": len(epochs),
            "resolved_markets": len(markets),
            "observations": observations,
            "sources": [
                "https://gamma-api.polymarket.com",
                "https://data-api.polymarket.com/v2/prices-history",
                "https://api.exchange.coinbase.com/products/BTC-USD/candles",
            ],
            "limitations": [
                "Historical point prices are not executable asks or queue-aware fills",
                "Entry adds fixed slippage and a fee sensitivity but lacks historical depth",
                "Coinbase candle open is a proxy for the Chainlink TWAP settlement stream",
            ],
        }
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        DATASET.write_bytes(canonical_bytes(document) + b"\n")
        return document
    finally:
        await http.close()


def load_dataset() -> dict[str, Any]:
    return json.loads(DATASET.read_text(encoding="utf-8"))


def observation_from_dict(row: dict[str, Any]) -> IntradayObservation:
    return IntradayObservation(**row)


def run_evaluation(document: dict[str, Any]) -> dict[str, Any]:
    observations = [observation_from_dict(row) for row in document["observations"]]
    epochs = sorted({observation.epoch for observation in observations})
    split_index = max(1, int(len(epochs) * 0.70))
    train_epochs, holdout_epochs = set(epochs[:split_index]), set(epochs[split_index:])
    train = [observation for observation in observations if observation.epoch in train_epochs]
    holdout = [observation for observation in observations if observation.epoch in holdout_epochs]
    development = [evaluate_intraday(train, spec) for spec in SPECS]
    eligible = [result for result in development
                if int(result["trades"]) >= 10 and float(result["net_pnl"]) > 0]
    selected_result = max(eligible, key=lambda result: (
        float(result["net_pnl"]), float(result["return_on_stake"] or -999)
    )) if eligible else None
    dataset_hash = hashlib.sha256(canonical_bytes(document)).hexdigest()
    if selected_result is None:
        result = {
            "status": "NO_TRAIN_EDGE",
            "dataset_sha256": dataset_hash,
            "markets": len(epochs),
            "train_markets": len(train_epochs),
            "holdout_markets": len(holdout_epochs),
            "specifications": len(SPECS),
            "development_top": sorted(development, key=lambda row: float(row["net_pnl"]), reverse=True)[:10],
            "financial_edge_proven": False,
        }
        return result
    selected_spec = IntradaySpec(**selected_result["spec"])
    registry = {
        "registered_at": datetime.now(UTC).isoformat(),
        "dataset_sha256": dataset_hash,
        "split": "first 70% epochs train; last 30% untouched holdout",
        "selected_spec": selected_spec.to_dict(),
        "specifications_considered": len(SPECS),
        "selection": "highest positive training PnL with at least 10 trades",
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    REGISTRY.write_bytes(canonical_bytes(registry) + b"\n")
    base = evaluate_intraday(holdout, selected_spec, slippage=0.02)
    conservative = evaluate_intraday(holdout, selected_spec, slippage=0.03)
    passed = (int(base["trades"]) >= 5 and float(base["net_pnl"]) > 0
              and float(conservative["net_pnl"]) > 0)
    return {
        "status": "HOLDOUT_POSITIVE" if passed else "HOLDOUT_NO_EDGE",
        "dataset_sha256": dataset_hash,
        "markets": len(epochs),
        "train_markets": len(train_epochs),
        "holdout_markets": len(holdout_epochs),
        "observations": len(observations),
        "specifications": len(SPECS),
        "selected_development": selected_result,
        "holdout_base": base,
        "holdout_conservative": conservative,
        "historical_validation_passed": passed,
        "financial_edge_proven": False,
        "limitations": document["limitations"] + [
            "The holdout is historical, not a prospective paper account",
            "No confidence interval is claimed from this short intraday sample",
        ],
    }


def write_report(result: dict[str, Any]) -> None:
    REPORT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# BTC 5m intraday temporal research",
        "",
        f"Status: **{result['status']}**",
        f"Markets: {result['markets']}; train: {result['train_markets']}; holdout: {result['holdout_markets']}.",
        f"Specifications considered: {result['specifications']}.",
    ]
    if "holdout_base" in result:
        base, conservative = result["holdout_base"], result["holdout_conservative"]
        lines += [
            "",
            f"Selected: `{result['selected_development']['spec']}`.",
            f"Holdout base: {base['trades']} trades, PnL {base['net_pnl']:.4f} SIM_USD.",
            f"Holdout conservative: {conservative['trades']} trades, PnL {conservative['net_pnl']:.4f} SIM_USD.",
        ]
    lines += ["", "This historical result never enables real-money execution and is not prospective proof."]
    REPORT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=int, default=12)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    if not 4 <= args.hours <= 48:
        parser.error("hours must be between 4 and 48")
    document = load_dataset() if args.offline else asyncio.run(build_dataset(args.hours))
    result = run_evaluation(document)
    write_report(result)
    summary = {key: value for key, value in result.items() if key in (
        "status", "markets", "train_markets", "holdout_markets", "observations",
        "specifications", "historical_validation_passed", "financial_edge_proven",
    )}
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
