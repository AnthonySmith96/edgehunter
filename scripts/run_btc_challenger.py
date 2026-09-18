"""Register and run a separate virtual-cash, frozen learned-model experiment."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from edgehunter.ingestion.http import PublicHTTP, SourceError  # noqa: E402
from edgehunter.research.btc_challenger import (  # noqa: E402
    ExperimentJournal,
    load_manifest,
    observe_once,
    register,
    report,
    settle_pending,
    write_report,
)

ROOT = Path(__file__).resolve().parents[1]


async def run(args: argparse.Namespace) -> None:
    manifest, config_hash = load_manifest(args.manifest)
    http = PublicHTTP({"gamma-api.polymarket.com", "clob.polymarket.com", "api.exchange.coinbase.com"}, interval=0.05)
    deadline = None if args.forever else time.monotonic() + args.duration
    try:
        with ExperimentJournal(args.events, config_hash) as journal:
            next_settlement = 0.0
            while True:
                if args.stop_file.exists():
                    write_report(args.report, {**report(journal), "operational_status": "STOPPED"})
                    return
                await observe_once(http, manifest, journal)
                # Settlement is lower priority than the frozen opportunity window.
                if time.monotonic() >= next_settlement:
                    try:
                        await settle_pending(http, journal)
                    except (SourceError, ValueError, TypeError, KeyError, ArithmeticError) as exc:
                        journal.append({"type": "ERROR", "error_class": type(exc).__name__, "reason": str(exc)})
                    next_settlement = time.monotonic() + 30
                result = report(journal)
                write_report(args.report, result)
                print(json.dumps({key: result[key] for key in (
                    "as_of", "opportunities", "predictions", "paper_fills", "settled_fills",
                    "realized_pnl_sim_usd", "cash_sim_usd", "financial_status",
                )}), flush=True)
                if deadline is not None and time.monotonic() >= deadline:
                    return
                await asyncio.sleep(args.interval)
    finally:
        await http.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    registration = commands.add_parser("register", help="freeze the selected model before observations")
    registration.add_argument("--source", type=Path, default=ROOT / "reports/btc_history_270d_learned.json")
    registration.add_argument("--manifest", type=Path, default=ROOT / "config/btc_challenger_v5.json")
    execution = commands.add_parser("run", help="public data and SIM_USD only")
    execution.add_argument("--manifest", type=Path, default=ROOT / "config/btc_challenger_v5.json")
    execution.add_argument("--events", type=Path, default=ROOT / "data/btc_challenger_v5/events.jsonl")
    execution.add_argument("--report", type=Path, default=ROOT / "reports/btc_challenger_live.json")
    execution.add_argument("--stop-file", type=Path, default=ROOT / ".local/btc_challenger.stop")
    execution.add_argument("--duration", type=int, default=3600)
    execution.add_argument("--forever", action="store_true")
    execution.add_argument("--interval", type=float, default=1.0)
    args = parser.parse_args()
    if args.command == "register":
        document = register(args.source, args.manifest)
        print(json.dumps({"registered": str(args.manifest), "source_sha256": document["source_report_sha256"]}))
    else:
        if args.duration < 0 or not 0.5 <= args.interval <= 5:
            parser.error("duration must be nonnegative and interval between 0.5 and 5 seconds")
        asyncio.run(run(args))


if __name__ == "__main__":
    main()
