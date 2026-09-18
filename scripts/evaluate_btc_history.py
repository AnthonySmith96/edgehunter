from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

from edgehunter.research.btc_intraday import IntradaySpec
from edgehunter.research.intraday_dataset import load_observation_shards, write_compact_report
from edgehunter.research.robust_intraday import EvaluationConfig, evaluate_untouched_holdout


def default_specs(horizons: Sequence[int]) -> list[IntradaySpec]:
    return [
        IntradaySpec(horizon, edge, max_ask, vol_scale)
        for horizon in sorted(set(horizons))
        for edge in (0.02, 0.04, 0.06)
        for max_ask in (0.65, 0.75, 0.85)
        for vol_scale in (0.8, 1.0, 1.2)
    ]


def load_specs(path: Path) -> list[IntradaySpec]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list):
        raise ValueError("spec file must contain a JSON array")
    return [
        IntradaySpec(
            horizon_seconds=int(item["horizon_seconds"]),
            min_edge=float(item["min_edge"]),
            max_ask=float(item["max_ask"]),
            vol_scale=float(item["vol_scale"]),
        )
        for item in value
    ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate merged BTC 5m historical JSONL shards")
    parser.add_argument("inputs", nargs="+", type=Path, help="JSONL shard paths")
    parser.add_argument("--output", type=Path, required=True, help="compact JSON report path")
    parser.add_argument("--specs", type=Path, help="optional JSON candidate-spec array")
    parser.add_argument("--initial-capital", type=float, default=100.0)
    parser.add_argument("--min-train-trades", type=int, default=10)
    parser.add_argument("--min-validation-trades", type=int, default=10)
    parser.add_argument("--min-train-days", type=int, default=5)
    parser.add_argument("--min-validation-days", type=int, default=3)
    parser.add_argument("--min-test-trades", type=int, default=20)
    parser.add_argument("--min-test-days", type=int, default=3)
    parser.add_argument("--embargo-seconds", type=int, default=7200)
    parser.add_argument("--fixed-holdout-start-epoch", type=int)
    parser.add_argument("--bootstrap-draws", type=int, default=2_000)
    parser.add_argument("--monte-carlo-runs", type=int, default=5_000)
    parser.add_argument("--monte-carlo-horizon", type=int, default=300)
    parser.add_argument("--seed", type=int, default=20260917)
    parser.add_argument("--checkpoint-dir", type=Path, help="resume this exact experiment after interruption")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.output.exists():
        raise SystemExit("Refusing to overwrite a frozen report; choose a new output path")
    def progress(message: str) -> None:
        print(json.dumps({"progress": message}), file=sys.stderr, flush=True)

    progress("loading historical observations")
    observations = load_observation_shards(args.inputs)
    specs = load_specs(args.specs) if args.specs else default_specs(
        [observation.horizon_seconds for observation in observations]
    )
    config = EvaluationConfig(
        min_train_trades=args.min_train_trades,
        min_validation_trades=args.min_validation_trades,
        min_train_days=args.min_train_days,
        min_validation_days=args.min_validation_days,
        min_test_trades=args.min_test_trades,
        min_test_days=args.min_test_days,
        embargo_seconds=args.embargo_seconds,
        fixed_holdout_start_epoch=args.fixed_holdout_start_epoch,
        initial_capital=args.initial_capital,
        bootstrap_draws=args.bootstrap_draws,
        monte_carlo_runs=args.monte_carlo_runs,
        monte_carlo_horizon_trades=args.monte_carlo_horizon,
        seed=args.seed,
    )
    result = evaluate_untouched_holdout(
        observations, specs, config=config, progress=progress, checkpoint_dir=args.checkpoint_dir,
    )
    progress("writing audited report")
    observation_bytes = json.dumps(
        [asdict(row) for row in observations], sort_keys=True, separators=(",", ":"),
    ).encode()
    spec_bytes = json.dumps(
        [spec.to_dict() for spec in specs], sort_keys=True, separators=(",", ":"),
    ).encode()
    config_bytes = json.dumps(asdict(config), sort_keys=True, separators=(",", ":")).encode()
    result["audit"] = {
        "dataset_sha256": hashlib.sha256(observation_bytes).hexdigest(),
        "candidate_specs_sha256": hashlib.sha256(spec_bytes).hexdigest(),
        "evaluation_config_sha256": hashlib.sha256(config_bytes).hexdigest(),
        "candidate_count": len(specs),
        "holdout_opened": result.get("holdout_opened", False),
    }
    write_compact_report(args.output, result)
    print(json.dumps({"status": result["status"], "observations": len(observations),
                      "candidates": len(specs), "report": str(args.output)}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
