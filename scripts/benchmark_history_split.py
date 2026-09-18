"""Measure the old quadratic purge recount without evaluating outcomes or strategies."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from time import perf_counter

from edgehunter.research.btc_intraday import IntradayObservation
from edgehunter.research.robust_intraday import chronological_split


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sample-rows", type=int, default=18_000)
    parser.add_argument("--fixed-holdout-start-epoch", type=int, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("benchmark output already exists; choose a new path")
    rows: list[IntradayObservation] = []
    digest = hashlib.sha256()
    with args.input.open("rb") as handle:
        for raw in handle:
            digest.update(raw)
            item = json.loads(raw)
            # Deliberately discard prices and labels: only temporal metadata is benchmarked.
            rows.append(IntradayObservation(
                slug=item["slug"], epoch=item["epoch"], horizon_seconds=item["horizon_seconds"],
                open_price=1, spot_price=1, vol_per_second=0.01,
                up_price=0.5, down_price=0.5, outcome_up=0,
            ))
    sample = rows[:args.sample_rows]
    started = perf_counter()
    small = chronological_split(sample, embargo_seconds=7200)
    sample_seconds = perf_counter() - started
    train_epochs = {row.epoch for row in small.train}
    validation_epochs = {row.epoch for row in small.validation}
    test_epochs = {row.epoch for row in small.test}
    started = perf_counter()
    legacy_purged = sum(
        row.epoch not in train_epochs | validation_epochs | test_epochs for row in sample
    )
    legacy_purge_seconds = perf_counter() - started
    assert legacy_purged == small.purged_rows
    started = perf_counter()
    full = chronological_split(
        rows, embargo_seconds=7200, fixed_holdout_start_epoch=args.fixed_holdout_start_epoch,
    )
    full_seconds = perf_counter() - started
    result = {
        "schema": "edgehunter.split_benchmark.v1", "input_sha256": digest.hexdigest(),
        "strategy_evaluated": False, "real_outcome_labels_used": False,
        "sample_rows": len(sample), "sample_optimized_split_seconds": sample_seconds,
        "sample_legacy_purge_recount_seconds": legacy_purge_seconds,
        "sample_legacy_purge_matches_optimized": legacy_purged == small.purged_rows,
        "sample_speedup_lower_bound": legacy_purge_seconds / sample_seconds,
        "speedup_note": "Old purge recount alone divided by entire optimized split; same sample",
        "full_rows": len(rows), "full_optimized_split_seconds": full_seconds,
        "split": {
            "train_rows": len(full.train), "validation_rows": len(full.validation),
            "test_rows": len(full.test), "purged_rows_for_embargo": full.purged_rows,
            "train_end_epoch": full.train_end_epoch, "validation_end_epoch": full.validation_end_epoch,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
