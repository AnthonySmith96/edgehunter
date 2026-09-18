import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal as D

import pytest

from edgehunter.research import btc_challenger as engine

EPOCH = 1_800_000_000
NOW = EPOCH + 143
SLUG = f"btc-updown-5m-{EPOCH}"


def source():
    return {
        "status": "HOLDOUT_INSUFFICIENT",
        "selected_spec": {"horizon_seconds": 60, "ridge": 100, "min_edge": 0.06, "max_ask": 0.85},
        "selected_model": {"horizon_seconds": 60, "ridge": 100,
                           "means": [0]*4, "scales": [1]*4, "coefficients": [2.2, 0, 0, 0, 0],
                           "training_rows": 100, "training_end_epoch": EPOCH-86400},
    }


def book(token="up"):
    return {"asset_id": token, "timestamp": NOW * 1000,
            "asks": [{"price": "0.51", "size": "100"}],
            "bids": [{"price": "0.49", "size": "100"}], "min_order_size": "5"}


def candles():
    return [[moment, 99, 101, 100, 100.01, 1]
            for moment in range(EPOCH+120-7200, EPOCH+180, 60)]


def ticker():
    return {"price": "100", "time": datetime.fromtimestamp(NOW, UTC).isoformat()}


def prediction(journal, slug=SLUG):
    journal.append({"type": "OPPORTUNITY", "slug": slug,
                    "observed_at": datetime.fromtimestamp(NOW, UTC).isoformat()})
    journal.append({"type": "PREDICTION", "slug": slug, "probability_up": 0.8})


def test_manifest_refuses_model_policy_or_source_mutation(tmp_path):
    raw = tmp_path / "source.json"
    raw.write_text(json.dumps(source()))
    path = tmp_path / "manifest.json"
    engine.register(raw, path)
    original = path.read_text()
    engine.load_manifest(path)
    with pytest.raises(FileExistsError):
        engine.register(raw, path)
    changed = json.loads(original)
    changed["policy"]["maximum_stake_sim_usd"] = "50"
    path.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="POLICY"):
        engine.load_manifest(path)
    path.write_text(original)
    raw.write_text(json.dumps({**source(), "status": "CHANGED"}))
    with pytest.raises(ValueError, match="SOURCE_REPORT_CHANGED"):
        engine.load_manifest(path)


def test_manifest_uses_portable_source_path_inside_project(tmp_path):
    reports = tmp_path / "reports"
    config = tmp_path / "config"
    reports.mkdir()
    raw = reports / "source.json"
    raw.write_text(json.dumps(source()))
    path = config / "manifest.json"

    document = engine.register(raw, path)

    assert document["source_report"] == "reports/source.json"
    engine.load_manifest(path)


def test_journal_reserves_cash_and_settlement_cannot_be_counted_twice(tmp_path):
    path = tmp_path / "events.jsonl"
    with engine.ExperimentJournal(path, "HASH") as journal:
        for i in range(10):
            slug = f"btc-updown-5m-{EPOCH+i*300}"
            prediction(journal, slug)
            journal.append({"type": "PAPER_FILL", "slug": slug,
                            "quantity": "20", "total_cost_sim_usd": "10"})
        assert engine.balances(journal.events)["cash"] == 0
        new = f"btc-updown-5m-{EPOCH+3000}"
        prediction(journal, new)
        with pytest.raises(ValueError, match="INSUFFICIENT"):
            journal.append({"type": "PAPER_FILL", "slug": new,
                            "quantity": "20", "total_cost_sim_usd": "10"})
        settlement = {"type": "SETTLEMENT", "slug": SLUG, "payout_per_share": "1", "pnl_sim_usd": "10"}
        journal.append(settlement)
        assert engine.balances(journal.events)["cash"] == 20
        with pytest.raises(ValueError, match="DUPLICATE"):
            journal.append(settlement)
    with engine.ExperimentJournal(path, "HASH") as restarted:
        assert engine.balances(restarted.events)["cash"] == 20
        assert restarted.attempted(SLUG)


def test_journal_recovers_torn_tail_but_rejects_config_change(tmp_path):
    path = tmp_path / "events.jsonl"
    with engine.ExperimentJournal(path, "HASH") as journal:
        prediction(journal)
    good = path.read_bytes()
    with path.open("ab") as handle:
        handle.write(b'{"type":"PAPER_FILL"')
    with engine.ExperimentJournal(path, "HASH") as journal:
        assert len(journal.events) == 2
    assert path.read_bytes() == good
    assert len(list(tmp_path.glob("*.torn-*"))) == 1
    with pytest.raises(ValueError, match="INTEGRITY"):
        with engine.ExperimentJournal(path, "OTHER"):
            pass


def test_freshness_pair_and_candle_gaps_are_rejected():
    up = engine.checked_book(book(), "up", NOW)
    down = engine.checked_book(book("down"), "down", NOW)
    assert up.midpoint == D("0.50")
    row = engine.observation_from_public(epoch=EPOCH, books=(up, down), candles=candles(), ticker=ticker(), now=NOW)
    assert row.effective_horizon_seconds == 180 and row.horizon_seconds == 60
    with pytest.raises(ValueError, match="STALE"):
        engine.checked_book(book(), "up", NOW+20)
    with pytest.raises(engine.MissingCandlesError, match="CONTIGUOUS") as error:
        engine.observation_from_public(epoch=EPOCH, books=(up, down), candles=candles()[1:], ticker=ticker(), now=NOW)
    assert error.value.details["missing_candle_epochs"] == [EPOCH + 120 - 7200]
    with pytest.raises(ValueError, match="UNSYNCHRONIZED"):
        engine.observation_from_public(epoch=EPOCH, books=(up, replace(down, timestamp=NOW-3)),
                                       candles=candles(), ticker=ticker(), now=NOW)
    with pytest.raises(ValueError, match="STALE"):
        engine.observation_from_public(epoch=EPOCH, books=(up, down), candles=candles(), ticker=ticker(), now=NOW+20)


def test_recheck_refuses_lost_edge_insufficient_cash_and_close():
    spec, _ = engine.frozen_model(source())
    checked = engine.checked_book(book(), "up", NOW)
    kwargs = dict(book=checked, probability=0.59, limit=D("0.7"), spec=spec,
                  available_cash=D(100), close_epoch=EPOCH+300, now=NOW)
    assert engine.recheck_fill(**kwargs)[1] == "EDGE_LOST_AFTER_LATENCY"
    assert engine.recheck_fill(**{**kwargs, "probability": 0.9, "available_cash": D(9)})[1] == "INSUFFICIENT_AVAILABLE_CASH"
    assert engine.recheck_fill(**{**kwargs, "now": EPOCH+299})[1] == "TOO_CLOSE_AFTER_LATENCY"


class PublicFixture:
    async def json(self, url, params=None):
        if "/events/slug/" in url:
            return {"markets": [{"id": "1", "slug": SLUG,
                                 "endDate": datetime.fromtimestamp(EPOCH+300, UTC).isoformat(),
                                 "clobTokenIds": ["up", "down"], "outcomes": ["Up", "Down"],
                                 "acceptingOrders": True, "closed": False, "negRisk": False,
                                 "resolutionSource": "Chainlink"}]}
        if "/book" in url:
            return book(params["token_id"])
        if "/candles" in url:
            # Default endpoint can serve a stale cached response missing the current minute.
            if not params or "start" not in params or "end" not in params:
                return candles()[:-1]
            # Coinbase also excludes a candle exactly at the query's end boundary.
            if datetime.fromisoformat(params["end"]).timestamp() <= EPOCH + 120:
                return candles()[:-1]
            return candles()
        if "/ticker" in url:
            return ticker()
        raise AssertionError(url)


def test_prospective_cycle_one_fill_and_no_duplicate_after_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(engine.time, "time", lambda: NOW)
    path = tmp_path / "events.jsonl"
    with engine.ExperimentJournal(path, "HASH") as journal:
        asyncio.run(engine.observe_once(PublicFixture(), source(), journal))
        fills = [r for r in journal.events if r["type"] == "PAPER_FILL"]
        assert len(fills) == 1
        assert "outcome_up" not in fills[0]["post_latency_features"]
        assert engine.balances(journal.events)["cash"] >= 90
    with engine.ExperimentJournal(path, "HASH") as journal:
        asyncio.run(engine.observe_once(PublicFixture(), source(), journal))
        assert engine.report(journal)["paper_fills"] == 1
        assert not engine.report(journal)["financial_edge_proven"]


def test_post_latency_probability_change_rejects_fill(tmp_path, monkeypatch):
    monkeypatch.setattr(engine.time, "time", lambda: NOW)
    predictions = iter([0.9, 0.5])
    monkeypatch.setattr(engine, "predict_up", lambda *args: next(predictions))
    with engine.ExperimentJournal(tmp_path / "events.jsonl", "HASH") as journal:
        asyncio.run(engine.observe_once(PublicFixture(), source(), journal))
        assert engine.report(journal)["paper_fills"] == 0
        assert journal.events[-1]["reason"] == "EDGE_LOST_AFTER_LATENCY"


def test_candle_query_after_publication_uses_current_open_without_its_unfinished_close(monkeypatch):
    monkeypatch.setattr(engine.time, "time", lambda: EPOCH + 140.25)
    _, _, raw, _ = asyncio.run(engine.fetch_inputs(PublicFixture(), ["up", "down"]))
    raw[-1][4] = 101  # unfinished close must not become the spot feature
    up = engine.checked_book(book(), "up", NOW)
    down = engine.checked_book(book("down"), "down", NOW)
    row = engine.observation_from_public(epoch=EPOCH, books=(up, down), candles=raw, ticker=ticker(), now=NOW)
    assert row.spot_price == 100 and row.effective_horizon_seconds == 180
