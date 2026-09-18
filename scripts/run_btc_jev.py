"""CLI runner for the JEV Executive Trading Bot (BTC 5-minute Polymarket paper trading).

Usage:
    python scripts/run_btc_jev.py [--interval 2.0] [--budget 5.0] [--model jev-latest]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sys
import tempfile
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from edgehunter.ingestion.http import PublicHTTP
from edgehunter.intelligence.environment import load_typesafe_env
from edgehunter.intelligence.jev_commander import JevCommander
from edgehunter.intelligence.providers import CostBudget
from edgehunter.research.btc_jev_runner import (
    DEFAULT_POLICY,
    JevJournal,
    generate_status_report,
    observe_and_trade,
    settle_jev_fills,
    utc_iso,
)

ROOT = Path(__file__).resolve().parents[1]


def nonnegative_decimal(value: str) -> Decimal:
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError("must be a finite nonnegative amount") from exc
    if not result.is_finite() or result < 0:
        raise argparse.ArgumentTypeError("must be a finite nonnegative amount")
    return result


def positive_decimal(value: str) -> Decimal:
    result = nonnegative_decimal(value)
    if result == 0:
        raise argparse.ArgumentTypeError("must be a positive amount")
    return result


def write_status_report(path: Path, report: dict[str, Any]) -> None:
    """Replace a report only after the complete JSON has been written."""
    payload = json.dumps(report, indent=2, allow_nan=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.",
            suffix=".tmp", delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(payload)
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


async def run_loop(
    *,
    events_path: Path,
    report_path: Path,
    stop_path: Path,
    budget_usd: Decimal,
    model: str,
    interval: float,
    duration: float | None = None,
    max_call_usd: Decimal | None = None,
) -> None:
    budget_usd = nonnegative_decimal(str(budget_usd))
    load_typesafe_env(ROOT / ".env")
    max_call_usd = positive_decimal(
        str(max_call_usd) if max_call_usd is not None
        else os.environ.get("TYPESAFE_MAX_CALL_USD", "0.001")
    )
    key = os.environ.get("TYPESAFE_API_KEY", "")
    if not key:
        print("⚠️ Advertencia: TYPESAFE_API_KEY no encontrada en .env", file=sys.stderr)

    timeout = float(os.environ.get("TYPESAFE_TIMEOUT_SECONDS", str(DEFAULT_POLICY["timeout_seconds"])))
    budget_db = ROOT / "data" / "btc_jev" / "usage.db"
    budget_db.parent.mkdir(parents=True, exist_ok=True)
    cost_budget = CostBudget(budget_db, budget_usd)

    commander = JevCommander(
        api_key=key,
        budget=cost_budget,
        model=model,
        max_call_cost=max_call_usd,
        timeout_seconds=timeout,
        min_edge=Decimal(str(DEFAULT_POLICY["minimum_edge"])),
        slippage_per_share=Decimal(str(DEFAULT_POLICY["slippage_per_share"])),
        fee_curve_sensitivity=Decimal(str(DEFAULT_POLICY["fee_curve_sensitivity"])),
        sizing_mode=str(DEFAULT_POLICY["sizing_mode"]),
        kelly_fraction=Decimal(str(DEFAULT_POLICY["kelly_fraction"])),
        max_stake_usd=Decimal(str(DEFAULT_POLICY["maximum_stake_sim_usd"])),
        min_stake_usd=Decimal(str(DEFAULT_POLICY["minimum_stake_sim_usd"])),
        max_entry_price=Decimal(str(DEFAULT_POLICY["max_entry_price"])),
    )

    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    http = PublicHTTP(
        {"gamma-api.polymarket.com", "clob.polymarket.com", "api.exchange.coinbase.com"},
        interval=0.05,
    )

    print("=" * 70)
    print(">> EDGEHUNTER - JEV EXECUTIVE MANDANTE INICIADO")
    print(f">> Modelo Jev: {model}")
    print(f">> Dimensionamiento: {DEFAULT_POLICY['sizing_mode'].upper()} (Fraccion: {DEFAULT_POLICY['kelly_fraction']})")
    print(f">> Rango de Apuesta: {DEFAULT_POLICY['minimum_stake_sim_usd']} a {DEFAULT_POLICY['maximum_stake_sim_usd']} SIM_USD")
    print(f">> Limite Maximo de Entrada: {DEFAULT_POLICY['max_entry_price']} (Filtro Asimetria)")
    print(f">> Presupuesto API asignado: {budget_usd} USD")
    print(">> Mercado: Bitcoin 5-minute Up/Down (Polymarket CLOB)")
    print(f">> Diario: {events_path}")
    print(f">> Reporte en vivo: {report_path}")
    print("=" * 70)

    stop_requested = False

    def handle_signal(*_: object) -> None:
        nonlocal stop_requested
        stop_requested = True

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    deadline = time.monotonic() + duration if duration else None
    next_settlement = 0.0
    next_heartbeat = time.monotonic() + 10.0

    pid_path = ROOT / ".local" / "btc_jev.pid"
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(str(os.getpid()), encoding="utf-8")

    try:
        with JevJournal(events_path) as journal:
            try:
                while not stop_requested:
                    if stop_path.exists():
                        print("\n[STOP] Archivo stop detectado. Deteniendo Jev Runner...", flush=True)
                        break
                    if deadline and time.monotonic() >= deadline:
                        break

                    try:
                        result = await observe_and_trade(http, commander, journal)
                        if result:
                            action = result.get("action")
                            slug = result.get("slug")
                            if action == "ENTER":
                                side = result.get("side")
                                cost = result.get("cost")
                                print(f"[JEV ORDENO ENTRADA] {slug} -> {side} (Costo: {cost} SIM_USD)", flush=True)
                            elif action == "PASS":
                                reason = result.get("reason")
                                print(f"[JEV DECIDIO PASAR] {slug} ({reason})", flush=True)

                        settled = 0
                        if time.monotonic() >= next_settlement:
                            settled = await settle_jev_fills(http, journal)
                            next_settlement = time.monotonic() + 15.0

                        report = generate_status_report(journal, commander)
                        b_sum = report.get("balance_summary", {})
                        total_eq = b_sum.get("total_equity_sim_usd", "100")
                        cash_disp = b_sum.get("cash_available_sim_usd", "100")
                        in_play = b_sum.get("capital_in_play_sim_usd", "0")
                        pnl_real = b_sum.get("realized_pnl_sim_usd", "0")
                        rec = b_sum.get("record", "0W - 0L")
                        wr = b_sum.get("win_rate_percent", 0.0)
                        streak = b_sum.get("current_streak", "")

                        if result and result.get("action") == "ENTER":
                            print(f"   >> [BALANCE TRAS ENTRADA] Disp: {cash_disp} SIM_USD | En Juego: {in_play} SIM_USD | PnL Realizado: {pnl_real} SIM_USD", flush=True)

                        if settled > 0:
                            print("=" * 70, flush=True)
                            print(f">> [LIQUIDACION EXITOSA] {settled} apuesta(s) cerradas por Polymarket.", flush=True)
                            print(f">> [SALDO EN VIVO] Total Equity: {total_eq} SIM_USD (Efectivo: {cash_disp} | En Juego: {in_play})", flush=True)
                            print(f">> [DESEMPEÑO] PnL: {pnl_real} SIM_USD | Record: {rec} ({wr}%)", flush=True)
                            print(f">> [RACHA ACTUAL] {streak}", flush=True)
                            print("=" * 70, flush=True)

                        if time.monotonic() >= next_heartbeat:
                            print(f"[ESTADO {utc_iso()[:19]}] Equity: {total_eq} SIM_USD | PnL: {pnl_real} SIM_USD | Record: {rec} ({wr}%) | {streak}", flush=True)
                            next_heartbeat = time.monotonic() + 60.0

                        try:
                            write_status_report(report_path, report)
                        except OSError:
                            pass

                    except Exception as exc:
                        print(f"[ERROR] Error en ciclo: {type(exc).__name__}: {exc}", flush=True)

                    await asyncio.sleep(interval)
            finally:
                try:
                    report = generate_status_report(journal, commander)
                    report["operational_status"] = "STOPPED"
                    report["stopped_at"] = utc_iso()
                    write_status_report(report_path, report)
                except OSError as exc:
                    print(f"[ERROR] No se pudo guardar el estado final: {exc}", flush=True)
    finally:
        if pid_path.exists():
            try:
                pid_path.unlink()
            except OSError:
                pass
        await commander.close()
        await http.close()
        print(">> Jev Executive Runner finalizado.", flush=True)


def main() -> None:
    load_typesafe_env(ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--budget", type=nonnegative_decimal, default=os.environ.get("TYPESAFE_BUDGET_USD", "0"))
    parser.add_argument(
        "--maximum-call-usd", type=positive_decimal,
        default=os.environ.get("TYPESAFE_MAX_CALL_USD", "0.001"),
    )
    parser.add_argument("--model", type=str, default=os.environ.get("TYPESAFE_MODEL", "jev-latest"))
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--duration", type=float, default=None)
    parser.add_argument("--events", type=Path, default=ROOT / "data" / "btc_jev" / "events.jsonl")
    parser.add_argument("--report", type=Path, default=ROOT / "reports" / "btc_jev_live.json")
    parser.add_argument("--stop-file", type=Path, default=ROOT / ".local" / "btc_jev.stop")
    args = parser.parse_args()

    asyncio.run(
        run_loop(
            events_path=args.events,
            report_path=args.report,
            stop_path=args.stop_file,
            budget_usd=args.budget,
            max_call_usd=args.maximum_call_usd,
            model=args.model,
            interval=args.interval,
            duration=args.duration,
        )
    )


if __name__ == "__main__":
    main()
