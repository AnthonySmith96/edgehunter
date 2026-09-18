"""Fetch a predeclared, bounded public sample and evaluate held-out paper PnL.

No authentication, no orders, no secrets, no paid endpoints. TLS stays verified.
Run: python scripts/research_historical.py (uses local cache idempotently).
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import ssl
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from edgehunter.research.evaluation import (  # noqa: E402
    BinCalibrator,
    HistoricalObservation,
    Hypothesis,
    calibration_metrics,
    evaluate,
    freeze_holdout,
    temporal_split,
    walk_forward,
)

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "research"
REPORTS = ROOT / "reports"
D = Decimal


def timestamp(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def store(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    temporary.replace(path)


HYPOTHESES = [Hypothesis(f"favorite_{n}", "favorite", D(n) / 100) for n in (50, 60, 70, 80, 90)] + [Hypothesis(f"underdog_{n}", "underdog", D(n) / 100) for n in (20, 30)]
ANCHORS = [(datetime(2026, 6, 1, tzinfo=timezone.utc) + timedelta(days=n)).date().isoformat() for n in range(0, 92, 4)]
SPEC = {
    "schema_version": 1,
    "registered_before_download": True,
    "fixed_seed": 20260917,
    "universe": "first 32 closed Gamma events by ascending event id on every fourth day June-August 2026; first eligible binary market by ascending market id per event",
    "anchors": ANCHORS,
    "decision": "scheduled endDate minus 24 hours; prices at/before decision minus inferred 60s availability lag, max 2h stale; creation before decision; actual closure after decision",
    "label": "Gamma exact terminal [1,0] or [0,1], UMA resolved; closedTime plus 24h estimated redemption lag",
    "split_train_end": "2026-07-16T00:00:00Z",
    "split_validation_end": "2026-08-08T00:00:00Z",
    "purge_embargo_seconds": 86400,
    "walk_forward_development_boundaries": ["2026-07-01T00:00:00Z", "2026-07-16T00:00:00Z", "2026-08-01T00:00:00Z", "2026-08-08T00:00:00Z"],
    "hypotheses": [{"id": h.identifier, "direction": h.direction, "threshold": str(h.threshold), "max_price": str(h.max_price)} for h in HYPOTHESES],
    "selection": "eligible=train>=20 & validation>=10 & train net_pnl>0 & validation net_pnl>0; maximize validation return_on_turnover; otherwise evaluate best populated validation candidate as UNPROVEN audit only",
    "fixed_cost_scenarios": {"optimistic": ["0.005", "0.04"], "base": ["0.01", "0.07"], "conservative": ["0.03", "0.10"]},
    "money": "1000 simulated collateral units, max 10 loss/event, max100 aggregate locked, min5 shares assumption; no leverage",
    "missing_historical_metadata": ["first_seen_at", "available_at", "historical_orderbook", "queue", "historical_fees", "contract_revision_history", "actual_redemption_time"],
    "gate": "exploratory only; cannot enable live regardless of outcome",
}


async def main(*, offline: bool = False) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    registry = DATA / "hypothesis_registry.json"
    if registry.exists() and json.loads(registry.read_text(encoding="utf-8")) != SPEC:
        raise ValueError("registered experiment differs; preserve registry and use a new prospective experiment")
    if not registry.exists():
        store(registry, SPEC)
    exclusions: list[dict[str, Any]] = []
    manifest: list[dict[str, Any]] = []
    semaphore = asyncio.Semaphore(4)
    async with httpx.AsyncClient(verify=ssl.create_default_context(), timeout=30, headers={"User-Agent": "EdgeHunter-Research/0.1 public-read-only"}) as client:
        async def fetch(endpoint: str, params: dict[str, Any]) -> Any:
            key = hashlib.sha256(json.dumps([endpoint, params], sort_keys=True).encode()).hexdigest()
            path = DATA / "raw" / f"{key}.json"
            meta = path.with_suffix(".meta.json")
            if path.exists():
                raw = path.read_bytes()
                provenance = json.loads(meta.read_text(encoding="utf-8"))
                if hashlib.sha256(raw).hexdigest() != provenance["sha256"]:
                    raise ValueError("raw cache checksum mismatch")
                manifest.append(provenance)
                return json.loads(raw)
            if offline:
                raise RuntimeError(f"offline replay requires missing cached input {key}")
            async with semaphore:
                for attempt in range(3):
                    response = await client.get(endpoint, params=params)
                    if response.status_code not in (429, 500, 502, 503, 504):
                        break
                    await asyncio.sleep(1 + attempt)
                response.raise_for_status()
                raw = response.content
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
                provenance = {"url": str(response.url), "retrieved_at": datetime.now(timezone.utc).isoformat(), "sha256": hashlib.sha256(raw).hexdigest(), "path": str(path.relative_to(ROOT)), "http_status": response.status_code, "source": "Polymarket public API", "response_bytes": len(raw)}
                store(meta, provenance)
                manifest.append(provenance)
                return response.json()

        async def events_on(day: str) -> list[dict[str, Any]]:
            end = (datetime.fromisoformat(day) + timedelta(days=1)).date().isoformat()
            try:
                payload = await fetch("https://gamma-api.polymarket.com/events", {"closed": "true", "limit": 32, "end_date_min": day, "end_date_max": end, "order": "id", "ascending": "true"})
                return list(payload)
            except (httpx.HTTPError, ValueError) as exc:
                exclusions.append({"anchor": day, "reason": "catalog_fetch_error", "error": type(exc).__name__})
                return []

        batches = await asyncio.gather(*(events_on(day) for day in ANCHORS))
        events = {str(event["id"]): event for batch in batches for event in batch}
        print(f"Catalog fetched: {len(events)} events; fixed registry preserved.", flush=True)
        rows: list[HistoricalObservation] = []
        source_rows: list[dict[str, Any]] = []

        async def process(event: dict[str, Any]) -> None:
            event_id = str(event["id"])
            eligible = []
            for market in sorted(event.get("markets", []), key=lambda x: int(x["id"])):
                try:
                    if json.loads(market.get("outcomes", "[]")) != ["Yes", "No"]:
                        continue
                    tokens, prices = json.loads(market["clobTokenIds"]), json.loads(market["outcomePrices"])
                    if len(tokens) != 2 or prices not in (["1", "0"], ["0", "1"]):
                        continue
                    if market.get("umaResolutionStatus") != "resolved":
                        continue
                    decision = timestamp(market["endDate"]) - 86400
                    closed = timestamp(market["closedTime"])
                    if timestamp(market["createdAt"]) >= decision or closed <= decision:
                        continue
                    eligible.append((market, tokens, prices, decision, closed))
                except (KeyError, ValueError, TypeError):
                    continue
            if not eligible:
                exclusions.append({"event_id": event_id, "reason": "no_eligible_binary_market_or_early_resolved"})
                return
            market, tokens, prices, decision, closed = eligible[0]
            histories = []
            try:
                for token in tokens:
                    payload = await fetch("https://clob.polymarket.com/prices-history", {"market": token, "startTs": decision - 7200, "endTs": decision, "fidelity": 5})
                    points = [point for point in payload.get("history", []) if decision - 7200 <= int(point["t"]) <= decision - 60 and 0 < D(str(point["p"])) < 1]
                    if not points:
                        exclusions.append({"event_id": event_id, "market_id": market["id"], "reason": "missing_or_stale_predecision_history"})
                        return
                    histories.append(max(points, key=lambda point: point["t"]))
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                exclusions.append({"event_id": event_id, "market_id": market["id"], "reason": "history_fetch_error", "error": type(exc).__name__})
                return
            cluster = datetime.fromtimestamp(decision, timezone.utc).date().isoformat()
            row = HistoricalObservation(event_id, str(market["id"]), decision, max(int(h["t"]) + 60 for h in histories), closed + 86400, D(str(histories[0]["p"])), D(str(histories[1]["p"])), int(prices[0]), cluster)
            rows.append(row)
            source_rows.append({"event_id": event_id, "market_id": market["id"], "question": market["question"], "condition_id": market.get("conditionId"), "decision_at": decision, "price_available_at": row.price_available_at, "availability_quality": "INFERRED_HISTORY_TIMESTAMP_PLUS_60_SECONDS_NOT_VERIFIED", "settlement_at": row.settlement_at, "redemption_time_quality": "ESTIMATED_CLOSED_TIME_PLUS_24H", "yes_price": str(row.yes_price), "no_price": str(row.no_price), "outcome_yes": row.outcome_yes, "cluster_id": cluster, "contract_hash_current_only": hashlib.sha256(market.get("description", "").encode()).hexdigest(), "resolution_status": market.get("umaResolutionStatus"), "history_points": histories})

        await asyncio.gather(*(process(event) for event in events.values()))
    store(DATA / "manifest.json", sorted(manifest, key=lambda m: m["path"]))
    exclusions.sort(key=lambda item: (str(item.get("event_id", "")), str(item.get("anchor", "")), item["reason"]))
    store(DATA / "exclusions.json", exclusions)
    store(DATA / "observations.json", sorted(source_rows, key=lambda r: (r["decision_at"], r["event_id"])))
    print(f"Eligible observations: {len(rows)}; exclusions: {len(exclusions)}.", flush=True)
    if len(rows) < 20:
        store(REPORTS / "research_historical.json", {"status": "DATA_INSUFFICIENT", "events": len(events), "observations": len(rows), "exclusion_counts": dict(Counter(e["reason"] for e in exclusions)), "profitable": None})
        return
    split = temporal_split(rows, timestamp(SPEC["split_train_end"]), timestamp(SPEC["split_validation_end"]))
    all_results = [{"hypothesis": h.identifier, "train": evaluate(split["train"], h), "validation": evaluate(split["validation"], h)} for h in HYPOTHESES]
    eligible_results = [r for r in all_results if r["train"]["trades"] >= 20 and r["validation"]["trades"] >= 10 and D(r["train"]["net_pnl"]) > 0 and D(r["validation"]["net_pnl"]) > 0]
    selection_passed = bool(eligible_results)
    development_walk_forward = walk_forward(split["train"] + split["validation"], HYPOTHESES, [timestamp(value) for value in SPEC["walk_forward_development_boundaries"]])
    candidates = eligible_results or [r for r in all_results if r["validation"]["trades"] >= 10] or all_results
    selected = max(candidates, key=lambda r: D(r["validation"]["return_on_turnover"] or "-999"))
    winner = next(h for h in HYPOTHESES if h.identifier == selected["hypothesis"])
    dataset_hash = hashlib.sha256((DATA / "observations.json").read_bytes()).hexdigest()
    freeze_holdout(DATA / "holdout_opened.json", dataset_hash, winner)
    holdout = {name: evaluate(split["test"], winner, slippage_per_share=D(costs[0]), fee_rate=D(costs[1])) for name, costs in SPEC["fixed_cost_scenarios"].items()}
    calibrator = BinCalibrator.fit(split["train"], timestamp(SPEC["split_train_end"]))
    base_prob = [float(r.yes_price) for r in split["test"]]
    outcomes = [r.outcome_yes for r in split["test"]]
    calibrated_prob = [calibrator.predict(r.yes_price, r.decision_at) for r in split["test"]]
    calibration = {"market_baseline": calibration_metrics(base_prob, outcomes), "candidate_train_only": calibration_metrics(calibrated_prob, outcomes), "production_status": "UNPROVEN_NO_PROMOTION"} if outcomes else {}
    base = holdout["base"]
    report = {"status": "EXPLORATORY_POSITIVE" if D(base["net_pnl"]) > 0 else "NO_EDGE", "dataset_sha256": dataset_hash, "catalog_events": len(events), "observations": len(rows), "split_counts": {k: len(v) for k, v in split.items()}, "exclusion_counts": dict(Counter(e["reason"] for e in exclusions)), "selected_hypothesis": winner.identifier, "selection_train_validation_passed": selection_passed, "hypotheses_tested_before_holdout": len(HYPOTHESES), "selection_control": "Fixed candidate family; temporal validation selects one; untouched holdout opened once; no significance claim from selected validation performance", "development_results": all_results, "holdout": holdout, "calibration": calibration, "live_gate": "BLOCKED", "financial_edge_proven": False, "limitations": ["closed-only bounded sample introduces survivor/coverage bias", "first 32 oldest events/day is not the complete market universe", "retrospective Gamma metadata lacks revision history", "historical available_at inferred, never observed", "history is neither executable ask nor historical orderbook", "fills, queue and actual fee schedules unavailable", "redemption delay and collateral equivalence estimated", "drawdown measured on settled cost basis, not intraperiod liquidation", "day clustering can miss cross-day causal dependence", "fixed hypotheses still exploratory; prospective validation required", "no real orders or funds used"], "prospective_protocol": {"status": "REGISTERED_NOT_COMPLETED", "strategy": winner.identifier, "minimum_calendar_days": 90, "minimum_distinct_events": 200, "minimum_day_clusters": 30, "net_pnl_ci_lower_required": ">0 under conservative costs", "stability_hours_required": 72, "fixed_before_observing_results": True, "must_have": ["recorded first_seen_at", "executable book", "market fees", "continuous operation", "independent review and human mandate for live"]}}
    report["walk_forward_development"] = development_walk_forward
    store(REPORTS / "research_historical.json", report)
    fields = ["scenario", "event_id", "market_id", "decision_at", "settlement_at", "side", "observed_price", "simulated_entry_price", "shares", "fees", "cost", "payout", "pnl", "cluster", "availability_verified"]
    with (REPORTS / "research_historical_trades.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for scenario, result in holdout.items():
            writer.writerows({"scenario": scenario, **trade} for trade in result["trade_log"])
    lines = ["# Investigación histórica EdgeHunter", "", f"Estado: **{report['status']}**, exploratorio; live bloqueado.", "", f"Muestra: {len(rows)} mercados/eventos; catálogo: {len(events)} eventos; splits: {report['split_counts']}.", f"Hipótesis elegida antes de abrir test: `{winner.identifier}`; pasó train/validation: {selection_passed}.", "", "| Escenario | Operaciones | PnL ficticio neto | Capital final (inicial 1000) | IC95 por días |", "|---|---:|---:|---:|---|" ]
    for name, result in holdout.items():
        lines.append(f"| {name} | {result['trades']} | {D(result['net_pnl']):.4f} | {D(result['final_capital']):.4f} | {result['bootstrap']['ci95']} |")
    lines += ["", "Los costos son supuestos de sensibilidad, no costos históricos comprobados. Precios históricos sin libro ni disponibilidad histórica verificada: este resultado NO prueba fills realizables ni rentabilidad futura.", "", "Se registraron siete hipótesis, se eligió una con train/validation y se abrió test una vez. No se ajustó la estrategia después de ver test. `data/research/holdout_opened.json` impide reutilizarlo con otra selección.", "", "El protocolo prospectivo requiere datos observados, 90 días, 200 eventos, 30 días independientes aproximados y estabilidad operativa de 72 horas; esos mínimos son diseño propuesto, no garantía estadística. No se han completado durante esta sesión.", "", "Detalle, operaciones individuales, calibración, exclusiones, hashes y raw inputs: `research_historical.json` y `../data/research/`."]
    (REPORTS / "research_historical.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "selected": winner.identifier, "selection_passed": selection_passed, "splits": report["split_counts"], "holdout": {k: {x: v[x] for x in ("net_pnl", "trades", "bootstrap")} for k, v in holdout.items()}}, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="Require all hashed raw inputs in cache; never access network")
    args = parser.parse_args()
    asyncio.run(main(offline=args.offline))
