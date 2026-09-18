from __future__ import annotations

import json
import random
from collections import defaultdict
from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal
from hashlib import sha256
from math import log
from pathlib import Path
from statistics import mean
from typing import Any, Iterable, Protocol, Sequence

D = Decimal


@dataclass(frozen=True)
class HistoricalObservation:
    event_id: str
    market_id: str
    decision_at: int
    price_available_at: int
    settlement_at: int
    yes_price: Decimal
    no_price: Decimal
    outcome_yes: int
    cluster_id: str
    historical_availability_verified: bool = False
    category: str = "unknown"

    def __post_init__(self) -> None:
        if self.price_available_at > self.decision_at or self.settlement_at <= self.decision_at:
            raise ValueError("temporal leakage")
        if self.outcome_yes not in (0, 1):
            raise ValueError("unresolved outcome")
        for price in (self.yes_price, self.no_price):
            if not price.is_finite() or not 0 < price < 1:
                raise ValueError("invalid price")


@dataclass(frozen=True)
class Hypothesis:
    identifier: str
    direction: str
    threshold: Decimal
    max_price: Decimal = D("0.98")

    def __post_init__(self) -> None:
        if not self.identifier or self.direction not in ("favorite", "underdog"):
            raise ValueError("invalid hypothesis")
        if not all(p.is_finite() and 0 <= p <= 1 for p in (self.threshold, self.max_price)):
            raise ValueError("invalid hypothesis probability")

    def side(self, row: HistoricalObservation) -> tuple[str, Decimal, int] | None:
        favorite_yes = row.yes_price >= row.no_price
        yes = favorite_yes if self.direction == "favorite" else not favorite_yes
        price, outcome = (row.yes_price, row.outcome_yes) if yes else (row.no_price, 1 - row.outcome_yes)
        selected = price >= self.threshold if self.direction == "favorite" else price <= self.threshold
        return ("YES" if yes else "NO", price, outcome) if selected and price <= self.max_price else None


class DecisionRule(Protocol):
    @property
    def identifier(self) -> str: ...

    def side(self, row: HistoricalObservation) -> tuple[str, Decimal, int] | None: ...


def temporal_split(rows: Sequence[HistoricalObservation], train_end: int, validation_end: int, embargo_seconds: int = 86400) -> dict[str, list[HistoricalObservation]]:
    """Purge labels overlapping next split; one event can occur in only one set."""
    if train_end >= validation_end or embargo_seconds < 0:
        raise ValueError("invalid split boundaries")
    output: dict[str, list[HistoricalObservation]] = {"train": [], "validation": [], "test": [], "purged": []}
    seen: set[str] = set()
    for row in sorted(rows, key=lambda x: (x.decision_at, x.event_id)):
        if row.event_id in seen:
            raise ValueError("duplicate event across dataset")
        seen.add(row.event_id)
        if row.decision_at < train_end:
            part = "train" if row.settlement_at + embargo_seconds < train_end else "purged"
        elif row.decision_at < validation_end:
            part = "validation" if row.settlement_at + embargo_seconds < validation_end else "purged"
        else:
            part = "test"
        output[part].append(row)
    memberships: dict[str, set[str]] = defaultdict(set)
    for part in ("train", "validation", "test"):
        for row in output[part]:
            memberships[row.cluster_id].add(part)
    spanning = {cluster for cluster, parts in memberships.items() if len(parts) > 1}
    for part in ("train", "validation", "test"):
        output["purged"].extend(row for row in output[part] if row.cluster_id in spanning)
        output[part] = [row for row in output[part] if row.cluster_id not in spanning]
    return output


def calibration_metrics(probabilities: Sequence[float], outcomes: Sequence[int]) -> dict[str, Any]:
    if not probabilities or len(probabilities) != len(outcomes):
        raise ValueError("nonempty matched predictions required")
    if any(not 0 <= p <= 1 for p in probabilities) or any(y not in (0, 1) for y in outcomes):
        raise ValueError("invalid forecast")
    brier = mean((p - y) ** 2 for p, y in zip(probabilities, outcomes))
    loss = -mean(y * log(max(1e-12, p)) + (1 - y) * log(max(1e-12, 1 - p)) for p, y in zip(probabilities, outcomes))
    buckets: list[dict[str, Any]] = []
    for i in range(10):
        pairs = [(p, y) for p, y in zip(probabilities, outcomes) if min(9, int(p * 10)) == i]
        if pairs:
            buckets.append({"lower": i / 10, "count": len(pairs), "mean_probability": mean(p for p, _ in pairs), "frequency": mean(y for _, y in pairs)})
    return {"n": len(probabilities), "brier": brier, "log_loss": loss, "reliability": buckets}


@dataclass(frozen=True)
class BinCalibrator:
    """Regularized bins retain market baseline as prior; candidate only."""
    rates: tuple[float, ...]
    label_cutoff: int
    trained_events: int

    @classmethod
    def fit(cls, rows: Sequence[HistoricalObservation], training_cutoff: int, prior_strength: int = 20) -> BinCalibrator:
        if prior_strength <= 0 or not rows:
            raise ValueError("training data/prior missing")
        if any(r.settlement_at >= training_cutoff for r in rows):
            raise ValueError("future training labels")
        rates = []
        for i in range(10):
            bucket = [r for r in rows if min(9, int(r.yes_price * 10)) == i]
            prior = (i + 0.5) / 10
            rates.append((sum(r.outcome_yes for r in bucket) + prior_strength * prior) / (len(bucket) + prior_strength))
        return cls(tuple(rates), training_cutoff, len(rows))

    def predict(self, price: Decimal, issued_at: int) -> float:
        if issued_at <= self.label_cutoff or not price.is_finite() or not 0 <= price <= 1:
            raise ValueError("invalid forecast time or price")
        return self.rates[min(9, int(price * 10))]


def cluster_bootstrap(pnls: Iterable[tuple[str, float]], seed: int = 20260917, draws: int = 2000) -> dict[str, Any]:
    """Resample independent day/event clusters, never individual ticks."""
    if draws < 40:
        raise ValueError("at least forty bootstrap draws required")
    clusters: dict[str, float] = defaultdict(float)
    for cluster, pnl in pnls:
        clusters[cluster] += pnl
    values = list(clusters.values())
    if len(values) < 2:
        return {"clusters": len(values), "ci95": None, "positive_fraction": None, "seed": seed}
    rng = random.Random(seed)
    samples = sorted(sum(rng.choice(values) for _ in values) for _ in range(draws))
    return {"clusters": len(values), "ci95": [samples[int(draws * .025)], samples[min(draws - 1, int(draws * .975))]], "positive_fraction": sum(p > 0 for p in samples) / draws, "seed": seed, "draws": draws, "method": "cluster percentile; not proof of independence across dates"}


def evaluate(rows: Sequence[HistoricalObservation], hypothesis: DecisionRule, *, initial_capital: Decimal = D("1000"), risk_per_event: Decimal = D("10"), max_locked: Decimal = D("100"), slippage_per_share: Decimal = D("0.01"), fee_rate: Decimal = D("0.07"), minimum_shares: Decimal = D("5")) -> dict[str, Any]:
    if not all(value.is_finite() for value in (initial_capital, risk_per_event, max_locked, slippage_per_share, fee_rate, minimum_shares)):
        raise ValueError("non-finite simulation parameter")
    if min(initial_capital, risk_per_event, max_locked, minimum_shares) <= 0 or min(slippage_per_share, fee_rate) < 0:
        raise ValueError("invalid simulation parameters")
    if max_locked > initial_capital or risk_per_event > max_locked:
        raise ValueError("invalid exposure budget")
    cash, peak_equity, max_drawdown = initial_capital, initial_capital, D("0")
    active: list[tuple[int, Decimal, Decimal, str]] = []
    trades: list[dict[str, Any]] = []
    ignored: dict[str, int] = defaultdict(int)
    max_locked_observed = D("0")
    pnl_by_cluster: list[tuple[str, float]] = []

    def settle(until: int) -> None:
        nonlocal cash, peak_equity, max_drawdown
        matured = sorted((a for a in active if a[0] <= until), key=lambda a: (a[0], a[3]))
        for item in matured:
            active.remove(item)
            cash += item[2]
            # Cost basis equity is explicitly not continuous mark-to-liquidation.
            equity = cash + sum((a[1] for a in active), D("0"))
            peak_equity = max(peak_equity, equity)
            max_drawdown = max(max_drawdown, peak_equity - equity)

    used_events: set[str] = set()
    for row in sorted(rows, key=lambda r: (r.decision_at, r.event_id)):
        if row.event_id in used_events:
            raise ValueError("duplicate event in simulation")
        used_events.add(row.event_id)
        settle(row.decision_at)
        selection = hypothesis.side(row)
        if selection is None:
            ignored["hypothesis_filter"] += 1
            continue
        side, observed, outcome = selection
        price = observed + slippage_per_share
        if price >= 1:
            ignored["cost_reaches_payout"] += 1
            continue
        fee = fee_rate * price * (1 - price)
        unit_cost = price + fee
        locked = sum((a[1] for a in active), D("0"))
        budget = min(risk_per_event, cash, max_locked - locked)
        shares = (budget / unit_cost).quantize(D("0.01"), rounding=ROUND_FLOOR)
        if shares < minimum_shares:
            ignored["minimum_or_capital_limit"] += 1
            continue
        cost, payout = shares * unit_cost, shares * outcome
        pnl = payout - cost
        cash -= cost
        active.append((row.settlement_at, cost, payout, row.event_id))
        max_locked_observed = max(max_locked_observed, locked + cost)
        pnl_by_cluster.append((row.cluster_id, float(pnl)))
        trades.append({"event_id": row.event_id, "market_id": row.market_id, "decision_at": row.decision_at, "settlement_at": row.settlement_at, "side": side, "observed_price": str(observed), "simulated_entry_price": str(price), "shares": str(shares), "fees": str(shares * fee), "cost": str(cost), "payout": str(payout), "pnl": str(pnl), "cluster": row.cluster_id, "availability_verified": row.historical_availability_verified})
    settle(2**63 - 1)
    turnover = sum((D(t["cost"]) for t in trades), D("0"))
    fee_total = sum((D(t["fees"]) for t in trades), D("0"))
    capital_days = sum(float(D(t["cost"])) * (t["settlement_at"] - t["decision_at"]) / 86400 for t in trades)
    pnl = cash - initial_capital
    return {"hypothesis": hypothesis.identifier, "initial_capital": str(initial_capital), "final_capital": str(cash), "net_pnl": str(pnl), "return_on_initial_capital": str(pnl / initial_capital), "return_on_turnover": str(pnl / turnover) if turnover else None, "turnover": str(turnover), "fees": str(fee_total), "trades": len(trades), "wins": sum(D(t["pnl"]) > 0 for t in trades), "max_locked": str(max_locked_observed), "max_drawdown_settled_cost_basis": str(max_drawdown), "capital_days": capital_days, "pnl_per_capital_day": float(pnl) / capital_days if capital_days else None, "ignored": dict(ignored), "cost_model": {"fee_rate_estimate": str(fee_rate), "slippage_spread_latency_estimate_per_share": str(slippage_per_share), "historical_fees_verified": False, "historical_orderbooks_available": False, "fills_verified": False, "minimum_shares_assumption": str(minimum_shares)}, "bootstrap": cluster_bootstrap(pnl_by_cluster), "trade_log": trades, "classification": "EXPLORATORY_HISTORICAL_SIMULATION_NOT_EXECUTABLE_PROOF"}


def freeze_holdout(path: Path, dataset_hash: str, hypothesis: Hypothesis) -> None:
    """Exclusive write prevents adaptive re-opening after results are observed."""
    content = {"dataset_hash": dataset_hash, "hypothesis": hypothesis.identifier, "direction": hypothesis.direction, "threshold": str(hypothesis.threshold), "max_price": str(hypothesis.max_price), "policy": "holdout opened once; reruns reproduce this candidate only"}
    if path.exists():
        if json.loads(path.read_text(encoding="utf-8")) != content:
            raise ValueError("holdout already opened with a different dataset or hypothesis")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(content, handle, indent=2)


def content_hash(content: bytes) -> str:
    return sha256(content).hexdigest()


def walk_forward(rows: Sequence[HistoricalObservation], hypotheses: Sequence[Hypothesis], boundaries: Sequence[int], *, embargo_seconds: int = 86400) -> dict[str, Any]:
    """Expanding training; each following window is evaluated exactly once.

    Outcomes may select a candidate only after settlement plus embargo. Clusters
    represented in the upcoming window cannot enter training. This diagnostic
    uses development dates only and cannot replace the untouched final holdout.
    """
    if len(boundaries) < 2 or list(boundaries) != sorted(set(boundaries)) or not hypotheses or embargo_seconds < 0:
        raise ValueError("invalid walk-forward specification")
    folds: list[dict[str, Any]] = []
    for start, end in zip(boundaries, boundaries[1:]):
        test = [row for row in rows if start <= row.decision_at < end]
        test_clusters = {row.cluster_id for row in test}
        train = [row for row in rows if row.settlement_at + embargo_seconds < start and row.cluster_id not in test_clusters]
        candidates = [(h, evaluate(train, h)) for h in hypotheses]
        eligible = [(h, result) for h, result in candidates if result["trades"] >= 10]
        if not eligible or not test:
            folds.append({"start": start, "end": end, "train_events": len(train), "evaluation_events": len(test), "status": "INSUFFICIENT_DATA"})
            continue
        winner, training = max(eligible, key=lambda item: D(item[1]["return_on_turnover"] or "-999"))
        folds.append({"start": start, "end": end, "train_events": len(train), "evaluation_events": len(test), "status": "EVALUATED", "selected_using_past_labels_only": winner.identifier, "training_net_pnl": training["net_pnl"], "evaluation": evaluate(test, winner)})
    return {"method": "expanding past-only training; per-window independent simulated capital; development diagnostic only", "folds": folds, "sum_window_pnl": str(sum((D(f["evaluation"]["net_pnl"]) for f in folds if f["status"] == "EVALUATED"), D("0")))}
