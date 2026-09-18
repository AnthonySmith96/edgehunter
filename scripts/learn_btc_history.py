from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict
from pathlib import Path

from edgehunter.research.intraday_dataset import load_observation_shards, write_compact_report
from edgehunter.research.learned_intraday import LearnedSpec, learn_validate_holdout
from edgehunter.research.robust_intraday import EvaluationConfig


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train and validate a regularized BTC intraday challenger")
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fixed-holdout-start-epoch", type=int, required=True)
    parser.add_argument("--initial-capital", type=float, default=100.0)
    parser.add_argument("--bootstrap-draws", type=int, default=2_000)
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
    rows = load_observation_shards(args.inputs)
    specs = [
        LearnedSpec(horizon, ridge, edge, max_ask)
        for horizon in sorted({row.horizon_seconds for row in rows})
        for ridge in (0.1, 1.0, 10.0, 100.0)
        for edge in (0.02, 0.04, 0.06, 0.08)
        for max_ask in (0.70, 0.85)
    ]
    config = EvaluationConfig(
        min_train_trades=50, min_validation_trades=20,
        min_train_days=20, min_validation_days=7,
        min_test_trades=20, min_test_days=4,
        initial_capital=args.initial_capital, bootstrap_draws=args.bootstrap_draws,
        monte_carlo_runs=100, monte_carlo_horizon_trades=1,
        fixed_holdout_start_epoch=args.fixed_holdout_start_epoch,
        seed=args.seed,
    )
    result = learn_validate_holdout(
        rows, specs, config=config, progress=progress, checkpoint_dir=args.checkpoint_dir,
    )
    progress("writing audited report")
    dataset = json.dumps([asdict(row) for row in rows], sort_keys=True, separators=(",", ":")).encode()
    candidates = json.dumps([asdict(spec) for spec in specs], sort_keys=True, separators=(",", ":")).encode()
    configuration = json.dumps(asdict(config), sort_keys=True, separators=(",", ":")).encode()
    result["audit"] = {
        "dataset_sha256": hashlib.sha256(dataset).hexdigest(),
        "candidate_specs_sha256": hashlib.sha256(candidates).hexdigest(),
        "evaluation_config_sha256": hashlib.sha256(configuration).hexdigest(),
        "candidate_count": len(specs),
        "holdout_opened": result["holdout_opened"],
        "fixed_holdout_start_epoch": args.fixed_holdout_start_epoch,
    }
    write_compact_report(args.output, result)
    print(json.dumps({
        "status": result["status"], "observations": len(rows), "candidates": len(specs),
        "holdout_opened": result["holdout_opened"], "report": str(args.output),
    }), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
