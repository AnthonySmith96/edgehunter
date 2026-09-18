from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest

from edgehunter.ops.paper_candidate import PaperCandidate, resolution_payout
from edgehunter.storage.journal import Journal


class FixedModel:
    def estimate_yes(self, market_probability, category, issued_at):
        return D("0.9"), D("0.8"), D("0.95")


def fixture(now):
    books = []
    for token, bid, ask in (("101", "0.59", "0.60"), ("102", "0.39", "0.40")):
        books.append({"event_time":now.isoformat(), "available_at":now.isoformat(),
                      "payload":{"asset_id":token, "bids":[{"price":bid,"size":"100"}],
                                 "asks":[{"price":ask,"size":"100"}],"tick_size":"0.01","min_order_size":"5"}})
    return {"market_id":"1", "outcomes":["Yes","No"], "contract_hash":"synthetic-contract",
            "contract":{"endDate":(now+timedelta(hours=24)).isoformat(),"conditionId":"event-1","resolutionSource":"https://example.org","negRisk":False},
            "books": books}


@pytest.mark.parametrize("winning", [True, False])
def test_prospective_paper_uses_book_costs_and_can_win_or_lose(tmp_path, winning):
    now = datetime.now(UTC)
    journal = Journal(tmp_path / "journal.db")
    journal.deposit(D("1000"), idempotency_key="capital")
    journal.renew_control_lease(now=now)
    candidate = PaperCandidate(journal, FixedModel(), "synthetic-model")
    shadow = candidate.evaluate(fixture(now), now=now, clock_healthy=True, execute=False)
    assert shadow["action"] == "PROPOSE" and not journal.intents()
    epoch = journal.acquire_executor("test", now=now)
    filled = candidate.evaluate(fixture(now), now=now, clock_healthy=True, execute=True, executor=("test",epoch))
    assert filled["action"] == "PAPER_FILLED"
    assert D(journal.report()["fees"]) > 0
    assert candidate.evaluate(fixture(now), now=now, clock_healthy=True, execute=True)["reason"] == "MARKET_ALREADY_ATTEMPTED"
    journal.settle("101", payout_per_share=D(int(winning)), settlement_id="terminal-1", now=now+timedelta(hours=1))
    report = journal.report()
    assert (D(report["net_trading_pnl"]) > 0) == winning
    assert report["ledger_valid"] and D(report["reserved"]) == 0


def test_stale_clock_or_stale_book_never_creates_order(tmp_path):
    now = datetime.now(UTC)
    journal = Journal(tmp_path / "journal.db")
    candidate = PaperCandidate(journal, FixedModel(), "model")
    assert candidate.evaluate(fixture(now), now=now, clock_healthy=False, execute=True)["reason"] == "CLOCK_SKEW"
    assert candidate.evaluate(fixture(now), now=now+timedelta(seconds=10), clock_healthy=True, execute=True)["reason"] == "STALE_OR_FUTURE_BOOK"
    assert not journal.intents()


def test_last_trade_is_not_resolution():
    market = {"closed":True,"clobTokenIds":["101","102"],"outcomePrices":["1","0"]}
    assert resolution_payout(market,"101") is None
    market["umaResolutionStatus"] = "resolved"
    assert resolution_payout(market,"101") == 1
    market["outcomePrices"] = ["0.999","0.001"]
    assert resolution_payout(market,"101") is None


def test_candidate_does_not_extrapolate_to_untrained_horizon(tmp_path):
    now = datetime.now(UTC)
    journal = Journal(tmp_path / "journal.db")
    candidate = PaperCandidate(journal, FixedModel(), "model")
    snapshot = fixture(now)
    snapshot["contract"]["endDate"] = (now+timedelta(days=30)).isoformat()
    assert candidate.evaluate(snapshot, now=now, clock_healthy=True, execute=True)["reason"] == "OUTSIDE_24H_RESEARCH_HORIZON"
    snapshot["contract"].pop("endDate")
    assert candidate.evaluate(snapshot, now=now, clock_healthy=True, execute=True)["reason"] == "UNKNOWN_EVENT_HORIZON"
    assert not journal.intents()
