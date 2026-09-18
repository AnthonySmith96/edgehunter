import importlib.util
import json
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "jev_probe", Path(__file__).resolve().parents[2] / "scripts/benchmark_jev.py",
)
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


def snapshot():
    return {"type": "OBSERVATION", "slug": "btc-updown-5m-1800000000",
            "observed_at": "2027-01-15T08:02:03+00:00", "up_ask": "0.6", "down_ask": "0.41",
            "coinbase": {"open": 100, "spot": 101, "vol_per_second": 0.001,
                         "ticker_time": "2027-01-15T08:02:02Z"}}


def test_incomplete_latest_snapshot_is_rejected_without_reusing_older_signal(tmp_path):
    old = snapshot()
    latest = {**snapshot(), "up_ask": None}
    path = tmp_path / "events.jsonl"
    path.write_text(json.dumps(old) + "\n" + json.dumps(latest) + "\n")
    with pytest.raises(ValueError, match="LATEST_SNAPSHOT_INCOMPLETE"):
        probe.latest_row(path)
    path.write_text(json.dumps(old) + "\n")
    row, deadline = probe.latest_row(path)
    assert row.up_price == 0.6 and deadline > 0
