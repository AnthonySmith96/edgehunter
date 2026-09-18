import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from edgehunter.intelligence.jev_commander import JevDecision
from edgehunter.research import btc_jev_runner as engine
from edgehunter.research.btc_jev_runner import (
    JevJournal,
    balances,
    calibration_report,
    compute_btc_features,
    generate_status_report,
    observe_and_trade,
    settle_jev_fills,
)


def test_jev_journal_accounting(tmp_path):
    journal_path = tmp_path / "events.jsonl"
    with JevJournal(journal_path) as journal:
        journal.append({"type": "OPPORTUNITY", "slug": "btc-5m-1"})
        journal.append({
            "type": "PAPER_FILL",
            "slug": "btc-5m-1",
            "quantity": "20.0",
            "total_cost_sim_usd": "10.0",
        })
        b = balances(journal.events)
        assert b["cash"] == Decimal("90.0")
        assert b["realized_pnl"] == Decimal("0")

        # Settlement win
        journal.append({
            "type": "SETTLEMENT",
            "slug": "btc-5m-1",
            "payout": "20.0",
            "pnl_sim_usd": "10.0",
            "won": True,
        })
        b = balances(journal.events)
        assert b["cash"] == Decimal("110.0")
        assert b["realized_pnl"] == Decimal("10.0")

    # Verify reload from disk
    with JevJournal(journal_path) as reloaded:
        assert len(reloaded.events) == 3
        assert reloaded.attempted("btc-5m-1")


def candle_fixture(epoch=9900):
    return [[t, 59900, 60300, 60000, 60050 + (t % 120), 10]
            for t in range(epoch - 1800, epoch + 60, 60)]


def ticker_fixture(now=10050):
    return {"price": "60200.0", "time": datetime.fromtimestamp(now, UTC).isoformat()}


def book_fixture(token="tok_up", now=10050, ask="0.50"):
    return {"asset_id": token, "timestamp": now * 1000, "min_order_size": "0.1",
            "bids": [{"price": str(Decimal(ask) - Decimal("0.01")), "size": "100"}],
            "asks": [{"price": ask, "size": "100"}]}


def market_fixture():
    return {"markets": [{"id": "m1", "clobTokenIds": ["tok_up", "tok_down"],
                         "outcomes": ["Up", "Down"], "closed": False, "acceptingOrders": True}]}


def test_compute_btc_features():
    spot, open_p, vol = compute_btc_features(
        epoch=9900, candles=candle_fixture(), ticker=ticker_fixture(), now=10050,
    )
    assert spot == 60200.0
    assert open_p == 60000.0
    assert vol > 0


def test_observe_and_trade_executes_fill_when_jev_commands_enter(tmp_path):
    async def scenario():
        journal_path = tmp_path / "events.jsonl"
        mock_http = MagicMock()
        mock_http.json = AsyncMock()

        # Mock Polymarket event
        now_test = 9900.0 + 150.0
        mock_http.json.side_effect = [
            # Gamma event
            market_fixture(),
            # CLOB Up book
            {
                "asset_id": "tok_up",
                "timestamp": int(now_test * 1000),
                "min_order_size": "0.1",
                "bids": [{"price": "0.49", "size": "100"}],
                "asks": [{"price": "0.50", "size": "100"}],
            },
            # CLOB Down book
            {
                "asset_id": "tok_down",
                "timestamp": int(now_test * 1000),
                "min_order_size": "0.1",
                "bids": [{"price": "0.49", "size": "100"}],
                "asks": [{"price": "0.50", "size": "100"}],
            },
            # Coinbase candles
            candle_fixture(),
            # Coinbase ticker
            ticker_fixture(),
            book_fixture(),
        ]

        mock_commander = MagicMock()
        mock_commander.model = "jev-latest"
        mock_commander.depth_fraction = Decimal("0.8")
        mock_commander.decide = AsyncMock(return_value=JevDecision(
            action="ENTER",
            side="UP",
            asset_id="tok_up",
            probability_up=0.70,
            probability_down=0.30,
            edge=Decimal("0.15"),
            chosen_ask=Decimal("0.50"),
            limit_price=Decimal("0.53"),
            fee_per_share=Decimal("0.02"),
            total_cost=Decimal("10.0"),
            quantity=Decimal("18.18"),
            stake_usd=Decimal("10.0"),
            jev_latency_ms=140.0,
            jev_model="jev-latest",
            reason="JEV_COMMAND_ENTER_UP",
            tokens_used={"input_tokens": 100, "output_tokens": 10},
            state_dossier={},
        ))

        with JevJournal(journal_path) as journal:
            # 10000 // 300 * 300 = 9900. remaining at 9900 + 150 = 150 seconds (<= horizon 160)
            now_test = 9900 + 150
            result = await observe_and_trade(mock_http, mock_commander, journal, now=float(now_test))
            assert result is not None
            assert result["action"] == "ENTER"
            assert result["side"] == "UP"
            fills = [e for e in journal.events if e["type"] == "PAPER_FILL"]
            assert len(fills) == 1
            assert fills[0]["side"] == "UP"
            assert fills[0]["asset_id"] == "tok_up"
            assert fills[0]["jev_model"] == "jev-latest"

            report = generate_status_report(journal, mock_commander)
            assert report["paper_fills_executed"] == 1
            assert report["commander_model"] == "jev-latest"

    asyncio.run(scenario())


@pytest.mark.parametrize("change, reason", [
    ("stale", "STALE_OR_FUTURE_TICKER"),
    ("future", "STALE_OR_FUTURE_TICKER"),
    ("missing_open", "MISSING_OPEN_CANDLE"),
    ("missing_recent", "INSUFFICIENT_CONTIGUOUS_VOLATILITY_CANDLES"),
    ("nan", "INVALID_TICKER_PRICE"),
])
def test_features_reject_unusable_evidence(change, reason):
    candles, ticker = candle_fixture(), ticker_fixture()
    if change == "stale":
        ticker = ticker_fixture(10000)
    elif change == "future":
        ticker = ticker_fixture(10060)
    elif change == "missing_open":
        candles = candles[:-1]
    elif change == "missing_recent":
        candles = [c for c in candles if c[0] != 9840]
    elif change == "nan":
        ticker["price"] = "nan"
    with pytest.raises(ValueError, match=reason):
        compute_btc_features(epoch=9900, candles=candles, ticker=ticker, now=10050)


def test_production_clock_advances_during_fetch(tmp_path, monkeypatch):
    async def scenario():
        clock = [10050.0]
        monkeypatch.setattr(engine.time, "time", lambda: clock[0])
        http = MagicMock()
        http.json = AsyncMock(return_value=market_fixture())

        async def slow_fetch(*args):
            clock[0] += 70
            return book_fixture(), book_fixture("tok_down"), candle_fixture(), ticker_fixture()

        monkeypatch.setattr(engine, "fetch_market_inputs", slow_fetch)
        commander = MagicMock()
        commander.decide = AsyncMock()
        with JevJournal(tmp_path / "events.jsonl") as journal:
            result = await observe_and_trade(http, commander, journal)
            assert result["reason"] == "MISSED_DECISION_WINDOW_AFTER_FETCH"
            commander.decide.assert_not_awaited()
            assert not any(e["type"] == "PAPER_FILL" for e in journal.events)

    asyncio.run(scenario())


def test_fill_rechecks_price_after_inference(tmp_path):
    async def scenario():
        http = MagicMock()
        http.json = AsyncMock(side_effect=[
            market_fixture(), book_fixture(), book_fixture("tok_down"), candle_fixture(),
            ticker_fixture(), book_fixture(ask="0.70"),
        ])
        decision = MagicMock(action="ENTER", side="UP", asset_id="tok_up", limit_price=Decimal("0.53"))
        decision.to_dict.return_value = {"action": "ENTER"}
        commander = MagicMock()
        commander.decide = AsyncMock(return_value=decision)
        with JevJournal(tmp_path / "events.jsonl") as journal:
            result = await observe_and_trade(http, commander, journal, now=10050)
            assert result["reason"] == "PRICE_MOVED_BEYOND_LIMIT"
            assert sum(e["type"] == "JEV_DECISION" for e in journal.events) == 1
            assert not any(e["type"] == "PAPER_FILL" for e in journal.events)

    asyncio.run(scenario())


def test_settle_jev_fills_updates_journal(tmp_path):
    async def scenario():
        journal_path = tmp_path / "events.jsonl"
        mock_http = MagicMock()
        mock_http.json = AsyncMock(return_value={
            "id": "m1",
            "closed": True,
            "umaResolutionStatus": "resolved",
            "clobTokenIds": json.dumps(["tok_up", "tok_down"]),
            "outcomePrices": json.dumps(["1", "0"]),
        })

        with JevJournal(journal_path) as journal:
            journal.append({
                "type": "PAPER_FILL",
                "slug": "btc-5m-1",
                "market_id": "m1",
                "side": "UP",
                "asset_id": "tok_up",
                "quantity": "20.0",
                "total_cost_sim_usd": "10.0",
                "end_epoch": 1000,
            })
            # Time is past end_epoch + 15
            settled = await settle_jev_fills(mock_http, journal)
            assert settled == 1
            settlements = [e for e in journal.events if e["type"] == "SETTLEMENT"]
            assert len(settlements) == 1
            assert settlements[0]["won"] is True
            assert Decimal(settlements[0]["pnl_sim_usd"]) == Decimal("10.0")

    asyncio.run(scenario())


@pytest.mark.parametrize("prices, expected", [(["0", "1"], "1"),
                                            (["0.0", "1.0"], "1"),
                                            (["0.5", "0.5"], "0.5")])
def test_abstention_gets_official_outcome_once_without_creating_cash(tmp_path, prices, expected):
    async def scenario():
        http = MagicMock()
        http.json = AsyncMock(return_value={
            "id": "m1", "closed": True, "umaResolutionStatus": "resolved",
            "outcomes": ["Down", "Up"], "clobTokenIds": ["tok_down", "tok_up"],
            "outcomePrices": prices,
        })
        with JevJournal(tmp_path / "events.jsonl") as journal:
            journal.append({
                "type": "JEV_DECISION", "slug": "btc-updown-5m-9900", "market_id": "m1",
                "end_epoch": 10200, "decision": {"action": "PASS"},
            })
            assert await settle_jev_fills(http, journal) == 0
            assert await settle_jev_fills(http, journal) == 0
            http.json.assert_awaited_once()
            outcomes = [e for e in journal.events if e["type"] == "OUTCOME"]
            assert len(outcomes) == 1
            assert outcomes[0]["outcome_up"] == expected
            assert balances(journal.events) == {"cash": Decimal("100"), "realized_pnl": Decimal("0")}

    asyncio.run(scenario())


def test_calibration_scores_abstentions_and_benchmark_on_same_outcomes():
    events = []
    for slug, probability, outcome, valid in [
        ("entry", .9, "1.0", True), ("pass", .1, "0", True),
        ("api_failed", .5, "1", False), ("pending", .99, None, True),
        ("late", 1.0, "1", True),
        ("void", .6, "0.5", True),
    ]:
        events.append({"type": "JEV_DECISION", "slug": slug, "decision": {
            "action": "ENTER" if slug == "entry" else "PASS", "probability_up": probability,
            "tokens_used": {"input_tokens": 100 if valid else 0},
            "reason": "DECISION_WINDOW_EXPIRED_AFTER_INFERENCE" if slug == "late" else "VALID_FORECAST",
            "state_dossier": {"polymarket_implied_probability_up": .5},
        }})
        if outcome is not None:
            events.append({"type": "OUTCOME", "slug": slug, "outcome_up": outcome})
    report = calibration_report(events)
    assert report["valid_forecasts"] == 4
    assert report["resolved_forecasts"] == report["paired_forecasts"] == 2
    assert report["pending_outcomes"] == 1
    assert report["excluded_nonbinary_outcomes"] == 1
    assert report["jev_paired"]["brier_score"] == pytest.approx(.01)
    assert report["market_paired"]["brier_score"] == pytest.approx(.25)
