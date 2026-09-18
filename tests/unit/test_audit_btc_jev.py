import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "audit_btc_jev", Path(__file__).resolve().parents[2] / "scripts/audit_btc_jev.py",
)
audit_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit_module)


def decision(epoch, probability, *, tokens=10, late=False):
    return {
        "type": "JEV_DECISION", "slug": f"btc-updown-5m-{epoch}", "market_id": str(epoch),
        "recorded_at": datetime.fromtimestamp(epoch + (301 if late else 150), UTC).isoformat(),
        "decision": {"probability_up": probability, "tokens_used": {"input_tokens": tokens},
                     "state_dossier": {"polymarket_implied_probability_up": .5}},
    }


def outcome(epoch, value):
    return {"type": "OUTCOME", "slug": f"btc-updown-5m-{epoch}", "outcome_up": value,
            "market_id": str(epoch), "official_resolution_status": "resolved"}


def test_audit_scores_valid_abstentions_and_excludes_failed_late_void(tmp_path):
    events = [decision(9900, .9), decision(10200, .1), decision(10500, .5, tokens=0),
              decision(10800, 1, late=True), decision(11100, .6)]
    for epoch, value in [(9900, "1.0"), (10200, "0"), (10500, "1"),
                         (10800, "1"), (11100, "0.5")]:
        events.append(outcome(epoch, value))
    journal = tmp_path / "events.jsonl"
    journal.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    result = audit_module.audit(journal)
    cohort = result["all_decisions"]
    assert cohort["resolved"] == 2
    assert cohort["invalid_timing_excluded"] == 1
    assert cohort["invalid_usage_excluded"] == 1
    assert cohort["nonbinary_outcomes_excluded"] == 1
    assert cohort["unresolved"] == 0
    assert cohort["jev"]["brier"] == pytest.approx(.01)
    assert cohort["market"]["brier"] == pytest.approx(.25)
    assert len(result["paired_predictions"]) == 2
    assert result["financial"]["realized_pnl_sim_usd"] == "0"


@pytest.mark.parametrize("change", ["market", "conflict", "early"])
def test_external_labels_cannot_change_market_or_outcome(tmp_path, change):
    journal = tmp_path / "events.jsonl"
    journal.write_text(json.dumps(decision(9900, .9)) + "\n" + json.dumps(outcome(9900, "1")) + "\n")
    labels = {"as_of": datetime.fromtimestamp(9900 if change == "early" else 11000, UTC).isoformat(),
              "outcomes": [{"slug": "btc-updown-5m-9900", "market_id": "bad" if change == "market" else "9900",
                            "resolution_status": "resolved", "outcome_up": "0" if change == "conflict" else "1.0"}]}
    path = tmp_path / "labels.json"
    path.write_text(json.dumps(labels))
    with pytest.raises(ValueError):
        audit_module.audit(journal, path)
