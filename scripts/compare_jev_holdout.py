"""Compare JEV against the previous learned baseline model on historical holdout data.

Token-efficient: selects a curated cohort of holdout observations to strictly
bound API costs (<$0.02 USD, ~250 tokens per call).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from edgehunter.intelligence.environment import load_typesafe_env
from edgehunter.intelligence.jev_forecast import JevForecastAdapter
from edgehunter.intelligence.providers import CostBudget
from edgehunter.research.btc_challenger import frozen_model
from edgehunter.research.btc_intraday import IntradayObservation
from edgehunter.research.learned_intraday import predict_up

ROOT = Path(__file__).resolve().parents[1]


def load_holdout_cohort(path: Path, count: int = 10) -> list[IntradayObservation]:
    """Extract a diverse, sequential sample of holdout observations with valid spreads."""
    f = path.open(encoding="utf-8")
    sample = []
    # Holdout starts at 1789195800
    for line in f:
        obs = json.loads(line)
        if obs.get("epoch", 0) < 1789195800:
            continue
        if obs.get("horizon_seconds") != 60:
            continue
        up_p = obs.get("up_price", 0)
        down_p = obs.get("down_price", 0)
        # Skip distorted or zero-liquidity books
        if not (0.05 < up_p < 0.95 and 0.05 < down_p < 0.95):
            continue
        row = IntradayObservation(
            slug=obs["slug"],
            epoch=obs["epoch"],
            horizon_seconds=obs.get("horizon_seconds", 60),
            open_price=float(obs["open_price"]),
            spot_price=float(obs["spot_price"]),
            vol_per_second=float(obs["vol_per_second"]),
            up_price=float(up_p),
            down_price=float(down_p),
            outcome_up=int(obs["outcome_up"]),
            effective_horizon_seconds=obs.get("effective_horizon_seconds", 180),
        )
        sample.append(row)
        if len(sample) >= count:
            break
    f.close()
    return sample


async def run_comparison(cohort_size: int = 10) -> dict[str, Any]:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    load_typesafe_env(ROOT / ".env")
    key = os.environ.get("TYPESAFE_API_KEY", "")
    model_name = os.environ.get("TYPESAFE_MODEL", "jev-latest")

    budget_db = ROOT / "data" / "btc_jev" / "usage.db"
    budget_db.parent.mkdir(parents=True, exist_ok=True)
    budget = CostBudget(budget_db, Decimal("5.0"))

    adapter = JevForecastAdapter(
        api_key=key,
        budget=budget,
        max_call_cost=Decimal("0.001"),
        model=model_name,
        timeout_seconds=5.0,
    )

    # Load previous baseline learned model
    learned_path = ROOT / "reports" / "btc_history_270d_learned.json"
    learned_report = json.loads(learned_path.read_text(encoding="utf-8"))
    spec, baseline_model = frozen_model(learned_report)

    # Load holdout sample
    obs_path = ROOT / "data" / "btc_history_270d" / "merged" / "observations.jsonl"
    cohort = load_holdout_cohort(obs_path, count=cohort_size)

    print("=" * 75)
    print(f">> COMPARACION HISTORICA: JEV ({model_name}) VS MODELO ANTERIOR")
    print(f">> Muestra evaluada: {len(cohort)} observaciones del holdout congelado")
    print("=" * 75)

    results = []
    total_tokens_in = 0
    total_tokens_out = 0

    jev_correct = 0
    baseline_correct = 0
    jev_sq_errors = []
    baseline_sq_errors = []
    market_sq_errors = []

    jev_pnl = Decimal("0")
    baseline_pnl = Decimal("0")
    stake = Decimal("10.0")
    slippage = Decimal("0.03")

    for idx, row in enumerate(cohort, start=1):
        actual = row.outcome_up  # 1 for UP, 0 for DOWN
        market_p = row.up_price / (row.up_price + row.down_price)

        # 1. Baseline Model
        base_p = predict_up(baseline_model, row)
        base_choice = 1 if base_p >= 0.5 else 0
        if base_choice == actual:
            baseline_correct += 1
        baseline_sq_errors.append((base_p - actual) ** 2)
        market_sq_errors.append((market_p - actual) ** 2)

        # Baseline trade decision
        base_edge_up = Decimal(str(round(base_p, 4))) - (Decimal(str(round(row.up_price, 4))) + slippage)
        base_edge_down = Decimal(str(round(1.0 - base_p, 4))) - (Decimal(str(round(row.down_price, 4))) + slippage)
        if base_edge_up >= Decimal("0.06"):
            payout = stake if actual == 1 else Decimal("0")
            baseline_pnl += payout - stake * (Decimal(str(round(row.up_price, 4))) + slippage)
        elif base_edge_down >= Decimal("0.06"):
            payout = stake if actual == 0 else Decimal("0")
            baseline_pnl += payout - stake * (Decimal(str(round(row.down_price, 4))) + slippage)

        # 2. JEV Inference
        try:
            forecast = await adapter.forecast(row, available_until=time.time() + 10.0)
            jev_p = forecast.probability_up
            total_tokens_in += forecast.usage.get("input_tokens", 0)
            total_tokens_out += forecast.usage.get("output_tokens", 0)
        except Exception as exc:
            print(f"⚠️ Error al consultar JEV en fila {idx}: {exc}")
            jev_p = market_p

        jev_choice = 1 if jev_p >= 0.5 else 0
        if jev_choice == actual:
            jev_correct += 1
        jev_sq_errors.append((jev_p - actual) ** 2)

        # JEV trade decision
        jev_edge_up = Decimal(str(round(jev_p, 4))) - (Decimal(str(round(row.up_price, 4))) + slippage)
        jev_edge_down = Decimal(str(round(1.0 - jev_p, 4))) - (Decimal(str(round(row.down_price, 4))) + slippage)
        jev_action = "PASS"
        trade_pnl = Decimal("0")

        if jev_edge_up >= Decimal("0.04"):
            jev_action = "BUY_UP"
            cost = stake * (Decimal(str(round(row.up_price, 4))) + slippage)
            payout = stake if actual == 1 else Decimal("0")
            trade_pnl = payout - cost
            jev_pnl += trade_pnl
        elif jev_edge_down >= Decimal("0.04"):
            jev_action = "BUY_DOWN"
            cost = stake * (Decimal(str(round(row.down_price, 4))) + slippage)
            payout = stake if actual == 0 else Decimal("0")
            trade_pnl = payout - cost
            jev_pnl += trade_pnl

        actual_str = "UP" if actual == 1 else "DOWN"
        jev_choice_str = "UP" if jev_choice == 1 else "DOWN"
        base_choice_str = "UP" if base_choice == 1 else "DOWN"

        jev_status = "GANÓ" if jev_choice == actual else "PERDIÓ"
        base_status = "GANÓ" if base_choice == actual else "PERDIÓ"

        print(
            f"[{idx:02d}/{len(cohort):02d}] Real: {actual_str:4s} | "
            f"JEV: {jev_p:.2f} ({jev_choice_str:4s} -> {jev_status:7s}) | "
            f"Base: {base_p:.2f} ({base_choice_str:4s} -> {base_status:7s}) | "
            f"Accion JEV: {jev_action:8s} | PnL: {trade_pnl:+.2f}"
        )

        results.append({
            "slug": row.slug,
            "actual_outcome": actual_str,
            "jev_probability_up": round(jev_p, 4),
            "jev_prediction": jev_choice_str,
            "jev_correct": bool(jev_choice == actual),
            "jev_action": jev_action,
            "jev_trade_pnl": str(trade_pnl),
            "baseline_probability_up": round(base_p, 4),
            "baseline_prediction": base_choice_str,
            "baseline_correct": bool(base_choice == actual),
            "market_probability_up": round(market_p, 4),
        })

    await adapter.close()

    n = len(cohort)
    jev_brier = sum(jev_sq_errors) / n if n else 0.0
    base_brier = sum(baseline_sq_errors) / n if n else 0.0
    mkt_brier = sum(market_sq_errors) / n if n else 0.0

    jev_acc = (jev_correct / n) * 100.0 if n else 0.0
    base_acc = (baseline_correct / n) * 100.0 if n else 0.0

    print("=" * 75)
    print(">> RESULTADOS COMPARATIVOS:")
    print(f"  * Precision de Aciertos: JEV {jev_acc:.1f}% ({jev_correct}/{n}) vs Anterior {base_acc:.1f}% ({baseline_correct}/{n})")
    print("  * Brier Score (error de calibracion, menor es mejor):")
    print(f"      - JEV:      {jev_brier:.4f}")
    print(f"      - Anterior: {base_brier:.4f}")
    print(f"      - Mercado:  {mkt_brier:.4f}")
    print("  * PnL Simulado en la Muestra:")
    print(f"      - JEV Mandante:   {jev_pnl:+.2f} SIM_USD")
    print(f"      - Modelo Anterior: {baseline_pnl:+.2f} SIM_USD")
    print(f"  * Consumo Total de Tokens: {total_tokens_in} input, {total_tokens_out} output (~${(total_tokens_in+total_tokens_out)*0.000002:.4f} USD)")
    print("=" * 75)

    summary = {
        "as_of": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "model_jev": model_name,
        "sample_size": n,
        "accuracy": {
            "jev_percent": jev_acc,
            "baseline_percent": base_acc,
            "winner": "JEV" if jev_acc > base_acc else ("BASELINE" if base_acc > jev_acc else "TIE"),
        },
        "brier_score": {
            "jev": round(jev_brier, 4),
            "baseline": round(base_brier, 4),
            "market": round(mkt_brier, 4),
            "best": "JEV" if jev_brier < base_brier else "BASELINE",
        },
        "simulated_pnl": {
            "jev_sim_usd": str(jev_pnl),
            "baseline_sim_usd": str(baseline_pnl),
        },
        "token_usage": {
            "input_tokens": total_tokens_in,
            "output_tokens": total_tokens_out,
            "estimated_cost_usd": round((total_tokens_in + total_tokens_out) * 0.000002, 5),
        },
        "cohort_results": results,
    }

    out_file = ROOT / "reports" / "jev_holdout_comparison.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


if __name__ == "__main__":
    asyncio.run(run_comparison(10))
