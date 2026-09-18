from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import asdict, dataclass
from math import isfinite
from pathlib import Path
from statistics import mean
from typing import Any, Callable, Sequence

from edgehunter.research.btc_intraday import IntradayObservation, IntradaySpec, simulate_trade
from edgehunter.research.evaluation_checkpoint import DevelopmentCheckpoint, evaluation_identity


@dataclass(frozen=True)
class CostScenario:
    name: str
    slippage: float
    fee_curve: float
    stake: float

    def __post_init__(self) -> None:
        if not self.name or not all(isfinite(value) for value in (self.slippage, self.fee_curve, self.stake)):
            raise ValueError("invalid cost scenario")
        if self.slippage < 0 or self.fee_curve < 0 or self.stake <= 0:
            raise ValueError("invalid cost scenario")


@dataclass(frozen=True)
class EvaluationConfig:
    train_fraction: float = 0.60
    validation_fraction: float = 0.20
    min_train_trades: int = 10
    min_validation_trades: int = 10
    min_train_days: int = 5
    min_validation_days: int = 3
    min_test_trades: int = 20
    min_test_days: int = 3
    embargo_seconds: int = 7_200
    minimum_monte_carlo_days: int = 5
    fixed_holdout_start_epoch: int | None = None
    initial_capital: float = 100.0
    bootstrap_draws: int = 2_000
    monte_carlo_runs: int = 5_000
    monte_carlo_horizon_trades: int = 300
    seed: int = 20260917

    def __post_init__(self) -> None:
        numbers = (self.train_fraction, self.validation_fraction, self.initial_capital)
        if not all(isfinite(value) for value in numbers):
            raise ValueError("invalid evaluation config")
        if not 0 < self.train_fraction < 1 or not 0 < self.validation_fraction < 1:
            raise ValueError("invalid split fractions")
        if self.train_fraction + self.validation_fraction >= 1:
            raise ValueError("test fraction must be positive")
        if min(self.min_train_trades, self.min_validation_trades, self.min_test_trades) < 1:
            raise ValueError("minimum trades must be positive")
        if min(self.min_train_days, self.min_validation_days, self.min_test_days) < 1:
            raise ValueError("minimum days must be positive")
        if self.embargo_seconds < 0 or self.minimum_monte_carlo_days < 2:
            raise ValueError("invalid temporal safeguards")
        if self.fixed_holdout_start_epoch is not None and self.fixed_holdout_start_epoch < 0:
            raise ValueError("invalid fixed holdout start")
        if self.initial_capital <= 0 or self.bootstrap_draws < 100:
            raise ValueError("invalid simulation budget")
        if self.monte_carlo_runs < 100 or self.monte_carlo_horizon_trades < 1:
            raise ValueError("invalid Monte Carlo config")


@dataclass(frozen=True)
class ChronologicalSplit:
    train: tuple[IntradayObservation, ...]
    validation: tuple[IntradayObservation, ...]
    test: tuple[IntradayObservation, ...]
    train_end_epoch: int
    validation_end_epoch: int
    purged_rows: int = 0


DEFAULT_COSTS = (
    CostScenario("base", slippage=0.01, fee_curve=0.07, stake=10.0),
    CostScenario("conservative", slippage=0.03, fee_curve=0.10, stake=10.0),
)


def chronological_split(
    observations: Sequence[IntradayObservation],
    *,
    train_fraction: float = 0.60,
    validation_fraction: float = 0.20,
    embargo_seconds: int = 0,
    fixed_holdout_start_epoch: int | None = None,
) -> ChronologicalSplit:
    """Split whole epochs in order, so one market instant never leaks across sets."""
    if not observations:
        raise ValueError("observations required")
    if not 0 < train_fraction < 1 or not 0 < validation_fraction < 1:
        raise ValueError("invalid split fractions")
    if train_fraction + validation_fraction >= 1 or embargo_seconds < 0:
        raise ValueError("test fraction must be positive")

    epochs = sorted({row.epoch for row in observations})
    if len(epochs) < 3:
        raise ValueError("at least three distinct epochs required")
    if fixed_holdout_start_epoch is None:
        train_count = max(1, int(len(epochs) * train_fraction))
        validation_count = max(1, int(len(epochs) * validation_fraction))
        if train_count + validation_count >= len(epochs):
            validation_count = len(epochs) - train_count - 1
        if validation_count < 1:
            raise ValueError("insufficient epochs for three splits")
        raw_train = epochs[:train_count]
        raw_validation = epochs[train_count : train_count + validation_count]
        raw_test = epochs[train_count + validation_count :]
    else:
        development_epochs = [epoch for epoch in epochs if epoch < fixed_holdout_start_epoch]
        raw_test = [epoch for epoch in epochs if epoch >= fixed_holdout_start_epoch]
        if len(development_epochs) < 2 or not raw_test:
            raise ValueError("fixed holdout leaves insufficient development or test epochs")
        development_train_fraction = train_fraction / (train_fraction + validation_fraction)
        train_count = max(1, int(len(development_epochs) * development_train_fraction))
        train_count = min(train_count, len(development_epochs) - 1)
        raw_train = development_epochs[:train_count]
        raw_validation = development_epochs[train_count:]
    validation_start = raw_validation[0]
    test_start = raw_test[0]
    train_epochs = {epoch for epoch in raw_train if epoch < validation_start - embargo_seconds}
    validation_epochs = {
        epoch for epoch in raw_validation
        if epoch >= validation_start + embargo_seconds and epoch < test_start - embargo_seconds
    }
    test_epochs = {epoch for epoch in raw_test if epoch >= test_start + embargo_seconds}
    if not train_epochs or not validation_epochs or not test_epochs:
        raise ValueError("embargo leaves an empty split")
    ordered = sorted(observations, key=lambda row: (row.epoch, row.slug, row.horizon_seconds))
    train = tuple(row for row in ordered if row.epoch in train_epochs)
    validation = tuple(row for row in ordered if row.epoch in validation_epochs)
    test = tuple(row for row in ordered if row.epoch in test_epochs)
    return ChronologicalSplit(
        train=train,
        validation=validation,
        test=test,
        train_end_epoch=max(train_epochs),
        validation_end_epoch=max(validation_epochs),
        # The epoch sets are disjoint; do not rebuild their union for every row.
        purged_rows=len(ordered) - len(train) - len(validation) - len(test),
    )


def _percentile(values: Sequence[float], probability: float) -> float:
    if not values:
        raise ValueError("values required")
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _bootstrap_interval(
    trades: Sequence[dict[str, float | int | str]], *, seed: int, draws: int,
    calendar_start: int | None = None, calendar_end: int | None = None,
) -> dict[str, Any]:
    """Resample UTC-day clusters to preserve dependence among nearby 5m markets."""
    if draws < 100:
        raise ValueError("at least one hundred bootstrap draws required")
    clusters: dict[int, list[float]] = defaultdict(list)
    for trade in trades:
        clusters[int(trade["epoch"]) // 86_400].append(float(trade["pnl"]))
    if calendar_start is not None and calendar_end is not None and calendar_end >= calendar_start:
        for day in range(calendar_start // 86_400, calendar_end // 86_400 + 1):
            clusters.setdefault(day, [])
    day_pnls = [sum(values) for _, values in sorted(clusters.items())]
    if len(day_pnls) < 2:
        return {
            "method": "UTC-day cluster bootstrap",
            "clusters": len(day_pnls),
            "draws": draws,
            "seed": seed,
            "net_pnl_ci95": None,
        }
    rng = random.Random(seed)
    samples = [sum(rng.choice(day_pnls) for _ in day_pnls) for _ in range(draws)]
    return {
        "method": "UTC-day cluster bootstrap",
        "clusters": len(day_pnls),
        "draws": draws,
        "seed": seed,
        "net_pnl_ci95": [_percentile(samples, 0.025), _percentile(samples, 0.975)],
        "positive_fraction": sum(sample > 0 for sample in samples) / draws,
    }


def summarize_trades(
    trades: Sequence[dict[str, float | int | str]],
    *,
    initial_capital: float,
    bootstrap_draws: int,
    seed: int,
    calendar_start: int | None = None,
    calendar_end: int | None = None,
) -> dict[str, Any]:
    if not isfinite(initial_capital) or initial_capital <= 0:
        raise ValueError("initial capital must be positive")
    ordered = sorted(trades, key=lambda trade: (int(trade["epoch"]), str(trade["slug"])))
    pnls = [float(trade["pnl"]) for trade in ordered]
    equity = peak = initial_capital
    max_drawdown = 0.0
    max_drawdown_fraction = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        drawdown = peak - equity
        max_drawdown = max(max_drawdown, drawdown)
        if peak > 0:
            max_drawdown_fraction = max(max_drawdown_fraction, drawdown / peak)
    total_staked = sum(float(trade["stake"]) for trade in ordered)
    net_pnl = sum(pnls)
    return {
        "trades": len(ordered),
        "wins": sum(pnl > 0 for pnl in pnls),
        "losses": sum(pnl <= 0 for pnl in pnls),
        "win_rate": sum(pnl > 0 for pnl in pnls) / len(pnls) if pnls else None,
        "net_pnl": net_pnl,
        "mean_pnl": mean(pnls) if pnls else None,
        "return_on_stake": net_pnl / total_staked if total_staked else None,
        "initial_capital": initial_capital,
        "final_capital": initial_capital + net_pnl,
        "max_drawdown": max_drawdown,
        "max_drawdown_fraction": max_drawdown_fraction,
        "active_days": len({int(trade["epoch"]) // 86_400 for trade in ordered}),
        "bootstrap": _bootstrap_interval(
            ordered, seed=seed, draws=bootstrap_draws,
            calendar_start=calendar_start, calendar_end=calendar_end,
        ),
    }


def evaluate_spec(
    observations: Sequence[IntradayObservation],
    spec: IntradaySpec,
    costs: CostScenario,
    *,
    initial_capital: float,
    bootstrap_draws: int,
    seed: int,
) -> dict[str, Any]:
    if costs.stake > initial_capital:
        raise ValueError("stake exceeds initial capital")
    trades: list[dict[str, float | int | str]] = []
    candidate_signals = 0
    skipped_insufficient_capital = 0
    ruined_at: int | None = None
    capital = initial_capital
    ordered_observations = sorted(
        observations, key=lambda row: (row.epoch, row.slug, row.horizon_seconds),
    )
    for observation in ordered_observations:
        trade = simulate_trade(
            observation,
            spec,
            slippage=costs.slippage,
            fee_curve=costs.fee_curve,
            stake=costs.stake,
        )
        if trade is not None:
            candidate_signals += 1
            if capital < costs.stake:
                skipped_insufficient_capital += 1
                if ruined_at is None:
                    ruined_at = observation.epoch
                continue
            trade["stake"] = costs.stake
            trades.append(trade)
            capital += float(trade["pnl"])
    calendar_start = ordered_observations[0].epoch if ordered_observations else None
    calendar_end = ordered_observations[-1].epoch if ordered_observations else None
    metrics = summarize_trades(
        trades,
        initial_capital=initial_capital,
        bootstrap_draws=bootstrap_draws,
        seed=seed,
        calendar_start=calendar_start,
        calendar_end=calendar_end,
    )
    metrics.update({
        "candidate_signals": candidate_signals,
        "skipped_insufficient_capital": skipped_insufficient_capital,
        "ruined_at_epoch": ruined_at,
        "capital_path_executable": True,
        "capital_accounting_assumption": "IMMEDIATE_SETTLEMENT_BETWEEN_MARKETS",
    })
    return {
        "spec": spec.to_dict(),
        "costs": asdict(costs),
        "metrics": metrics,
        "trade_log": trades,
    }


def monte_carlo_risk_of_ruin(
    pnls: Sequence[float] | Sequence[Sequence[float]],
    *,
    initial_capital: float,
    minimum_operating_capital: float,
    runs: int,
    horizon_trades: int,
    seed: int,
) -> dict[str, Any]:
    """Bootstrap whole ordered UTC-day blocks and stop when another stake cannot be funded."""
    parameters = (initial_capital, minimum_operating_capital)
    if not pnls:
        raise ValueError("finite historical PnLs required")
    first = pnls[0]
    if isinstance(first, Sequence) and not isinstance(first, (str, bytes)):
        day_blocks = [[float(value) for value in block] for block in pnls]  # type: ignore[union-attr]
    else:
        day_blocks = [[float(value)] for value in pnls]  # type: ignore[arg-type]
    flat = [value for block in day_blocks for value in block]
    if not flat or not all(isfinite(value) for value in (*parameters, *flat)):
        raise ValueError("finite historical PnLs required")
    if initial_capital <= 0 or minimum_operating_capital <= 0:
        raise ValueError("capital parameters must be positive")
    if runs < 100 or horizon_trades < 1:
        raise ValueError("invalid Monte Carlo dimensions")
    rng = random.Random(seed)
    terminal: list[float] = []
    max_drawdowns: list[float] = []
    ruined = 0
    for _ in range(runs):
        capital = peak = initial_capital
        max_drawdown = 0.0
        run_ruined = capital < minimum_operating_capital
        simulated_trades = 0
        while simulated_trades < horizon_trades and not run_ruined:
            block = rng.choice(day_blocks)
            for pnl in block:
                if simulated_trades >= horizon_trades or run_ruined:
                    break
                capital += pnl
                simulated_trades += 1
                peak = max(peak, capital)
                max_drawdown = max(max_drawdown, peak - capital)
                run_ruined = capital < minimum_operating_capital
        ruined += int(run_ruined)
        terminal.append(capital)
        max_drawdowns.append(max_drawdown)
    return {
        "method": "UTC-day block bootstrap with replacement; order preserved within day",
        "source_day_blocks": len(day_blocks),
        "runs": runs,
        "horizon_trades": horizon_trades,
        "seed": seed,
        "initial_capital": initial_capital,
        "minimum_operating_capital": minimum_operating_capital,
        "risk_of_ruin": ruined / runs,
        "terminal_capital_p05": _percentile(terminal, 0.05),
        "terminal_capital_median": _percentile(terminal, 0.50),
        "terminal_capital_p95": _percentile(terminal, 0.95),
        "max_drawdown_p95": _percentile(max_drawdowns, 0.95),
    }


def evaluate_untouched_holdout(
    observations: Sequence[IntradayObservation],
    specs: Sequence[IntradaySpec],
    *,
    config: EvaluationConfig = EvaluationConfig(),
    cost_scenarios: Sequence[CostScenario] = DEFAULT_COSTS,
    progress: Callable[[str], None] | None = None,
    checkpoint_dir: Path | None = None,
) -> dict[str, Any]:
    """Select on train/validation, then evaluate only the winner on final test."""
    if not specs:
        raise ValueError("candidate specs required")
    if len(cost_scenarios) < 2:
        raise ValueError("base and conservative cost scenarios required")
    names = [scenario.name for scenario in cost_scenarios]
    if len(names) != len(set(names)):
        raise ValueError("cost scenario names must be unique")
    if "conservative" not in names:
        raise ValueError("a conservative cost scenario is required")
    conservative = next(scenario for scenario in cost_scenarios if scenario.name == "conservative")
    for scenario in cost_scenarios:
        if scenario.name != conservative.name and (
            conservative.slippage < scenario.slippage
            or conservative.fee_curve < scenario.fee_curve
            or conservative.stake != scenario.stake
        ):
            raise ValueError("conservative costs must dominate other scenarios at equal stake")
    checkpoint = None
    if checkpoint_dir is not None:
        if progress:
            progress("verifying development checkpoint fingerprints")
        checkpoint = DevelopmentCheckpoint(
            checkpoint_dir,
            evaluation_identity(
                rows=observations, specs=specs, config=config, costs=cost_scenarios,
                implementation_paths=[Path(__file__), Path(__file__).with_name("btc_intraday.py")],
            ),
            len(specs),
        )
        saved = checkpoint.load_final()
        if saved is not None:
            if progress:
                progress("restored completed evaluation; no holdout replay")
            return saved
    if progress:
        progress(f"splitting {len(observations)} observations chronologically")
    split = chronological_split(
        observations,
        train_fraction=config.train_fraction,
        validation_fraction=config.validation_fraction,
        embargo_seconds=config.embargo_seconds,
        fixed_holdout_start_epoch=config.fixed_holdout_start_epoch,
    )
    train_by_horizon = {
        horizon: tuple(row for row in split.train if row.horizon_seconds == horizon)
        for horizon in {spec.horizon_seconds for spec in specs}
    }
    validation_by_horizon = {
        horizon: tuple(row for row in split.validation if row.horizon_seconds == horizon)
        for horizon in train_by_horizon
    }
    test_by_horizon = {
        horizon: tuple(row for row in split.test if row.horizon_seconds == horizon)
        for horizon in train_by_horizon
    }
    development: list[dict[str, Any]] = []
    eligible: list[tuple[IntradaySpec, dict[str, Any]]] = []
    screening_draws = 100
    for index, spec in enumerate(specs):
        if progress:
            progress(f"development candidate {index + 1}/{len(specs)}")
        saved = checkpoint.load_candidate(index) if checkpoint else None
        if saved is not None:
            development.append(saved)
            if saved["passes_development_gate"]:
                eligible.append((spec, saved))
            if progress:
                progress(f"restored development candidate {index + 1}/{len(specs)}")
            continue
        train_results = {
            scenario.name: evaluate_spec(
                train_by_horizon[spec.horizon_seconds],
                spec,
                scenario,
                initial_capital=config.initial_capital,
                bootstrap_draws=screening_draws,
                seed=config.seed + index,
            )
            for scenario in cost_scenarios
        }
        validation_results = {
            scenario.name: evaluate_spec(
                validation_by_horizon[spec.horizon_seconds],
                spec,
                scenario,
                initial_capital=config.initial_capital,
                bootstrap_draws=screening_draws,
                seed=config.seed + 10_000 + index,
            )
            for scenario in cost_scenarios
        }
        train_metrics = train_results[conservative.name]["metrics"]
        validation_metrics = validation_results[conservative.name]["metrics"]
        basic_passes = (
            train_metrics["trades"] >= config.min_train_trades
            and validation_metrics["trades"] >= config.min_validation_trades
            and train_metrics["active_days"] >= config.min_train_days
            and validation_metrics["active_days"] >= config.min_validation_days
            and train_metrics["net_pnl"] > 0
            and validation_metrics["net_pnl"] > 0
            and train_metrics["ruined_at_epoch"] is None
            and validation_metrics["ruined_at_epoch"] is None
        )
        if basic_passes:
            train_results[conservative.name] = evaluate_spec(
                train_by_horizon[spec.horizon_seconds], spec, conservative,
                initial_capital=config.initial_capital,
                bootstrap_draws=config.bootstrap_draws, seed=config.seed + index,
            )
            validation_results[conservative.name] = evaluate_spec(
                validation_by_horizon[spec.horizon_seconds], spec, conservative,
                initial_capital=config.initial_capital,
                bootstrap_draws=config.bootstrap_draws, seed=config.seed + 10_000 + index,
            )
            train_metrics = train_results[conservative.name]["metrics"]
            validation_metrics = validation_results[conservative.name]["metrics"]
        train_ci = train_metrics["bootstrap"]["net_pnl_ci95"]
        validation_ci = validation_metrics["bootstrap"]["net_pnl_ci95"]
        passes = (
            basic_passes
            and train_ci is not None and train_ci[0] > 0
            and validation_ci is not None and validation_ci[0] > 0
        )
        record = {
            "spec": spec.to_dict(),
            "train": train_results,
            "validation": validation_results,
            "passes_development_gate": passes,
        }
        development.append(record)
        if checkpoint:
            checkpoint.save_candidate(index, record)
        if passes:
            eligible.append((spec, record))

    split_summary = {
        "train_rows": len(split.train),
        "validation_rows": len(split.validation),
        "test_rows": len(split.test),
        "train_end_epoch": split.train_end_epoch,
        "validation_end_epoch": split.validation_end_epoch,
        "purged_rows_for_embargo": split.purged_rows,
        "embargo_seconds": config.embargo_seconds,
        "fixed_holdout_start_epoch": config.fixed_holdout_start_epoch,
        "test_was_used_for_selection": False,
    }
    if not eligible:
        if progress:
            progress("development complete: no eligible candidate; holdout remains closed")
        result: dict[str, Any] = {
            "status": "NO_CANDIDATE_PASSED_DEVELOPMENT_GATE",
            "split": split_summary,
            "selection_cost_scenario": conservative.name,
            "development": development,
            "selected_spec": None,
            "test": None,
            "monte_carlo": None,
            "evaluation_classification": "HISTORICAL_SIGNAL_BACKTEST_NOT_EXECUTION_PROOF",
            "holdout_opened": False,
        }
        if checkpoint:
            checkpoint.save_final(result)
        return result

    def selection_score(item: tuple[IntradaySpec, dict[str, Any]]) -> tuple[float, float, int]:
        record = item[1]
        validation_metrics = record["validation"][conservative.name]["metrics"]
        train_metrics = record["train"][conservative.name]["metrics"]
        validation_ci = validation_metrics["bootstrap"]["net_pnl_ci95"]
        train_ci = train_metrics["bootstrap"]["net_pnl_ci95"]
        return (
            float(validation_ci[0]),
            float(train_ci[0]),
            int(validation_metrics["trades"]),
        )

    selected, _ = max(eligible, key=selection_score)
    if checkpoint:
        checkpoint.begin_holdout()
    if progress:
        progress("development complete: evaluating the selected candidate on holdout")
    test_results = {
        scenario.name: evaluate_spec(
            test_by_horizon[selected.horizon_seconds],
            selected,
            scenario,
            initial_capital=config.initial_capital,
            bootstrap_draws=config.bootstrap_draws,
            seed=config.seed + 20_000,
        )
        for scenario in cost_scenarios
    }
    conservative_result = test_results[conservative.name]
    conservative_trades = conservative_result["trade_log"]
    test_metrics = conservative_result["metrics"]
    monte_carlo = None
    day_blocks: dict[int, list[float]] = defaultdict(list)
    for trade in conservative_trades:
        day_blocks[int(trade["epoch"]) // 86_400].append(float(trade["pnl"]))
    if len(day_blocks) >= config.minimum_monte_carlo_days:
        monte_carlo = monte_carlo_risk_of_ruin(
            [day_blocks[day] for day in sorted(day_blocks)],
            initial_capital=config.initial_capital,
            minimum_operating_capital=conservative.stake,
            runs=config.monte_carlo_runs,
            horizon_trades=config.monte_carlo_horizon_trades,
            seed=config.seed + 30_000,
        )
    test_ci = test_metrics["bootstrap"]["net_pnl_ci95"]
    sufficient = (
        test_metrics["trades"] >= config.min_test_trades
        and test_metrics["active_days"] >= config.min_test_days
        and test_ci is not None
    )
    passed = (
        sufficient
        and test_metrics["net_pnl"] > 0
        and test_metrics["ruined_at_epoch"] is None
        and test_ci[0] > 0
    )
    status = "HOLDOUT_PASSED" if passed else ("HOLDOUT_FAILED" if sufficient else "HOLDOUT_INSUFFICIENT")
    result = {
        "status": status,
        "split": split_summary,
        "selection_cost_scenario": conservative.name,
        "development": development,
        "selected_spec": selected.to_dict(),
        "test": test_results,
        "monte_carlo": monte_carlo,
        "evaluation_classification": "HISTORICAL_SIGNAL_BACKTEST_NOT_EXECUTION_PROOF",
        "holdout_opened": True,
        "holdout_gate": {
            "minimum_trades": config.min_test_trades,
            "minimum_active_days": config.min_test_days,
            "net_pnl_required": "> 0",
            "bootstrap_95_lower_required": "> 0",
            "capital_must_remain_executable": True,
        },
    }
    if checkpoint:
        checkpoint.save_final(result)
    return result
