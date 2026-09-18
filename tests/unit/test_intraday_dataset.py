import json

import pytest

from edgehunter.research.intraday_dataset import (
    compact_evaluation_report,
    load_observation_shards,
    observation_from_mapping,
    write_observation_jsonl,
)
from edgehunter.research.robust_intraday import ChronologicalSplit


def row(slug="btc-1", horizon=60, outcome=1):
    return {
        "slug": slug,
        "epoch": 100,
        "horizon_seconds": horizon,
        "open_price": 100.0,
        "spot_price": 100.3,
        "vol_per_second": 0.0002,
        "up_price": 0.6,
        "down_price": 0.4,
        "outcome_up": outcome,
    }


def test_jsonl_shards_merge_identical_overlap(tmp_path):
    first = tmp_path / "a.jsonl"
    second = tmp_path / "b.jsonl"
    first.write_text(json.dumps(row()) + "\n", encoding="utf-8")
    second.write_text(json.dumps(row()) + "\n" + json.dumps(row("btc-2")) + "\n", encoding="utf-8")
    observations = load_observation_shards([first, second])
    assert [item.slug for item in observations] == ["btc-1", "btc-2"]
    output = tmp_path / "roundtrip.jsonl"
    write_observation_jsonl(output, observations)
    assert load_observation_shards([output]) == observations


def test_jsonl_shards_reject_conflicting_duplicate_and_bad_schema(tmp_path):
    first = tmp_path / "a.jsonl"
    second = tmp_path / "b.jsonl"
    first.write_text(json.dumps(row()) + "\n", encoding="utf-8")
    second.write_text(json.dumps(row(outcome=0)) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="conflicting duplicate"):
        load_observation_shards([first, second])
    second.write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing observation fields"):
        load_observation_shards([second])


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [("outcome_up", 0.9), ("outcome_up", True), ("epoch", True), ("slug", None)],
)
def test_jsonl_schema_rejects_silent_type_coercions(tmp_path, field, bad_value):
    path = tmp_path / "bad.jsonl"
    value = row()
    value[field] = bad_value
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_observation_shards([path])


def test_compact_report_removes_trade_logs_recursively():
    full = {
        "status": "ok",
        "development": [{"train": {"trade_log": [{"pnl": 1}], "metrics": {"net_pnl": 1}}}],
        "test": {"base": {"trade_log": [{"pnl": 2}], "metrics": {"net_pnl": 2}}},
    }
    compact = compact_evaluation_report(full)
    assert compact["report_schema"] == "edgehunter.btc_intraday_evaluation.v1"
    assert "trade_log" not in json.dumps(compact)
    assert compact["test"]["base"]["metrics"]["net_pnl"] == 2


@pytest.mark.parametrize("field", ["up_price_timestamp", "down_price_timestamp"])
@pytest.mark.parametrize("timestamp", [-1, 99, 341, 400])
def test_import_rejects_quotes_outside_available_market_interval(field, timestamp):
    # At epoch 100 with 60 seconds remaining, the decision is at 340,
    # so even a quote before expiry (400) can introduce future information.
    value = row()
    value[field] = timestamp
    with pytest.raises(ValueError, match="within market start and decision time"):
        observation_from_mapping(value)


@pytest.mark.parametrize("horizon", [0, -1, 301])
def test_import_rejects_horizons_outside_five_minute_contract(horizon):
    with pytest.raises(ValueError, match="horizon must be between one and 300"):
        observation_from_mapping(row(horizon=horizon))


@pytest.mark.parametrize("effective_horizon", [0, 301, 59])
def test_import_rejects_features_outside_causal_window(effective_horizon):
    value = row()
    value["effective_horizon_seconds"] = effective_horizon
    with pytest.raises(ValueError, match="effective"):
        observation_from_mapping(value)


@pytest.mark.parametrize(
    "field", ["up_price_timestamp", "down_price_timestamp", "effective_horizon_seconds"],
)
@pytest.mark.parametrize("bad_value", [True, 120.0, "120", float("inf")])
def test_import_rejects_optional_timestamp_type_coercion(field, bad_value):
    value = row()
    value[field] = bad_value
    with pytest.raises(ValueError, match="integer or null"):
        observation_from_mapping(value)


@pytest.mark.parametrize("horizon", [1, 60, 300])
def test_import_accepts_causal_boundaries_and_optional_legacy_metadata(horizon):
    value = row(horizon=horizon)
    legacy = observation_from_mapping(value)
    assert legacy.up_price_timestamp is None
    assert legacy.down_price_timestamp is None
    assert legacy.effective_horizon_seconds is None
    value.update(
        up_price_timestamp=value["epoch"],
        down_price_timestamp=value["epoch"] + 300 - horizon,
        effective_horizon_seconds=horizon,
    )
    observation = observation_from_mapping(value)
    assert observation.up_price_timestamp == 100
    assert observation.down_price_timestamp == 400 - horizon
    assert observation.effective_horizon_seconds == horizon


def test_jsonl_import_identifies_line_with_future_quote(tmp_path):
    path = tmp_path / "future.jsonl"
    future = row("future")
    future["up_price_timestamp"] = 341
    path.write_text(json.dumps(row()) + "\n" + json.dumps(future) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"future\.jsonl:2:.*decision time"):
        load_observation_shards([path])


def test_split_type_is_importable_for_downstream_contract():
    assert ChronologicalSplit.__name__ == "ChronologicalSplit"
