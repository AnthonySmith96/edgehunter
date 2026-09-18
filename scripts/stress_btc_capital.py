"""Diagnose the frozen candidate on its already-used validation period only."""
from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from edgehunter.research.capital_stress import StakePolicy, replay_capital
from edgehunter.research.intraday_dataset import load_observation_shards, write_compact_report
from edgehunter.research.learned_intraday import LearnedSpec, LogisticSnapshot
from edgehunter.research.robust_intraday import CostScenario, chronological_split


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--source", type=Path, default=Path("reports/btc_history_270d_learned.json"))
    parser.add_argument("--output", type=Path, default=Path("reports/btc_capital_stress.json"))
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Refusing to overwrite a prior diagnostic; choose a new output")
    source_raw = args.source.read_bytes()
    source = json.loads(source_raw)
    spec = LearnedSpec(**source["selected_spec"])
    model = LogisticSnapshot(**source["selected_model"])
    rows = load_observation_shards([args.input])
    normalized = json.dumps([asdict(row) for row in rows], sort_keys=True, separators=(",", ":")).encode()
    if hashlib.sha256(normalized).hexdigest() != source["audit"]["dataset_sha256"]:
        raise SystemExit("Dataset differs from the frozen evaluation")
    split = chronological_split(
        rows, embargo_seconds=7200,
        fixed_holdout_start_epoch=source["split"]["fixed_holdout_start_epoch"],
    )
    if (len(split.validation) != source["split"]["validation_rows"]
            or split.train_end_epoch != model.training_end_epoch
            or split.validation_end_epoch != source["split"]["validation_end_epoch"]):
        raise SystemExit("Frozen validation boundaries changed")
    validation = tuple(row for row in split.validation if row.horizon_seconds == spec.horizon_seconds)
    policies = (
        StakePolicy("fixed_10", maximum_stake=10),
        StakePolicy("fixed_5", maximum_stake=5),
        StakePolicy("quarter_kelly_capped_10", kelly_fraction=0.25),
    )
    costs = (
        CostScenario("conservative", 0.03, 0.10, 10),
        CostScenario("extra_2c", 0.05, 0.10, 10),
        CostScenario("extra_5c", 0.08, 0.10, 10),
    )
    results = []
    for cost in costs:
        for policy in policies:
            for delay in (300, 3600):
                result = replay_capital(validation, model, spec, cost, policy,
                                        settlement_delay_seconds=delay)
                results.append({"policy": asdict(policy), "costs": asdict(cost), **result})
                print(json.dumps({"cost": cost.name, "policy": policy.name, "delay": delay,
                                  "trades": result["metrics"]["trades"],
                                  "pnl": result["metrics"]["net_pnl"]}), flush=True)
    write_compact_report(args.output, {
        "classification": "EXPLORATORY_STRESS_ON_PREVIOUSLY_USED_VALIDATION",
        "financial_edge_proven": False, "holdout_evaluated": False,
        "refit": False, "automatic_policy_selection": False,
        "source_sha256": hashlib.sha256(source_raw).hexdigest(),
        "dataset_sha256": source["audit"]["dataset_sha256"],
        "validation_rows": len(validation),
        "validation_start_epoch": validation[0].epoch,
        "validation_end_epoch": validation[-1].epoch,
        "model_spec": asdict(spec), "scenarios": results,
        "limitations": [
            "Historical prices are sampled proxies, not confirmed executable asks or depth.",
            "Minimum size of five shares and release delays are assumptions.",
            "Validation previously selected the model; this is not fresh performance evidence.",
            "Kelly depends on imperfect estimated probabilities and cannot create an edge.",
            "Daily bootstrap intervals describe selected past PnLs, not future returns.",
        ],
    })


if __name__ == "__main__":
    main()
