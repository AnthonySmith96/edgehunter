"""Prospective BTC 5-minute paper experiment using only public GET endpoints."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import random
import sys
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import ROUND_FLOOR, Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from edgehunter.ingestion.http import PublicHTTP, SourceError  # noqa: E402
from edgehunter.ops.lock import InstanceLock  # noqa: E402
from edgehunter.ops.paper_candidate import resolution_payout  # noqa: E402
from edgehunter.research.btc_lag import (  # noqa: E402
    choose_candidate,
    realized_vol_per_second,
    robust_probability_bounds,
)
from edgehunter.venues.polymarket.public import decode_list  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "btc_paper_v1.json"
EVENTS_PATH = ROOT / "data" / "btc_paper" / "events.jsonl"
REPORT_PATH = ROOT / "reports" / "btc_paper_live.json"
LOCK_PATH = ROOT / ".local" / "btc_paper.lock"
D = Decimal
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
COINBASE = "https://api.exchange.coinbase.com"


def utcnow() -> datetime:
    return datetime.now(UTC)


def load_config() -> tuple[dict[str, Any], str]:
    raw = CONFIG_PATH.read_bytes()
    config = json.loads(raw)
    if config.get("mode") != "PAPER_ONLY" or config.get("experiment") != "btc_5m_external_lag_v1":
        raise RuntimeError("unexpected BTC paper policy")
    return config, hashlib.sha256(raw).hexdigest()


def load_events() -> list[dict[str, Any]]:
    if not EVENTS_PATH.exists():
        return []
    rows = []
    for line in EVENTS_PATH.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def append_event(event: dict[str, Any], config_hash: str) -> None:
    EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "recorded_at": utcnow().isoformat(),
        "experiment": "btc_5m_external_lag_v1",
        "config_sha256": config_hash,
        **event,
    }
    with EVENTS_PATH.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(document, ensure_ascii=False, sort_keys=True, default=str) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def best_ask(book: dict[str, Any]) -> tuple[D, D] | None:
    levels = sorted(book.get("asks", []), key=lambda level: D(str(level["price"])))
    if not levels:
        return None
    return D(str(levels[0]["price"])), D(str(levels[0]["size"]))


def event_epoch(now: datetime) -> int:
    return int(now.timestamp()) // 300 * 300


async def fetch_current_market(http: PublicHTTP, now: datetime) -> tuple[dict[str, Any], int, str]:
    epoch = event_epoch(now)
    slug = f"btc-updown-5m-{epoch}"
    event = await http.json(f"{GAMMA}/events/slug/{slug}")
    if not isinstance(event, dict) or not isinstance(event.get("markets"), list) or len(event["markets"]) != 1:
        raise SourceError("INVALID_BTC_EVENT")
    market = event["markets"][0]
    if not isinstance(market, dict) or market.get("slug") != slug:
        raise SourceError("BTC_MARKET_MISMATCH")
    return market, epoch, slug


async def book(http: PublicHTTP, token: str) -> dict[str, Any]:
    payload = await http.json(f"{CLOB}/book", {"token_id": token})
    if not isinstance(payload, dict) or str(payload.get("asset_id")) != token:
        raise SourceError("BOOK_TOKEN_MISMATCH")
    return payload


async def coinbase_inputs(http: PublicHTTP, epoch: int, minutes: int) -> dict[str, Any]:
    candles = await http.json(f"{COINBASE}/products/BTC-USD/candles", {"granularity": 60})
    ticker = await http.json(f"{COINBASE}/products/BTC-USD/ticker")
    if not isinstance(candles, list) or not isinstance(ticker, dict):
        raise SourceError("INVALID_COINBASE_DATA")
    valid = [row for row in candles if isinstance(row, list) and len(row) >= 5]
    opening = next((row for row in valid if int(row[0]) == epoch), None)
    historical = sorted((row for row in valid if int(row[0]) < epoch), key=lambda row: int(row[0]))[-minutes:]
    if opening is None or len(historical) < 10:
        raise SourceError("COINBASE_HISTORY_OR_OPEN_UNAVAILABLE")
    closes = [float(row[4]) for row in historical]
    return {
        "spot": float(ticker["price"]),
        "open": float(opening[3]),
        "ticker_time": ticker.get("time"),
        "vol_per_second": realized_vol_per_second(closes),
        "vol_observations": len(closes),
        "history_last_epoch": int(historical[-1][0]),
    }


def bootstrap_day_ci(settlements: list[dict[str, Any]]) -> list[float] | None:
    by_day: dict[str, float] = defaultdict(float)
    for row in settlements:
        by_day[str(row["settled_at"])[:10]] += float(row["pnl_sim_usd"])
    days = sorted(by_day)
    if len(days) < 2:
        return None
    rng = random.Random(20260918)
    samples = []
    for _ in range(5000):
        samples.append(sum(by_day[rng.choice(days)] for _ in days))
    samples.sort()
    return [samples[124], samples[4874]]


def write_report(events: list[dict[str, Any]], config_hash: str) -> dict[str, Any]:
    observations = [row for row in events if row.get("type") == "OBSERVATION"]
    signals = [row for row in events if row.get("type") == "SIGNAL"]
    fills = [row for row in events if row.get("type") == "PAPER_FILL"]
    settlements = [row for row in events if row.get("type") == "SETTLEMENT"]
    no_fills = [row for row in events if row.get("type") == "NO_FILL"]
    pnl = sum((D(str(row["pnl_sim_usd"])) for row in settlements), D(0))
    days = {str(row["settled_at"])[:10] for row in settlements}
    ci = bootstrap_day_ci(settlements)
    gate = len(settlements) >= 100 and len(days) >= 7 and pnl > 0 and ci is not None and ci[0] > 0
    report = {
        "as_of": utcnow().isoformat(),
        "experiment": "btc_5m_external_lag_v1",
        "mode": "PAPER_ONLY_PUBLIC_DATA",
        "config_sha256": config_hash,
        "observations": len(observations),
        "attempted_markets": len(signals),
        "paper_fills": len(fills),
        "unfilled_attempts": len(no_fills),
        "settled_fills": len(settlements),
        "wins": sum(D(str(row["payout_per_share"])) > 0 for row in settlements),
        "net_pnl_sim_usd": str(pnl),
        "currently_positive": bool(settlements) and pnl > 0,
        "calendar_days": len(days),
        "bootstrap_day_pnl_ci95": ci,
        "success_gate_passed": gate,
        "financial_edge_proven": gate,
        "open_fills": len(fills) - len(settlements),
        "latest_settlements": settlements[-20:],
        "evidence": {
            "events": str(EVENTS_PATH.relative_to(ROOT)),
            "config": str(CONFIG_PATH.relative_to(ROOT)),
            "rule_registered_before_first_signal": True,
            "settlement_requires_official_terminal_metadata": True,
        },
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = REPORT_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    temporary.replace(REPORT_PATH)
    return report


async def settle_pending(http: PublicHTTP, events: list[dict[str, Any]], config_hash: str) -> None:
    settled = {str(row["market_id"]) for row in events if row.get("type") == "SETTLEMENT"}
    fills = [row for row in events if row.get("type") == "PAPER_FILL"
             and str(row["market_id"]) not in settled]
    for fill in fills:
        if utcnow() < datetime.fromisoformat(str(fill["end_at"])) + timedelta(seconds=30):
            continue
        market = await http.json(f"{GAMMA}/markets/{fill['market_id']}")
        if not isinstance(market, dict):
            continue
        payout = resolution_payout(market, str(fill["asset_id"]))
        if payout is None:
            continue
        quantity = D(str(fill["quantity"]))
        pnl = quantity * payout - D(str(fill["total_cost_sim_usd"]))
        append_event({
            "type": "SETTLEMENT",
            "market_id": str(fill["market_id"]),
            "slug": fill["slug"],
            "side": fill["side"],
            "settled_at": utcnow().isoformat(),
            "payout_per_share": str(payout),
            "pnl_sim_usd": str(pnl),
            "official_outcome_prices": market.get("outcomePrices"),
            "official_resolution_status": market.get("umaResolutionStatus"),
        }, config_hash)


async def observe_once(http: PublicHTTP, config: dict[str, Any], config_hash: str,
                       events: list[dict[str, Any]]) -> None:
    now = utcnow()
    market, epoch, slug = await fetch_current_market(http, now)
    attempted = {str(row["slug"]) for row in events if row.get("type") == "SIGNAL"}
    outcomes = decode_list(market.get("outcomes", []))
    tokens = [str(token) for token in decode_list(market.get("clobTokenIds", []))]
    if outcomes != ["Up", "Down"] or len(tokens) != 2 or market.get("acceptingOrders") is not True:
        raise SourceError("BTC_MARKET_NOT_TRADABLE")
    end = datetime.fromisoformat(str(market["endDate"]).replace("Z", "+00:00"))
    remaining = (end - now).total_seconds()
    if remaining <= 0:
        return
    parameters = config["parameters"]
    reference, books = await asyncio.gather(
        coinbase_inputs(http, epoch, int(parameters["volatility_minutes"])),
        asyncio.gather(*(book(http, token) for token in tokens)),
    )
    asks = [best_ask(payload) for payload in books]
    low, high = robust_probability_bounds(
        reference["spot"], reference["open"], reference["vol_per_second"], remaining,
        betas=tuple(float(value) for value in parameters["betas"]),
        tail_weight=float(parameters["tail_weight"]),
        tail_scale=float(parameters["tail_scale"]),
    )
    tick = D(str(market.get("orderPriceMinTickSize", books[0].get("tick_size", "0.01"))))
    decision = None
    if asks[0] is not None and asks[1] is not None:
        decision = choose_candidate(
            asks[0][0], asks[1][0], low, high, tick=tick,
            min_ask=D(str(parameters["min_ask"])), max_ask=D(str(parameters["max_ask"])),
            min_edge=D(str(parameters["min_edge"])), max_edge=D(str(parameters["max_edge"])),
            fee_curve=D(str(parameters["fee_curve_sensitivity"])),
        )
    observation = {
        "type": "OBSERVATION",
        "market_id": str(market["id"]),
        "slug": slug,
        "observed_at": now.isoformat(),
        "end_at": end.isoformat(),
        "seconds_remaining": remaining,
        "coinbase": reference,
        "up_ask": str(asks[0][0]) if asks[0] is not None else None,
        "down_ask": str(asks[1][0]) if asks[1] is not None else None,
        "probability_up_low": low,
        "probability_up_high": high,
        "candidate": decision.side if decision else None,
        "abstain_reason": "INCOMPLETE_BOOK" if any(ask is None for ask in asks)
        else ("NO_COSTED_EDGE" if decision is None else None),
        "contract_resolution_source": market.get("resolutionSource"),
    }
    append_event(observation, config_hash)
    if decision is None or slug in attempted or remaining <= int(parameters["close_buffer_seconds"]):
        return
    token_index = 0 if decision.side == "UP" else 1
    token = tokens[token_index]
    limit = min(decision.ask + tick * D(str(parameters["limit_slack_ticks"])), D(1) - tick)
    append_event({
        "type": "SIGNAL",
        "market_id": str(market["id"]),
        "slug": slug,
        "side": decision.side,
        "asset_id": token,
        "signal_at": utcnow().isoformat(),
        "seen_ask": str(decision.ask),
        "limit_price": str(limit),
        "conservative_probability": str(decision.conservative_probability),
        "modeled_edge_per_share": str(decision.edge_per_share),
    }, config_hash)
    await asyncio.sleep(int(parameters["latency_ms"]) / 1000)
    post_latency = await book(http, token)
    post_latency_ask = best_ask(post_latency)
    if post_latency_ask is None:
        append_event({"type": "NO_FILL", "market_id": str(market["id"]), "slug": slug,
                      "side": decision.side, "reason": "POST_LATENCY_EMPTY_ASK"}, config_hash)
        return
    fill_price, visible_size = post_latency_ask
    if fill_price > limit:
        append_event({"type": "NO_FILL", "market_id": str(market["id"]), "slug": slug,
                      "side": decision.side, "reason": "ASK_MOVED_ABOVE_LIMIT",
                      "post_latency_ask": str(fill_price)}, config_hash)
        return
    fee_per_share = D(str(parameters["fee_curve_sensitivity"])) * fill_price * (1 - fill_price)
    maximum_stake = D(str(parameters["maximum_stake_sim_usd"]))
    quantity = min(maximum_stake / (fill_price + fee_per_share), visible_size * D("0.8"))
    quantity = quantity.quantize(D("0.000001"), rounding=ROUND_FLOOR)
    minimum = D(str(market.get("orderMinSize", post_latency.get("min_order_size", "5"))))
    if quantity < minimum:
        append_event({"type": "NO_FILL", "market_id": str(market["id"]), "slug": slug,
                      "side": decision.side, "reason": "INSUFFICIENT_DEPTH_OR_MINIMUM",
                      "post_latency_ask": str(fill_price)}, config_hash)
        return
    fee = quantity * fee_per_share
    total_cost = quantity * fill_price + fee
    append_event({
        "type": "PAPER_FILL",
        "market_id": str(market["id"]),
        "slug": slug,
        "side": decision.side,
        "asset_id": token,
        "filled_at": utcnow().isoformat(),
        "end_at": end.isoformat(),
        "quantity": str(quantity),
        "fill_price": str(fill_price),
        "fee_sim_usd": str(fee),
        "total_cost_sim_usd": str(total_cost),
        "latency_ms": int(parameters["latency_ms"]),
        "post_latency_book_timestamp": post_latency.get("timestamp"),
    }, config_hash)


async def run(duration: int | None, interval: float) -> None:
    config, config_hash = load_config()
    http = PublicHTTP({"gamma-api.polymarket.com", "clob.polymarket.com",
                       "api.exchange.coinbase.com"}, interval=0.05)
    deadline = time.monotonic() + duration if duration is not None else None
    try:
        while True:
            events = load_events()
            try:
                await settle_pending(http, events, config_hash)
                events = load_events()
                await observe_once(http, config, config_hash, events)
            except (SourceError, KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
                append_event({"type": "ERROR", "error": str(exc),
                              "error_class": type(exc).__name__}, config_hash)
            report = write_report(load_events(), config_hash)
            print(json.dumps({key: report[key] for key in (
                "as_of", "observations", "attempted_markets", "paper_fills",
                "settled_fills", "net_pnl_sim_usd", "currently_positive",
                "success_gate_passed")}, ensure_ascii=False), flush=True)
            if duration == 0 or (deadline is not None and time.monotonic() >= deadline):
                return
            await asyncio.sleep(max(interval, 1.0))
    finally:
        await http.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=int, default=3600, help="seconds; 0 performs one cycle")
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--forever", action="store_true", help="run until explicitly stopped")
    args = parser.parse_args()
    if args.duration < 0 or args.interval < 1:
        parser.error("duration must be nonnegative and interval at least one second")
    with InstanceLock(LOCK_PATH):
        asyncio.run(run(None if args.forever else args.duration, args.interval))


if __name__ == "__main__":
    main()
