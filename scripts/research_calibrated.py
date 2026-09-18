"""Experiment 2: select on previously inspected data, freeze, then open September.

Run once online, then reproduce with --offline. All money is virtual.
The old holdout is now development data; experiment 1 is preserved unchanged.
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
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from edgehunter.research.calibrated import (  # noqa: E402
    CalibratedFavorite,
    CalibrationSpec,
    market_category,
)
from edgehunter.research.evaluation import (  # noqa: E402
    HistoricalObservation,
    calibration_metrics,
    cluster_bootstrap,
    evaluate,
)

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "research_v2"
OLD = ROOT / "data" / "research"
REPORTS = ROOT / "reports"


def timestamp(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()


def store(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    temporary.replace(path)


def freeze(path: Path, value: Any) -> None:
    """Idempotent replay only; do not rewrite the preregistration or fitted model."""
    if path.exists():
        if canonical(json.loads(path.read_text(encoding="utf-8"))) != canonical(value):
            raise ValueError(f"immutable experiment changed: {path.name}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, default=str)


SPECS = [CalibrationSpec(prior, D(z), category) for prior in (10, 30, 100) for z in ("0", "0.5", "1") for category in (False, True)]
ANCHORS = [(datetime(2026, 9, 2, tzinfo=timezone.utc) + timedelta(days=i)).date().isoformat() for i in range(16)]
START = timestamp("2026-09-01T00:00:00Z")
END = timestamp("2026-09-17T00:00:00Z")
FOLDS = [timestamp(value) for value in ("2026-07-01T00:00:00Z", "2026-07-16T00:00:00Z", "2026-08-08T00:00:00Z", "2026-09-01T00:00:00Z")]
COSTS = {"optimistic": ["0.005", "0.04"], "base": ["0.01", "0.07"], "conservative": ["0.03", "0.10"]}
SPEC = {
    "experiment": 2,
    "status": "RETROSPECTIVE_HOLDOUT_REGISTERED_BEFORE_NEW_DATA_DOWNLOAD_NOT_PROSPECTIVE_TRADING",
    "development": "Previously inspected experiment 1 observations, decisions June-August 2026, now explicitly contaminated development; mature settlement plus 24h embargo before each fit",
    "independence": "Disjoint dates and event IDs, not proven statistical independence",
    "hypotheses": [{"id": spec.identifier, **asdict(spec)} for spec in SPECS],
    "selection": "3 expanding development folds; eligible total trades>=10, at least2 positive folds, total pnl>0; choose eligible max total PnL or best >=10 trades as unproven audit; tie by identifier; final fit mature development only",
    "development_boundaries": FOLDS,
    "holdout_start": START,
    "holdout_end_exclusive": END,
    "anchors": ANCHORS,
    "universe": "First 32 closed Gamma events by ascending ID on each anchor; first binary Yes/No market by ascending ID with decision_at within holdout, before examining its terminal price; no fallback market after bad history or unresolved label",
    "decision": "market endDate minus24h; created before decision; exact prices last available at/before decision minus60s; max2h stale; availability inferred",
    "settlement": "Exact terminal [1,0] or [0,1], UMA resolved, closed after decision; closedTime+24h <= preregistered as_of timestamp",
    "signals": "Selected calibrated favorite lower heuristic bound exceeds observed price+0.01+0.07*p*(1-p) by0.0025; max observed0.98; sensitivity retains base-cost signal rule",
    "cost_scenarios": COSTS,
    "money": "1000 fictitious units, max10 loss/event, max100 locked, minimum5 shares assumed, no leverage",
    "seed": 20260917,
    "gate": "RESEARCH_PAPER_ONLY; heuristic intervals not validated; never enables real orders",
    "prior_experiment_count": 1,
    "prior_hypotheses": 7,
    "prospective_validation_required": True,
}


def development() -> tuple[list[HistoricalObservation], dict[str, Any]]:
    categories: dict[str, str] = {}
    category_provenance = []
    for meta_path in sorted((OLD / "raw").glob("*.meta.json")):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if not meta["url"].startswith("https://gamma-api.polymarket.com/events?"):
            continue
        raw_path = meta_path.with_name(meta_path.name.replace(".meta.json", ".json"))
        raw = raw_path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != meta["sha256"]:
            raise ValueError("development category checksum mismatch")
        category_provenance.append({"path": str(raw_path.relative_to(ROOT)), "sha256": meta["sha256"]})
        for event in json.loads(raw):
            categories[str(event["id"])] = market_category([str(tag.get("slug", "")) for tag in event.get("tags", [])])
    original = (OLD / "observations.json").read_bytes()
    rows = []
    exclusions: Counter[str] = Counter()
    for item in json.loads(original):
        if not timestamp("2026-06-01T00:00:00Z") <= item["decision_at"] < START:
            exclusions["decision_outside_development_window"] += 1
            continue
        if item["settlement_at"] + 86400 >= START - 1:
            exclusions["label_not_mature_before_final_cutoff_and_embargo"] += 1
            continue
        rows.append(HistoricalObservation(str(item["event_id"]), str(item["market_id"]), item["decision_at"], item["price_available_at"], item["settlement_at"], D(item["yes_price"]), D(item["no_price"]), item["outcome_yes"], item["cluster_id"], False, categories.get(str(item["event_id"]), "unknown")))
    return rows, {"previous_observations_sha256": hashlib.sha256(original).hexdigest(), "categories_from_current_metadata_not_historically_verified": True, "category_provenance": category_provenance, "exclusions": dict(exclusions)}


def select_model(rows: list[HistoricalObservation]) -> tuple[CalibratedFavorite, list[dict[str, Any]], bool]:
    candidates = []
    for spec in SPECS:
        folds = []
        for start, end in zip(FOLDS, FOLDS[1:]):
            evaluation_rows = [row for row in rows if start <= row.decision_at < end]
            clusters = {row.cluster_id for row in evaluation_rows}
            training = [row for row in rows if row.settlement_at + 86400 < start - 1 and row.cluster_id not in clusters]
            fitted = CalibratedFavorite.fit(training, spec, start - 1)
            result = evaluate(evaluation_rows, fitted)
            folds.append({"start": start, "end": end, "training_events": len(training), "evaluation_events": len(evaluation_rows), "result": result})
        total_pnl = sum((D(fold["result"]["net_pnl"]) for fold in folds), D(0))
        trades = sum(fold["result"]["trades"] for fold in folds)
        positive_folds = sum(D(fold["result"]["net_pnl"]) > 0 for fold in folds)
        candidates.append({"identifier": spec.identifier, "trades": trades, "positive_folds": positive_folds, "total_pnl": str(total_pnl), "eligible": trades >= 10 and positive_folds >= 2 and total_pnl > 0, "folds": folds})
    eligible = [candidate for candidate in candidates if candidate["eligible"]]
    pool = eligible or [candidate for candidate in candidates if candidate["trades"] >= 10] or candidates
    selected = max(pool, key=lambda candidate: (D(candidate["total_pnl"]), candidate["identifier"]))
    spec = next(spec for spec in SPECS if spec.identifier == selected["identifier"])
    return CalibratedFavorite.fit(rows, spec, START - 1), candidates, bool(eligible)


async def main(*, offline: bool = False) -> None:
    registry_path = DATA / "hypothesis_registry.json"
    if registry_path.exists():
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        if canonical(registry["specification"]) != canonical(SPEC):
            raise ValueError("experiment specification changed")
    else:
        if offline:
            raise RuntimeError("offline replay requires a previously frozen experiment")
        registry = {"registered_at": datetime.now(timezone.utc).isoformat(), "as_of_timestamp": int(datetime.now(timezone.utc).timestamp()), "specification": SPEC}
        freeze(registry_path, registry)
    rows, provenance = development()
    development_serialized = [asdict(row) for row in rows]
    freeze(DATA / "development_observations.json", development_serialized)
    model, candidates, selection_passed = select_model(rows)
    freeze(DATA / "development_results.json", {"provenance": provenance, "candidates": candidates})
    frozen_model = {"policy": "RESEARCH_PAPER_ONLY", "financial_edge_proven": False, "selection_development_passed": selection_passed, "development_sha256": hashlib.sha256(canonical(development_serialized)).hexdigest(), "snapshot": model.snapshot(), "specification_sha256": hashlib.sha256(canonical(SPEC)).hexdigest(), "registered_at": registry["registered_at"]}
    # This write occurs before any September catalog/outcome/history request.
    freeze(DATA / "frozen_model.json", frozen_model)
    print(f"Model frozen before September download: {model.identifier}; development gate={selection_passed}; training={len(rows)}", flush=True)
    exclusions: list[dict[str, Any]] = []
    manifest: list[dict[str, Any]] = []
    semaphore = asyncio.Semaphore(4)
    holdout: list[HistoricalObservation] = []
    sources: list[dict[str, Any]] = []
    development_ids = {row.event_id for row in rows}
    all_previous_ids = {str(item["event_id"]) for item in json.loads((OLD / "observations.json").read_text(encoding="utf-8"))}
    async with httpx.AsyncClient(verify=ssl.create_default_context(), timeout=30, headers={"User-Agent": "EdgeHunter-Research/0.2 public-read-only"}) as client:
        async def fetch(endpoint: str, params: dict[str, Any]) -> Any:
            key = hashlib.sha256(canonical([endpoint, params])).hexdigest()
            path = DATA / "raw" / f"{key}.json"
            meta_path = path.with_suffix(".meta.json")
            if path.exists():
                raw = path.read_bytes()
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                if hashlib.sha256(raw).hexdigest() != meta["sha256"]:
                    raise ValueError("holdout raw cache checksum mismatch")
                manifest.append(meta)
                return json.loads(raw)
            if offline:
                raise RuntimeError(f"offline replay lacks cached response {key}")
            async with semaphore:
                for attempt in range(3):
                    response = await client.get(endpoint, params=params)
                    if response.status_code not in (429, 500, 502, 503, 504):
                        break
                    await asyncio.sleep(attempt + 1)
                response.raise_for_status()
                raw = response.content
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
                meta = {"url": str(response.url), "retrieved_at": datetime.now(timezone.utc).isoformat(), "sha256": hashlib.sha256(raw).hexdigest(), "path": str(path.relative_to(ROOT)), "status": response.status_code, "source": "Polymarket public Gamma/CLOB API"}
                store(meta_path, meta)
                manifest.append(meta)
                return response.json()

        async def events_on(day: str) -> list[dict[str, Any]]:
            end = (datetime.fromisoformat(day) + timedelta(days=1)).date().isoformat()
            try:
                return list(await fetch("https://gamma-api.polymarket.com/events", {"closed": "true", "limit": 32, "end_date_min": day, "end_date_max": end, "order": "id", "ascending": "true"}))
            except httpx.HTTPError as exc:
                exclusions.append({"anchor": day, "reason": "catalog_fetch_error", "error": type(exc).__name__})
                return []

        batches = await asyncio.gather(*(events_on(day) for day in ANCHORS))
        events = {str(event["id"]): event for batch in batches for event in batch}
        print(f"New catalog: {len(events)} events", flush=True)

        async def process(event: dict[str, Any]) -> None:
            event_id = str(event["id"])
            if event_id in all_previous_ids:
                exclusions.append({"event_id": event_id, "reason": "event_seen_in_prior_experiment"})
                return
            chosen = None
            for market in sorted(event.get("markets", []), key=lambda item: int(item["id"])):
                try:
                    decision = timestamp(market["endDate"]) - 86400
                    if json.loads(market.get("outcomes", "[]")) != ["Yes", "No"] or not START <= decision < END:
                        continue
                    if timestamp(market["createdAt"]) >= decision:
                        continue
                    tokens = json.loads(market["clobTokenIds"])
                    if len(tokens) != 2:
                        continue
                    chosen = market, tokens, decision
                    break
                except (KeyError, TypeError, ValueError):
                    continue
            if chosen is None:
                exclusions.append({"event_id": event_id, "reason": "no_binary_market_with_decision_in_frozen_window"})
                return
            market, tokens, decision = chosen
            try:
                prices = json.loads(market.get("outcomePrices", "[]"))
                closed = timestamp(market["closedTime"])
                settlement = closed + 86400
                if prices not in (["1", "0"], ["0", "1"]) or market.get("umaResolutionStatus") != "resolved" or closed <= decision or settlement > registry["as_of_timestamp"]:
                    exclusions.append({"event_id": event_id, "reason": "label_unresolved_early_or_redemption_not_mature_as_of_freeze"})
                    return
                histories = []
                for token in tokens:
                    payload = await fetch("https://clob.polymarket.com/prices-history", {"market": token, "startTs": decision - 7200, "endTs": decision, "fidelity": 5})
                    points = [point for point in payload.get("history", []) if decision - 7200 <= int(point["t"]) <= decision - 60 and 0 < D(str(point["p"])) < 1]
                    if not points:
                        exclusions.append({"event_id": event_id, "market_id": market["id"], "reason": "missing_or_stale_predecision_history"})
                        return
                    histories.append(max(points, key=lambda point: point["t"]))
            except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
                exclusions.append({"event_id": event_id, "market_id": market["id"], "reason": "invalid_label_or_history_fetch_error", "error": type(exc).__name__})
                return
            category = market_category([str(tag.get("slug", "")) for tag in event.get("tags", [])])
            row = HistoricalObservation(event_id, str(market["id"]), decision, max(int(point["t"]) + 60 for point in histories), settlement, D(str(histories[0]["p"])), D(str(histories[1]["p"])), int(prices[0]), datetime.fromtimestamp(decision, timezone.utc).date().isoformat(), False, category)
            holdout.append(row)
            sources.append({**asdict(row), "question": market["question"], "history_points": histories, "contract_hash_current_only": hashlib.sha256(market.get("description", "").encode()).hexdigest(), "availability_quality": "INFERRED_NOT_OBSERVED", "redemption_quality": "ESTIMATED_CLOSED_PLUS_24H", "category_quality": "CURRENT_METADATA_NOT_HISTORICAL"})

        await asyncio.gather(*(process(event) for event in events.values()))
    sources.sort(key=lambda item: (item["decision_at"], item["event_id"]))
    holdout.sort(key=lambda row: (row.decision_at, row.event_id))
    if development_ids & {row.event_id for row in holdout}:
        raise ValueError("holdout development overlap")
    freeze(DATA / "observations.json", sources)
    store(DATA / "manifest.json", sorted(manifest, key=lambda item: item["path"]))
    store(DATA / "exclusions.json", sorted(exclusions, key=lambda item: (str(item.get("event_id", "")), str(item.get("anchor", "")), item["reason"])))
    frozen_hash = hashlib.sha256(canonical(frozen_model)).hexdigest()
    dataset_hash = hashlib.sha256(canonical(sources)).hexdigest()
    freeze(DATA / "holdout_opened.json", {"frozen_model_sha256": frozen_hash, "dataset_sha256": dataset_hash, "policy": "replay same model and dataset only; no retuning this holdout"})
    results = {}
    for name, (slippage, fee) in COSTS.items():
        result = evaluate(holdout, model, slippage_per_share=D(slippage), fee_rate=D(fee))
        zero_days = [(cluster, 0.0) for cluster in sorted({row.cluster_id for row in holdout})]
        result["observed_day_bootstrap"] = cluster_bootstrap(zero_days + [(trade["cluster"], float(trade["pnl"])) for trade in result["trade_log"]])
        results[name] = result
    base = results["base"]
    status = "EXPLORATORY_POSITIVE" if D(base["net_pnl"]) > 0 else ("NO_TRADES_NO_EDGE" if not base["trades"] else "NO_EDGE")
    if not holdout:
        status = "DATA_INSUFFICIENT"
    calibration = {}
    if holdout:
        outcomes = [row.outcome_yes for row in holdout]
        calibration = {"market_baseline": calibration_metrics([float(row.yes_price) for row in holdout], outcomes), "frozen_model": calibration_metrics([float(model.estimate_yes(row.yes_price, row.category, row.decision_at)[0]) for row in holdout], outcomes)}
    report = {"experiment": 2, "status": status, "financial_edge_proven": False, "policy": "RESEARCH_PAPER_ONLY", "live_gate": "BLOCKED", "selected_hypothesis": model.identifier, "selection_development_passed": selection_passed, "registered_at": registry["registered_at"], "development_observations": len(rows), "catalog_events": len(events), "holdout_observations": len(holdout), "exclusion_counts": dict(Counter(item["reason"] for item in exclusions)), "category_counts": dict(Counter(row.category for row in holdout)), "frozen_model_sha256": frozen_hash, "dataset_sha256": dataset_hash, "holdout": results, "calibration": calibration, "experiments_observed_including_this": 2, "candidate_hypotheses_across_experiments": 7 + len(SPECS), "multiple_testing": "Two reported experiments, 25 total candidate specifications; no familywise significance or profitability claim; old holdout is contaminated development for experiment2", "limitations": ["retrospective closed-only sample with coverage and survival bias", "bounded first32 event catalog per day is not entire universe", "current tags/contracts/endDates can differ from historically available metadata", "price history lacks executable asks, queue, actual fill and fees", "availability and redemption delay are inferred", "uncertainty bounds heuristic not validated confidence intervals", "day bootstrap descriptive; correlation can cross days; at most16 observed days", "development selected 1 of18 model specifications after failed experiment1", "first binary market selected before its label, but sample eligibility still depends on resolved market", "both experiments are historical hypothetical trades, not prospective paper fills"], "reproduction": ".venv\\Scripts\\python.exe scripts/research_calibrated.py --offline"}
    store(REPORTS / "research_calibrated.json", report)
    csv_path = REPORTS / "research_calibrated_trades.csv"
    fields = ["scenario", "event_id", "market_id", "decision_at", "settlement_at", "side", "observed_price", "simulated_entry_price", "shares", "fees", "cost", "payout", "pnl", "cluster", "availability_verified"]
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for scenario, result in results.items():
            writer.writerows({"scenario": scenario, **trade} for trade in result["trade_log"])
    lines = ["# Experimento 2: calibración regularizada", "", f"Estado: **{status}**. Sólo simulación; edge financiero no demostrado; live bloqueado.", "", f"Modelo congelado antes de descargar septiembre: `{model.identifier}`.", f"Desarrollo: {len(rows)} observaciones ya inspeccionadas del experimento1; selección walk-forward pasó: {selection_passed}.", f"Holdout fijo: decisiones del 1 al 16 de septiembre2026; {len(holdout)} observaciones de {len(events)} eventos consultados.", "", "| Escenario | Operaciones | PnL ficticio neto | Capital final (1000 inicial) | IC95 descriptivo por días observados |", "|---|---:|---:|---:|---|"]
    for name, result in results.items():
        lines.append(f"| {name} | {result['trades']} | {D(result['net_pnl']):.4f} | {D(result['final_capital']):.4f} | {result['observed_day_bootstrap']['ci95']} |")
    lines += ["", "Se preserva el fracaso del experimento1. Sus datos ya fueron observados y son desarrollo para el2; las fechas/eventos nuevos no se reutilizaron para elegir modelo. Los escenarios cambian costos y restricciones de ejecución, manteniendo la regla de señal base.", "", "Se informan ambos experimentos y las25 especificaciones consideradas (7+18). Un saldo positivo es un resultado histórico exploratorio, no evidencia de rentabilidad real. Los intervalos por días son descriptivos y pueden subestimar dependencia entre eventos/días.", "", "No existen books, fills ni fees históricos verificados; timestamps de disponibilidad, categorías vigentes y demora de redención son aproximaciones. No se ha completado una prueba prospectiva.", "", "Reproducir: `.venv\\Scripts\\python.exe scripts/research_calibrated.py --offline`. Datos/hash/modelo: `../data/research_v2/`. Operaciones: `research_calibrated_trades.csv`."]
    (REPORTS / "research_calibrated.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"status": status, "observations": len(holdout), "exclusions": report["exclusion_counts"], "holdout": {name: {key: result[key] for key in ("trades", "net_pnl", "observed_day_bootstrap")} for name, result in results.items()}}, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="Replay frozen inputs without network")
    args = parser.parse_args()
    asyncio.run(main(offline=args.offline))
