"""Experiment 3: fixed replication of a published Polymarket tail result.

Registration and evaluation are separate commands. The September dataset was
already inspected in experiment 2, so this is an external-rule replication and
not a new untouched holdout. No threshold/category selection is performed.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal as D
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from edgehunter.research.evaluation import HistoricalObservation, evaluate  # noqa: E402
from edgehunter.research.external_tail import ExternalFavoriteTail  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "research_external"
SOURCE = ROOT / "data" / "research_v2" / "observations.json"
SOURCE_LOCK = ROOT / "data" / "research_v2" / "holdout_opened.json"
REPORT = ROOT / "reports" / "research_external_replication.json"
SUMMARY = ROOT / "reports" / "research_external_replication.md"
TRADES = ROOT / "reports" / "research_external_replication_trades.csv"

PAPER = "https://arxiv.org/abs/2609.12878"
COSTS = {
    "optimistic": {"slippage": D("0.005"), "fee": D("0.04")},
    "base": {"slippage": D("0.01"), "fee": D("0.07")},
    "conservative": {"slippage": D("0.03"), "fee": D("0.10")},
}
SPECIFICATION: dict[str, Any] = {
    "experiment": 3,
    "hypothesis": "Buy the favorite when its observed price is at least 0.90",
    "categories": ["crypto", "politics"],
    "sports_excluded": True,
    "maximum_observed_price": "0.98",
    "maximum_reason": "A minimum 0.01 base slippage must leave entry cost below the unit payout",
    "source": PAPER,
    "source_finding": "Favorite purchases >=0.90 positive in aggregate; two-sided tail pattern robust in Crypto and Politics and absent in Sports",
    "decision_time": "Existing experiment-2 observation at market end minus 24 hours",
    "dataset_status": "PREEXISTING_AND_PREVIOUSLY_INSPECTED; exact external rule not selected on it",
    "selection": "NONE; one fixed external rule; all scenarios reported",
    "cost_scenarios": {name: {key: str(value) for key, value in values.items()} for name, values in COSTS.items()},
    "capital": "1000 SIM_USD; maximum 10 per event and 100 simultaneously locked",
    "gate": "RESEARCH_PAPER_ONLY; cannot establish live profitability",
}


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()


def experiment_two_canonical(value: Any) -> bytes:
    """Match the already-frozen experiment-2 dataset hash byte for byte."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()


def register() -> dict[str, Any]:
    DATA.mkdir(parents=True, exist_ok=True)
    path = DATA / "hypothesis_registry.json"
    value = {
        "registered_at": datetime.now(UTC).isoformat(),
        "specification": SPECIFICATION,
        "specification_sha256": hashlib.sha256(canonical(SPECIFICATION)).hexdigest(),
    }
    if path.exists():
        current = json.loads(path.read_text(encoding="utf-8"))
        if current["specification_sha256"] != value["specification_sha256"] or current["specification"] != SPECIFICATION:
            raise ValueError("immutable external hypothesis registry changed")
        return current
    with path.open("x", encoding="utf-8") as output:
        json.dump(value, output, indent=2, ensure_ascii=False)
    return value


def load_rows() -> tuple[list[HistoricalObservation], str]:
    raw = json.loads(SOURCE.read_text(encoding="utf-8"))
    dataset_hash = hashlib.sha256(experiment_two_canonical(raw)).hexdigest()
    locked = json.loads(SOURCE_LOCK.read_text(encoding="utf-8"))
    if locked["dataset_sha256"] != dataset_hash:
        raise ValueError("source dataset differs from the frozen experiment-2 holdout")
    rows = [HistoricalObservation(
        str(item["event_id"]), str(item["market_id"]), int(item["decision_at"]),
        int(item["price_available_at"]), int(item["settlement_at"]), D(item["yes_price"]),
        D(item["no_price"]), int(item["outcome_yes"]), str(item["cluster_id"]),
        bool(item["historical_availability_verified"]), str(item["category"]),
    ) for item in raw]
    return rows, dataset_hash


def evaluate_registered() -> dict[str, Any]:
    registry_path = DATA / "hypothesis_registry.json"
    if not registry_path.exists():
        raise RuntimeError("register the external rule before opening the existing dataset")
    registry = register()
    rows, dataset_hash = load_rows()
    rule = ExternalFavoriteTail()
    results = {
        name: evaluate(rows, rule, slippage_per_share=cost["slippage"], fee_rate=cost["fee"])
        for name, cost in COSTS.items()
    }
    base, conservative = results["base"], results["conservative"]
    status = "REPLICATION_POSITIVE_BUT_NOT_ROBUST" if D(base["net_pnl"]) > 0 else "REPLICATION_NO_EDGE"
    if D(conservative["net_pnl"]) > 0 and conservative["trades"] >= 10:
        status = "REPLICATION_POSITIVE_CONSERVATIVE_UNDERPOWERED"
    report = {
        "experiment": 3, "status": status, "financial_edge_proven": False,
        "registered_at": registry["registered_at"], "source": PAPER,
        "dataset_sha256": dataset_hash, "observations": len(rows), "rule": asdict(rule),
        "results": results,
        "limitations": [
            "Dataset existed and outcomes were inspected in a prior experiment; this is an external-rule replication, not an untouched holdout",
            "Historical prices are not executable order books and availability is inferred",
            "The source paper measures observed purchases across a much larger trade dataset; this replication samples one event observation 24 hours before end",
            "Current category tags may differ from tags available at decision time",
            "No model or cost scenario was selected from these replication results",
        ],
    }
    REPORT.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str)+"\n", encoding="utf-8")
    fields = ["scenario", "event_id", "market_id", "decision_at", "settlement_at", "side",
              "observed_price", "simulated_entry_price", "shares", "fees", "cost", "payout", "pnl",
              "cluster", "availability_verified"]
    with TRADES.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        for scenario, result in results.items():
            for trade in result["trade_log"]:
                writer.writerow({"scenario": scenario, **trade})
    lines = [
        "# Experimento 3: réplica de una regla publicada", "",
        f"Estado: **{status}**. Dinero ficticio; ventaja financiera no demostrada.", "",
        "Regla congelada antes de calcular: favorito con precio observado >=0.90 y <=0.98, sólo crypto/politics; deportes excluido. No hubo entrenamiento ni búsqueda de umbrales.", "",
        "| Escenario | Operaciones | PnL neto | Capital final | IC95 descriptivo |", "|---|---:|---:|---:|---|",
    ]
    for name, result in results.items():
        lines.append(f"| {name} | {result['trades']} | {result['net_pnl']} | {result['final_capital']} | {result['bootstrap']['ci95']} |")
    lines += ["", f"Fuente externa: {PAPER}.", "",
              "Esta réplica usa un dataset ya inspeccionado y una muestra/diseño diferentes al paper; no convierte un resultado positivo en prueba prospectiva."]
    SUMMARY.write_text("\n".join(lines)+"\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--register", action="store_true")
    args = parser.parse_args()
    result = register() if args.register else evaluate_registered()
    print(json.dumps(result if args.register else {
        "status": result["status"],
        "results": {name: {"trades": value["trades"], "net_pnl": value["net_pnl"],
                           "ci95": value["bootstrap"]["ci95"]} for name, value in result["results"].items()},
    }, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
