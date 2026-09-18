"""Prepare or run one bounded Jev shadow forecast; no orders and no retrospective labels."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from edgehunter.intelligence.environment import load_typesafe_env  # noqa: E402
from edgehunter.intelligence.jev_forecast import (  # noqa: E402
    MODEL,
    JevForecastAdapter,
    forecast_state,
    request_body,
)
from edgehunter.intelligence.providers import AccessBlocked, CostBudget  # noqa: E402
from edgehunter.research.btc_challenger import frozen_model, write_report  # noqa: E402
from edgehunter.research.btc_intraday import IntradayObservation  # noqa: E402
from edgehunter.research.learned_intraday import predict_up  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def latest_row(events: Path) -> tuple[IntradayObservation, float]:
    # Local original paper feed: only public prices/time; labels/fills never copied.
    observation = None
    for line in events.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        if value.get("type") == "OBSERVATION":
            observation = value
    if observation is None:
        raise ValueError("no observed public snapshot")
    epoch = int(observation["slug"].rsplit("-", 1)[1])
    reference = observation["coinbase"]
    if (not isinstance(reference, dict)
            or any(reference.get(name) is None for name in ("open", "spot", "vol_per_second", "ticker_time"))
            or any(observation.get(name) is None for name in ("up_ask", "down_ask"))):
        raise ValueError("LATEST_SNAPSHOT_INCOMPLETE; wait for a complete fresh public observation")
    received_at = datetime.fromisoformat(observation["observed_at"]).timestamp()
    ticker_at = datetime.fromisoformat(str(reference["ticker_time"]).replace("Z", "+00:00")).timestamp()
    # Experimental snapshot semantics differ from the learned historical model.
    # Local timing benchmark is illustrative only, not a probability-quality comparison.
    row = IntradayObservation(
        slug=observation["slug"], epoch=epoch, horizon_seconds=60,
        open_price=float(reference["open"]), spot_price=float(reference["spot"]),
        vol_per_second=float(reference["vol_per_second"]),
        up_price=float(observation["up_ask"]), down_price=float(observation["down_ask"]),
        outcome_up=0, effective_horizon_seconds=max(1, min(300, int(epoch + 300 - received_at))),
    )
    if ticker_at > received_at + 2:
        raise ValueError("future ticker timestamp")
    return row, min(received_at + 15, ticker_at + 15, epoch + 280)


async def main() -> None:
    load_typesafe_env(ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="one paid API attempt only with explicit budget and key")
    parser.add_argument("--budget-usd", type=Decimal, default=Decimal(os.environ.get("TYPESAFE_BUDGET_USD", "0")))
    parser.add_argument("--maximum-call-usd", type=Decimal, default=Decimal(os.environ.get("TYPESAFE_MAX_CALL_USD", "0.001")))
    args = parser.parse_args()
    key = os.environ.get("TYPESAFE_API_KEY", "")
    if os.environ.get("TYPESAFE_MODEL", MODEL) != MODEL:
        raise SystemExit("This experiment is fixed to jev-1.13.0; model changes require a new experiment")
    report: dict = {
        "model": MODEL, "key_present": bool(key), "live_requested": args.live,
        "live_call_completed": False, "jev_latency_ms": None,
        "jev_profitability_demonstrated": False, "evaluation_mode": "SHADOW_ONLY",
        "financial_edge_proven": False, "provider_budget_usd": str(args.budget_usd),
        "limitations": [
            "No Jev forecast has been calibrated on this market.",
            "One API timing sample does not establish tail latency or forecasting quality.",
            "The old live feed uses ticker/ask features; this probe cannot prove historical replication.",
            "Prediction scores and relevance scores are not interchangeable.",
        ],
    }
    if args.live and (not key or args.budget_usd <= 0):
        report["status"] = "TYPESAFE_API_KEY_MISSING" if not key else "EXPLICIT_PROVIDER_BUDGET_REQUIRED"
        write_report(ROOT / "reports/jev_shadow_probe.json", report)
        print(json.dumps(report, indent=2))
        return
    try:
        row, deadline = latest_row(ROOT / "data/btc_paper/events.jsonl")
        body = request_body(forecast_state(row))
        _, model = frozen_model(json.loads((ROOT / "reports/btc_history_270d_learned.json").read_text()))
        samples = []
        for _ in range(5000):
            start = time.perf_counter_ns()
            predict_up(model, row)
            samples.append((time.perf_counter_ns() - start) / 1_000_000)
        report["local_scalar_prediction_ms_median"] = statistics.median(samples)
        report["local_scalar_prediction_ms_p95"] = sorted(samples)[4749]
        report["timing_samples"] = len(samples)
        report["snapshot_slug"] = row.slug
        report["snapshot_fresh"] = time.time() < deadline
        write_report(ROOT / "reports/jev_shadow_request.json", body)
        if not args.live:
            report["status"] = "PREPARED_NO_PROVIDER_CALL"
        elif not key:
            report["status"] = "TYPESAFE_API_KEY_MISSING"
        elif args.budget_usd <= 0:
            report["status"] = "EXPLICIT_PROVIDER_BUDGET_REQUIRED"
        else:
            adapter = JevForecastAdapter(
                api_key=key, budget=CostBudget(ROOT / "data/jev_shadow/usage.db", args.budget_usd),
                max_call_cost=args.maximum_call_usd,
                timeout_seconds=float(os.environ.get("TYPESAFE_TIMEOUT_SECONDS", "2")),
            )
            try:
                result = await adapter.forecast(row, available_until=deadline)
                report.update(status="ONE_SHADOW_FORECAST_RECORDED", live_call_completed=True,
                              jev_latency_ms=result.elapsed_ms, forecast=asdict(result))
                path = ROOT / "data/jev_shadow/forecasts.jsonl"
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps({"received_at": time.time(), "slug": row.slug,
                                             "end_epoch": row.epoch + 300,
                                             "local_probability_up": predict_up(model, row),
                                             "features": body["state"], **asdict(result)}) + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
            finally:
                await adapter.close()
    except (ValueError, TypeError, KeyError, OSError, AccessBlocked) as error:
        report["status"] = "PROBE_UNAVAILABLE"
        report["reason"] = str(error)
    write_report(ROOT / "reports/jev_shadow_probe.json", report)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
