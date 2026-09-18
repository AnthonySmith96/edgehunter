from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np

from edgehunter.research.btc_intraday import IntradayObservation
from edgehunter.research.evaluation_checkpoint import DevelopmentCheckpoint, evaluation_identity
from edgehunter.research.robust_intraday import (
    CostScenario,
    EvaluationConfig,
    chronological_split,
    summarize_trades,
)


@dataclass(frozen=True)
class LearnedSpec:
    horizon_seconds: int
    ridge: float
    min_edge: float
    max_ask: float

    def __post_init__(self) -> None:
        if self.horizon_seconds <= 0 or self.ridge <= 0:
            raise ValueError("invalid learned specification")
        if not 0 <= self.min_edge < 1 or not 0.5 <= self.max_ask < 1:
            raise ValueError("invalid learned trading bounds")


@dataclass(frozen=True)
class LogisticSnapshot:
    horizon_seconds: int
    ridge: float
    means: tuple[float, ...]
    scales: tuple[float, ...]
    coefficients: tuple[float, ...]
    training_rows: int
    training_end_epoch: int


def _raw_features(row: IntradayObservation) -> tuple[float, ...]:
    market_up = row.up_price / (row.up_price + row.down_price)
    market_up = min(0.999, max(0.001, market_up))
    horizon = row.effective_horizon_seconds or row.horizon_seconds
    z = math.log(row.spot_price / row.open_price) / (row.vol_per_second * math.sqrt(horizon))
    logit_market = math.log(market_up / (1 - market_up))
    return logit_market, z, z * abs(z), logit_market * z


def fit_logistic(
    rows: Sequence[IntradayObservation], *, horizon_seconds: int, ridge: float,
    training_end_epoch: int,
) -> LogisticSnapshot:
    selected = [row for row in rows if row.horizon_seconds == horizon_seconds]
    if len(selected) < 20 or ridge <= 0:
        raise ValueError("insufficient training rows or invalid ridge")
    if any(row.epoch > training_end_epoch for row in selected):
        raise ValueError("training row exceeds declared cutoff")
    raw = np.asarray([_raw_features(row) for row in selected], dtype=np.float64)
    means = raw.mean(axis=0)
    scales = raw.std(axis=0)
    scales[scales < 1e-9] = 1.0
    standardized = (raw - means) / scales
    x = np.column_stack((np.ones(len(selected)), standardized))
    y = np.asarray([row.outcome_up for row in selected], dtype=np.float64)
    weights = np.zeros(x.shape[1], dtype=np.float64)
    penalty = np.eye(x.shape[1], dtype=np.float64) * ridge
    penalty[0, 0] = 0.0
    for _ in range(50):
        logits = np.clip(x @ weights, -30, 30)
        probabilities = 1 / (1 + np.exp(-logits))
        curvature = np.maximum(probabilities * (1 - probabilities), 1e-6)
        gradient = x.T @ (probabilities - y) + penalty @ weights
        hessian = (x.T * curvature) @ x + penalty
        step = np.linalg.solve(hessian, gradient)
        weights -= step
        if float(np.max(np.abs(step))) < 1e-8:
            break
    return LogisticSnapshot(
        horizon_seconds=horizon_seconds,
        ridge=ridge,
        means=tuple(float(value) for value in means),
        scales=tuple(float(value) for value in scales),
        coefficients=tuple(float(value) for value in weights),
        training_rows=len(selected),
        training_end_epoch=training_end_epoch,
    )


def predict_up(model: LogisticSnapshot, row: IntradayObservation) -> float:
    if row.horizon_seconds != model.horizon_seconds:
        raise ValueError("row horizon does not match model")
    raw = _raw_features(row)
    features = [1.0] + [
        (value - mean) / scale
        for value, mean, scale in zip(raw, model.means, model.scales)
    ]
    logit = sum(weight * value for weight, value in zip(model.coefficients, features))
    return 1 / (1 + math.exp(-max(-30.0, min(30.0, logit))))


def evaluate_learned(
    rows: Sequence[IntradayObservation], model: LogisticSnapshot, spec: LearnedSpec,
    costs: CostScenario, *, initial_capital: float, bootstrap_draws: int, seed: int,
    _predicted_rows: Sequence[tuple[IntradayObservation, float]] | None = None,
) -> dict[str, Any]:
    if spec.horizon_seconds != model.horizon_seconds or abs(spec.ridge - model.ridge) > 1e-12:
        raise ValueError("model and specification mismatch")
    if costs.stake > initial_capital:
        raise ValueError("stake exceeds initial capital")
    trades: list[dict[str, float | int | str]] = []
    capital = initial_capital
    skipped = 0
    ruined_at: int | None = None
    if _predicted_rows is None:
        selected = sorted(
            (row for row in rows if row.horizon_seconds == spec.horizon_seconds),
            key=lambda row: (row.epoch, row.slug),
        )
        predictions = tuple((row, predict_up(model, row)) for row in selected)
    else:
        predictions = tuple(_predicted_rows)
        selected = [row for row, _ in predictions]
    for row, probability_up in predictions:
        candidates: list[tuple[float, str, float, int]] = []
        for side, observed, probability, payout in (
            ("UP", row.up_price, probability_up, row.outcome_up),
            ("DOWN", row.down_price, 1 - probability_up, 1 - row.outcome_up),
        ):
            entry = observed + costs.slippage
            if not 0 < entry <= spec.max_ask or entry >= 1:
                continue
            fee = costs.fee_curve * entry * (1 - entry)
            edge = probability - entry - fee
            if edge >= spec.min_edge:
                candidates.append((edge, side, entry + fee, payout))
        if not candidates:
            continue
        edge, side, unit_cost, payout = max(candidates)
        if capital < costs.stake:
            skipped += 1
            ruined_at = ruined_at or row.epoch
            continue
        shares = costs.stake / unit_cost
        pnl = shares * payout - costs.stake
        capital += pnl
        trades.append({
            "slug": row.slug, "epoch": row.epoch, "side": side, "pnl": pnl,
            "stake": costs.stake, "edge": edge, "probability_up": probability_up,
        })
    calendar_start = selected[0].epoch if selected else None
    calendar_end = selected[-1].epoch if selected else None
    metrics = summarize_trades(
        trades, initial_capital=initial_capital, bootstrap_draws=bootstrap_draws, seed=seed,
        calendar_start=calendar_start, calendar_end=calendar_end,
    )
    metrics.update({
        "skipped_insufficient_capital": skipped,
        "ruined_at_epoch": ruined_at,
        "capital_path_executable": True,
        "capital_accounting_assumption": "IMMEDIATE_SETTLEMENT_BETWEEN_MARKETS",
    })
    return {"spec": asdict(spec), "costs": asdict(costs), "metrics": metrics, "trade_log": trades}


def learn_validate_holdout(
    rows: Sequence[IntradayObservation], specs: Sequence[LearnedSpec], *,
    config: EvaluationConfig, costs: CostScenario = CostScenario("conservative", 0.03, 0.10, 10.0),
    progress: Callable[[str], None] | None = None,
    checkpoint_dir: Path | None = None,
) -> dict[str, Any]:
    """Fit on train, select on validation, and open the fixed holdout at most once per report."""
    if not specs:
        raise ValueError("learned specifications required")
    checkpoint = None
    if checkpoint_dir is not None:
        if progress:
            progress("verifying development checkpoint fingerprints")
        checkpoint = DevelopmentCheckpoint(
            checkpoint_dir,
            evaluation_identity(
                rows=rows, specs=specs, config=config, costs=[costs],
                implementation_paths=[
                    Path(__file__), Path(__file__).with_name("robust_intraday.py"),
                    Path(__file__).with_name("btc_intraday.py"),
                ],
            ),
            len(specs),
        )
        saved = checkpoint.load_final()
        if saved is not None:
            _restore_snapshot_tuples(saved)
            if progress:
                progress("restored completed evaluation; no holdout replay")
            return saved
    if progress:
        progress(f"splitting {len(rows)} observations chronologically")
    split = chronological_split(
        rows, train_fraction=config.train_fraction, validation_fraction=config.validation_fraction,
        embargo_seconds=config.embargo_seconds,
        fixed_holdout_start_epoch=config.fixed_holdout_start_epoch,
    )
    models: dict[tuple[int, float], LogisticSnapshot] = {}
    predictions: dict[
        tuple[int, float],
        tuple[
            tuple[tuple[IntradayObservation, float], ...],
            tuple[tuple[IntradayObservation, float], ...],
        ],
    ] = {}
    horizons = {spec.horizon_seconds for spec in specs}
    train_by_horizon = {
        horizon: tuple(row for row in split.train if row.horizon_seconds == horizon)
        for horizon in horizons
    }
    validation_by_horizon = {
        horizon: tuple(row for row in split.validation if row.horizon_seconds == horizon)
        for horizon in horizons
    }
    test_by_horizon = {
        horizon: tuple(row for row in split.test if row.horizon_seconds == horizon)
        for horizon in horizons
    }
    development: list[dict[str, Any]] = []
    eligible: list[tuple[LearnedSpec, LogisticSnapshot, dict[str, Any]]] = []
    screening_draws = 100
    for index, spec in enumerate(specs):
        if progress:
            progress(f"development candidate {index + 1}/{len(specs)}")
        saved = checkpoint.load_candidate(index) if checkpoint else None
        if saved is not None:
            _restore_snapshot_tuples(saved)
            development.append(saved)
            restored_model = LogisticSnapshot(**saved["model"])
            models[(spec.horizon_seconds, spec.ridge)] = restored_model
            if saved["passes_development_gate"]:
                eligible.append((spec, restored_model, saved))
            if progress:
                progress(f"restored development candidate {index + 1}/{len(specs)}")
            continue
        key = (spec.horizon_seconds, spec.ridge)
        model = models.get(key)
        if model is None:
            if progress:
                progress(f"fitting horizon={spec.horizon_seconds} ridge={spec.ridge}")
            model = fit_logistic(
                train_by_horizon[spec.horizon_seconds],
                horizon_seconds=spec.horizon_seconds, ridge=spec.ridge,
                training_end_epoch=split.train_end_epoch,
            )
            models[key] = model
        predicted = predictions.get(key)
        if predicted is None:
            predicted = (
                tuple((row, predict_up(model, row)) for row in train_by_horizon[spec.horizon_seconds]),
                tuple((row, predict_up(model, row)) for row in validation_by_horizon[spec.horizon_seconds]),
            )
            predictions[key] = predicted
        train = evaluate_learned(
            train_by_horizon[spec.horizon_seconds], model, spec, costs,
            initial_capital=config.initial_capital,
            bootstrap_draws=screening_draws, seed=config.seed + index,
            _predicted_rows=predicted[0],
        )
        validation = evaluate_learned(
            validation_by_horizon[spec.horizon_seconds], model, spec, costs,
            initial_capital=config.initial_capital,
            bootstrap_draws=screening_draws, seed=config.seed + 10_000 + index,
            _predicted_rows=predicted[1],
        )
        train_metrics, validation_metrics = train["metrics"], validation["metrics"]
        basic_passes = (
            train_metrics["trades"] >= config.min_train_trades
            and validation_metrics["trades"] >= config.min_validation_trades
            and train_metrics["active_days"] >= config.min_train_days
            and validation_metrics["active_days"] >= config.min_validation_days
            and train_metrics["net_pnl"] > 0 and validation_metrics["net_pnl"] > 0
            and train_metrics["ruined_at_epoch"] is None
            and validation_metrics["ruined_at_epoch"] is None
        )
        if basic_passes:
            train = evaluate_learned(
                train_by_horizon[spec.horizon_seconds], model, spec, costs,
                initial_capital=config.initial_capital,
                bootstrap_draws=config.bootstrap_draws, seed=config.seed + index,
                _predicted_rows=predicted[0],
            )
            validation = evaluate_learned(
                validation_by_horizon[spec.horizon_seconds], model, spec, costs,
                initial_capital=config.initial_capital,
                bootstrap_draws=config.bootstrap_draws,
                seed=config.seed + 10_000 + index,
                _predicted_rows=predicted[1],
            )
            train_metrics, validation_metrics = train["metrics"], validation["metrics"]
        train_ci = train_metrics["bootstrap"]["net_pnl_ci95"]
        validation_ci = validation_metrics["bootstrap"]["net_pnl_ci95"]
        passes = (
            basic_passes and train_ci is not None and train_ci[0] > 0
            and validation_ci is not None and validation_ci[0] > 0
        )
        record = {
            "spec": asdict(spec), "model": asdict(model), "train": train,
            "validation": validation, "passes_development_gate": passes,
        }
        development.append(record)
        if checkpoint:
            checkpoint.save_candidate(index, record)
        if passes:
            eligible.append((spec, model, record))
    common = {
        "split": {
            "train_rows": len(split.train), "validation_rows": len(split.validation),
            "test_rows": len(split.test), "train_end_epoch": split.train_end_epoch,
            "validation_end_epoch": split.validation_end_epoch,
            "purged_rows_for_embargo": split.purged_rows,
            "fixed_holdout_start_epoch": config.fixed_holdout_start_epoch,
            "test_was_used_for_selection": False,
        },
        "development": development,
        "evaluation_classification": "LEARNED_HISTORICAL_SIGNAL_BACKTEST_NOT_EXECUTION_PROOF",
    }
    if not eligible:
        if progress:
            progress("development complete: no eligible candidate; holdout remains closed")
        result: dict[str, Any] = {
            **common, "status": "NO_LEARNED_CANDIDATE_PASSED_DEVELOPMENT_GATE",
            "selected_spec": None, "selected_model": None, "test": None,
            "holdout_opened": False,
        }
        if checkpoint:
            checkpoint.save_final(result)
        return result

    def score(item: tuple[LearnedSpec, LogisticSnapshot, dict[str, Any]]) -> tuple[float, int]:
        metrics = item[2]["validation"]["metrics"]
        return float(metrics["bootstrap"]["net_pnl_ci95"][0]), int(metrics["trades"])

    selected_spec, selected_model, _ = max(eligible, key=score)
    if checkpoint:
        checkpoint.begin_holdout()
    if progress:
        progress("development complete: evaluating the selected candidate on holdout")
    selected_test_rows = test_by_horizon[selected_spec.horizon_seconds]
    selected_test_predictions = tuple(
        (row, predict_up(selected_model, row)) for row in selected_test_rows
    )
    test = evaluate_learned(
        selected_test_rows, selected_model, selected_spec, costs,
        initial_capital=config.initial_capital, bootstrap_draws=config.bootstrap_draws,
        seed=config.seed + 20_000,
        _predicted_rows=selected_test_predictions,
    )
    metrics = test["metrics"]
    ci = metrics["bootstrap"]["net_pnl_ci95"]
    sufficient = (
        metrics["trades"] >= config.min_test_trades
        and metrics["active_days"] >= config.min_test_days and ci is not None
    )
    passed = (
        sufficient and metrics["net_pnl"] > 0 and metrics["ruined_at_epoch"] is None
        and ci[0] > 0
    )
    result = {
        **common,
        "status": "HOLDOUT_PASSED" if passed else (
            "HOLDOUT_FAILED" if sufficient else "HOLDOUT_INSUFFICIENT"
        ),
        "selected_spec": asdict(selected_spec), "selected_model": asdict(selected_model),
        "test": test, "holdout_opened": True,
    }
    if checkpoint:
        checkpoint.save_final(result)
    return result


def _restore_snapshot_tuples(value: Any) -> None:
    """JSON caches must return the same snapshot shapes as a fresh evaluation."""
    if isinstance(value, dict):
        if {"means", "scales", "coefficients", "training_rows"}.issubset(value):
            for field in ("means", "scales", "coefficients"):
                value[field] = tuple(value[field])
        for child in value.values():
            _restore_snapshot_tuples(child)
    elif isinstance(value, list):
        for child in value:
            _restore_snapshot_tuples(child)
