"""Score recorded prospective Jev probabilities against official paper settlements."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from edgehunter.intelligence.jev_forecast import score_paired_forecasts  # noqa: E402
from edgehunter.research.btc_challenger import write_report  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--forecasts", type=Path, default=ROOT / "data/jev_shadow/forecasts.jsonl")
    parser.add_argument("--settlements", type=Path, default=ROOT / "data/btc_paper/events.jsonl")
    args = parser.parse_args()
    forecasts = [json.loads(line) for line in args.forecasts.read_text().splitlines() if line.strip()] if args.forecasts.exists() else []
    outcomes = {}
    for line in args.settlements.read_text(encoding="utf-8").splitlines():
        value = json.loads(line)
        if value.get("type") != "SETTLEMENT" or value.get("official_resolution_status") != "resolved":
            continue
        prices = value.get("official_outcome_prices")
        prices = json.loads(prices) if isinstance(prices, str) else prices
        if not isinstance(prices, list) or len(prices) != 2:
            continue
        numeric = [float(price) for price in prices]
        if numeric in ([1, 0], [0, 1]):
            outcomes[value["slug"]] = int(numeric[0])
    report = score_paired_forecasts(forecasts, outcomes)
    report["status"] = "AWAITING_JEV_FORECASTS" if not forecasts else "DESCRIPTIVE_COMPARISON_ONLY"
    write_report(ROOT / "reports/jev_shadow_score.json", report)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
