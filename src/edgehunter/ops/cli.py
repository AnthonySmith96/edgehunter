from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from edgehunter.ops.accounting import export_ledger, period_report
from edgehunter.ops.common import doctor, write_json
from edgehunter.ops.daemon import probe_sources, run_daemon
from edgehunter.ops.demo import run_demo
from edgehunter.ops.lock import InstanceLock
from edgehunter.storage.journal import Journal
from edgehunter.strategies import StrategyRegistry


def default_root() -> Path:
    configured = os.environ.get("EDGEHUNTER_ROOT")
    return Path(configured).resolve() if configured else Path(__file__).resolve().parents[3]


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="edgehunter", description="Investigación auditable y dinero ficticio")
    result.add_argument("--root", type=Path, default=default_root())
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor").add_argument("--redact", action="store_true", default=True)
    demo = commands.add_parser("demo")
    demo.add_argument("--no-pocketbase", action="store_true")
    for command in ("run", "start"):
        run = commands.add_parser(command)
        run.add_argument("--mode", choices=["shadow", "paper"], default="paper")
        run.add_argument("--duration", type=float)
        run.add_argument("--once", action="store_true")
        run.add_argument("--state-dir", type=Path)
        run.add_argument("--pocketbase-url")
        run.add_argument("--daemon-credentials", type=Path)
        run.add_argument("--market-limit", type=int, default=12)
        run.add_argument("--interval", type=float, default=30)
    for command in ("status", "stop", "approvals", "cancel-open", "reconcile", "pause", "backup", "report"):
        entry = commands.add_parser(command)
        entry.add_argument("--state-dir", type=Path)
        if command == "pause":
            entry.add_argument("--reason", required=True)
        if command == "approvals":
            entry.add_argument("action", choices=["list"], default="list", nargs="?")
        if command == "cancel-open":
            entry.add_argument("--scope", choices=["edgehunter"], required=True)
        if command == "reconcile":
            entry.add_argument("--read-only", action="store_true", required=True)
        if command == "report":
            entry.add_argument("--period", choices=["daily", "all"], default="daily")
    restore = commands.add_parser("restore")
    restore.add_argument("--dry-run", type=Path, required=True, dest="backup_path")
    commands.add_parser("sources").add_argument("action", choices=["probe"])
    commands.add_parser("strategies").add_argument("action", choices=["evaluate"])
    commands.add_parser("deployment").add_argument("action", choices=["preflight"])
    commands.add_parser("research")
    return result


def read_status(state: Path) -> dict[str, Any]:
    path = state / "status.json"
    if not path.exists():
        return {"state": "NOT_STARTED", "live_capability": False}
    result: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    age = (datetime.now(UTC)-datetime.fromisoformat(result["as_of"])).total_seconds()
    result["heartbeat_age_seconds"] = round(age, 2)
    if result["state"] != "STOPPED" and age > 90:
        result["state"] = "STALE_OR_STOPPED"
    return result


def start_background(args: argparse.Namespace) -> dict[str, Any]:
    state = args.state_dir or args.root / ".local" / "runtime"
    state.mkdir(parents=True, exist_ok=True)
    with InstanceLock(state / "instance.lock"):
        previous = state / "STOP"
        if previous.exists():
            previous.unlink()
    command = [sys.executable, "-m", "edgehunter", "--root", str(args.root), "run", "--mode", args.mode,
               "--state-dir", str(state), "--market-limit", str(args.market_limit), "--interval", str(args.interval)]
    if args.duration:
        command += ["--duration", str(args.duration)]
    if args.once:
        command += ["--once"]
    for name in ("pocketbase_url", "daemon_credentials"):
        if getattr(args, name):
            command += ["--"+name.replace("_", "-"), str(getattr(args, name))]
    flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    launch_id = uuid.uuid4().hex
    environment = {**os.environ, "EDGEHUNTER_LAUNCH_ID": launch_id}
    with (state / "daemon.log").open("ab") as output:
        process = subprocess.Popen(command, cwd=args.root, stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                                   creationflags=flags, start_new_session=os.name != "nt", env=environment)
    for _ in range(100):
        current = read_status(state)
        if current.get("launch_id") == launch_id and current.get("state") not in {"STARTING", "NOT_STARTED", "STOPPED"}:
            return {"pid": current["pid"], "state": current["state"], "mode": args.mode.upper(),
                    "admin_url": current.get("admin_url"), "credentials_file": current.get("credentials_file"),
                    "log": str(state / "daemon.log"), "live_capability": False}
        if process.poll() is not None:
            raise RuntimeError("Daemon startup failed; inspect restricted .local/runtime/daemon.log")
        time.sleep(0.2)
    return {"pid": process.pid, "state": "STARTING", "log": str(state / "daemon.log")}


def execute(args: argparse.Namespace) -> Any:
    root = args.root.resolve()
    args.root = root
    command = args.command
    state = getattr(args, "state_dir", None) or root / ".local" / "runtime"
    if command == "doctor":
        result = doctor(root)
        write_json(root / "reports" / "doctor.json", result)
        return result
    if command == "demo":
        result = asyncio.run(run_demo(root, pocketbase=not args.no_pocketbase))
        return {key: value for key, value in result.items() if key != "decisions"}
    if command == "sources":
        return asyncio.run(probe_sources(root))
    if command == "strategies":
        return {"strategies": StrategyRegistry().health(), "research_report": str(root / "reports" / "research_historical.json")}
    if command == "research":
        process = subprocess.run([sys.executable, str(root / "scripts" / "research_historical.py")], cwd=root)
        if process.returncode:
            raise RuntimeError("Research failed; preserve dataset and inspect reports")
        return {"reports": str(root / "reports"), "mode": "HISTORICAL_EXPLORATORY"}
    if command == "run":
        return asyncio.run(run_daemon(root, mode=args.mode, duration=args.duration, once=args.once,
            state_dir=state, pocketbase_url=args.pocketbase_url, daemon_credentials=args.daemon_credentials,
            market_limit=args.market_limit, interval=args.interval))
    if command == "start":
        return start_background(args)
    if command == "status":
        return read_status(state)
    if command == "stop":
        state.mkdir(parents=True, exist_ok=True)
        (state / "STOP").write_text("operator requested graceful shutdown", encoding="utf-8")
        return {"state": "STOP_REQUESTED", "scope": str(state)}
    if command == "restore":
        return Journal.inspect_backup(args.backup_path)
    if command == "deployment":
        return {**doctor(root), "deployment": "NOT_DEPLOYED", "blocked": [
            "No authorized remote host/domain", "No external monitor or offsite backup destination",
            "No remote authentication or mobile access test"]}
    path = state / "journal.db"
    if not path.exists():
        raise RuntimeError("No journal exists; start paper or run the demo first")
    journal = Journal(path)
    if command == "pause":
        journal.halt(args.reason)
        return {"state": "PERSISTED_HALT", "reason": args.reason, "existing_positions": journal.report()["positions"]}
    if command == "backup":
        return journal.backup(root / ".local" / "backups" / (datetime.now(UTC).strftime("journal-%Y%m%dT%H%M%S")+".db"))
    if command == "approvals":
        return {"approvals": [{"intent_id": row["intent_id"], "approval_id": row["approval_id"],
                  "state": row["state"], "simulated": True} for row in journal.intents()]}
    if command == "cancel-open":
        from edgehunter.execution import PaperExecutor
        # Avoid a second simulator mutating fills while the daemon owns the journal.
        with InstanceLock(state / "instance.lock"):
            for intent in journal.intents():
                if intent["state"] not in {"UNKNOWN", "SUBMITTING"}:
                    PaperExecutor(journal).cancel(intent["intent_id"])
        return {"scope": "PAPER_ONLY", "journal": journal.report()}
    if command == "reconcile":
        return {"read_only": True, "ledger_valid": journal.validate_ledger(), "scope": "LOCAL_SIMULATION",
                "venue_account": "NO_ACCOUNT_OR_SIGNER", "journal": journal.report()}
    if command == "report":
        result = {**period_report(journal, period=args.period), "kind": "VIRTUAL_MONEY"}
        result["exported_entries"] = export_ledger(journal, root / "reports" / "ledger.csv")
        write_json(root / "reports" / "paper.json", result)
        return result
    raise ValueError("Unknown command")


def main() -> None:
    args = parser().parse_args()
    try:
        result = execute(args)
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except Exception as exc:
        # Do not echo exception bodies: provider errors can contain credentials.
        print(json.dumps({"status": "ERROR", "type": type(exc).__name__,
                          "detail": str(exc) if isinstance(exc, (ValueError, RuntimeError)) else "Inspect local diagnostics"}), file=sys.stderr)
        raise SystemExit(1) from None
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
