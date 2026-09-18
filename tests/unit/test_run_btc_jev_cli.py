import argparse
import asyncio
import importlib.util
import json
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from edgehunter.intelligence.environment import ALLOWED_KEYS
from edgehunter.intelligence.providers import AccessBlocked

SPEC = importlib.util.spec_from_file_location(
    "run_btc_jev", Path(__file__).resolve().parents[2] / "scripts/run_btc_jev.py",
)
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


@pytest.fixture
def isolated_runner(tmp_path, monkeypatch):
    for key in ALLOWED_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    return tmp_path


@pytest.mark.parametrize(
    "dotenv_budget,process_budget,cli_budget,expected",
    [("0", None, None, "0"), ("0", "2", None, "2"),
     ("3", "2", "0", "0"), ("0", "0", "4", "4"),
     (None, None, None, "0")],
)
def test_budget_precedence_and_zero(isolated_runner, monkeypatch, dotenv_budget,
                                    process_budget, cli_budget, expected):
    if dotenv_budget is not None:
        (isolated_runner / ".env").write_text(f"TYPESAFE_BUDGET_USD={dotenv_budget}\n")
    if process_budget is not None:
        monkeypatch.setenv("TYPESAFE_BUDGET_USD", process_budget)
    argv = ["run_btc_jev.py"]
    if cli_budget is not None:
        argv += ["--budget", cli_budget]
    monkeypatch.setattr(runner.sys, "argv", argv)
    run_loop = AsyncMock()
    monkeypatch.setattr(runner, "run_loop", run_loop)

    runner.main()

    assert run_loop.await_args.kwargs["budget_usd"] == Decimal(expected)


@pytest.mark.parametrize("source", ["dotenv", "process", "cli"])
def test_model_and_maximum_call_precedence(isolated_runner, monkeypatch, source):
    (isolated_runner / ".env").write_text(
        "TYPESAFE_MODEL=dotenv-model\nTYPESAFE_MAX_CALL_USD=0.002\n"
    )
    argv = ["run_btc_jev.py"]
    expected_cost = "0.002"
    if source in {"process", "cli"}:
        monkeypatch.setenv("TYPESAFE_MODEL", "process-model")
        monkeypatch.setenv("TYPESAFE_MAX_CALL_USD", "0.003")
        expected_cost = "0.003"
    if source == "cli":
        argv += ["--model", "cli-model", "--maximum-call-usd", "0.004"]
        expected_cost = "0.004"
    monkeypatch.setattr(runner.sys, "argv", argv)
    run_loop = AsyncMock()
    monkeypatch.setattr(runner, "run_loop", run_loop)

    runner.main()

    assert run_loop.await_args.kwargs["model"] == f"{source}-model"
    assert run_loop.await_args.kwargs["max_call_usd"] == Decimal(expected_cost)


@pytest.mark.parametrize("source", ["dotenv", "cli"])
@pytest.mark.parametrize("value", ["-1", "NaN", "Infinity", "invalid"])
def test_invalid_budget_stops_before_runner(isolated_runner, monkeypatch, source, value):
    argv = ["run_btc_jev.py"]
    if source == "dotenv":
        (isolated_runner / ".env").write_text(f"TYPESAFE_BUDGET_USD={value}\n")
    else:
        argv += ["--budget", value]
    monkeypatch.setattr(runner.sys, "argv", argv)
    run_loop = AsyncMock()
    monkeypatch.setattr(runner, "run_loop", run_loop)

    with pytest.raises(SystemExit) as error:
        runner.main()

    assert error.value.code == 2
    run_loop.assert_not_called()


def test_direct_runner_negative_budget_stops_before_clients(isolated_runner, monkeypatch):
    commander = MagicMock()
    http = MagicMock()
    monkeypatch.setattr(runner, "JevCommander", commander)
    monkeypatch.setattr(runner, "PublicHTTP", http)

    with pytest.raises(argparse.ArgumentTypeError):
        asyncio.run(runner.run_loop(
            events_path=isolated_runner / "events.jsonl",
            report_path=isolated_runner / "report.json", stop_path=isolated_runner / "stop",
            budget_usd=Decimal("-1"), model="test", interval=0,
        ))

    commander.assert_not_called()
    http.assert_not_called()
    assert not (isolated_runner / "data").exists()


@pytest.mark.parametrize("cancelled", [False, True])
def test_runtime_policy_zero_budget_and_stopped_report(isolated_runner, monkeypatch, cancelled):
    (isolated_runner / ".env").write_text("TYPESAFE_MAX_CALL_USD=0.007\n")
    commander = MagicMock(close=AsyncMock())
    commander_factory = MagicMock(return_value=commander)
    http = MagicMock(close=AsyncMock())
    monkeypatch.setattr(runner, "JevCommander", commander_factory)
    monkeypatch.setattr(runner, "PublicHTTP", MagicMock(return_value=http))
    monkeypatch.setattr(runner.signal, "signal", MagicMock())
    # Make configuration changes observable even if class defaults match the policy today.
    monkeypatch.setitem(runner.DEFAULT_POLICY, "minimum_edge", "0.08")
    monkeypatch.setitem(runner.DEFAULT_POLICY, "slippage_per_share", "0.02")
    monkeypatch.setitem(runner.DEFAULT_POLICY, "fee_curve_sensitivity", "0.11")
    monkeypatch.setitem(runner.DEFAULT_POLICY, "timeout_seconds", 3.5)
    observe = AsyncMock(side_effect=asyncio.CancelledError() if cancelled else None)
    monkeypatch.setattr(runner, "observe_and_trade", observe)
    stop_path = isolated_runner / "stop"
    if not cancelled:
        stop_path.touch()
    journals = []

    def report_while_locked(journal, _commander):
        assert journal.active
        assert journal.lock.file is not None
        journals.append(journal)
        return {"operational_status": "OBSERVING_AND_READY", "balance_summary": {"cash": "100"}}

    monkeypatch.setattr(runner, "generate_status_report", report_while_locked)
    report_path = isolated_runner / "reports" / "report.json"
    coroutine = runner.run_loop(
        events_path=isolated_runner / "events.jsonl", report_path=report_path,
        stop_path=stop_path, budget_usd=Decimal("0"), model="test", interval=0,
    )
    if cancelled:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(coroutine)
    else:
        asyncio.run(coroutine)

    config = commander_factory.call_args.kwargs
    assert config["budget"].cap == 0
    with pytest.raises(AccessBlocked, match="PROVIDER_BUDGET_EXHAUSTED"):
        config["budget"].reserve(config["max_call_cost"])
    assert config["max_call_cost"] == Decimal("0.007")
    assert config["min_edge"] == Decimal("0.08")
    assert config["slippage_per_share"] == Decimal("0.02")
    assert config["fee_curve_sensitivity"] == Decimal("0.11")
    assert config["timeout_seconds"] == 3.5
    report = json.loads(report_path.read_text())
    assert report["operational_status"] == "STOPPED"
    assert report["stopped_at"]
    assert report["balance_summary"] == {"cash": "100"}
    assert journals and not journals[-1].active
    commander.close.assert_awaited_once()
    http.close.assert_awaited_once()
    if not cancelled:
        observe.assert_not_awaited()


def test_atomic_report_keeps_previous_file_when_replace_fails(tmp_path, monkeypatch):
    path = tmp_path / "report.json"
    path.write_text('{"old": true}')

    def failed_replace(source, destination):
        assert destination == path
        assert json.loads(Path(source).read_text()) == {"new": True}
        assert json.loads(path.read_text()) == {"old": True}
        raise PermissionError("reader has file open")

    monkeypatch.setattr(runner.os, "replace", failed_replace)
    with pytest.raises(PermissionError):
        runner.write_status_report(path, {"new": True})
    assert json.loads(path.read_text()) == {"old": True}
    assert list(tmp_path.iterdir()) == [path]
