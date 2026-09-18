"""Regularized market-residual calibration fitted only to mature past labels."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal as D
from typing import Any, Sequence

from .evaluation import HistoricalObservation


@dataclass(frozen=True)
class CalibrationSpec:
    prior_strength: int
    uncertainty_z: D
    use_category: bool
    min_net_edge: D = D("0.0025")

    @property
    def identifier(self) -> str:
        return f"residual_prior{self.prior_strength}_z{self.uncertainty_z}_category{int(self.use_category)}_edge{self.min_net_edge}"


@dataclass(frozen=True)
class CalibratedFavorite:
    spec: CalibrationSpec
    label_cutoff: int
    # global/category bucket -> (sample size, sum of forecast residuals)
    statistics: dict[str, tuple[int, D]]
    trained_events: int

    @property
    def identifier(self) -> str:
        return self.spec.identifier

    @classmethod
    def fit(cls, rows: Sequence[HistoricalObservation], spec: CalibrationSpec, cutoff: int) -> CalibratedFavorite:
        if spec.prior_strength <= 0 or not spec.uncertainty_z.is_finite() or not spec.min_net_edge.is_finite() or spec.uncertainty_z < 0 or spec.min_net_edge < 0:
            raise ValueError("invalid calibration regularization")
        if any(row.settlement_at >= cutoff for row in rows):
            raise ValueError("calibration cannot access future labels")
        if len({row.event_id for row in rows}) != len(rows):
            raise ValueError("duplicate training event")
        stats: dict[str, tuple[int, D]] = {}
        for row in rows:
            yes = row.yes_price >= row.no_price
            price, outcome = (row.yes_price, row.outcome_yes) if yes else (row.no_price, 1 - row.outcome_yes)
            bucket = min(9, int(price * 10))
            for key in (f"global:{bucket}", f"{row.category}:{bucket}"):
                count, residual = stats.get(key, (0, D(0)))
                stats[key] = count + 1, residual + outcome - price
        return cls(spec, cutoff, stats, len(rows))

    def _estimate_favorite(self, price: D, category: str) -> tuple[D, D, D]:
        bucket = min(9, int(price * 10))
        count, residual = self.statistics.get(f"global:{bucket}", (0, D(0)))
        prior = D(self.spec.prior_strength)
        correction = residual / (count + prior)
        if self.spec.use_category:
            local_count, local_residual = self.statistics.get(f"{category}:{bucket}", (0, D(0)))
            if local_count:
                correction = (local_residual + prior * correction) / (local_count + prior)
                count = local_count
        probability = min(D("0.999"), max(D("0.001"), price + correction))
        # Heuristic uncertainty penalty, explicitly not a calibrated confidence interval.
        uncertainty = (probability * (1 - probability) / (count + prior)).sqrt()
        lower = max(D(0), probability - self.spec.uncertainty_z * uncertainty)
        upper = min(D(1), probability + self.spec.uncertainty_z * uncertainty)
        return probability, lower, upper

    def estimate_yes(self, market_probability: D, category: str, issued_at: int) -> tuple[D, D, D]:
        """Return YES mean/lower/upper; bounds are heuristic, never validated CIs.

        Input is a current market-implied YES probability, not an executable ask.
        Caller must check quote freshness, fees, contracts and paper-only policy.
        """
        if issued_at <= self.label_cutoff:
            raise ValueError("forecast issued before calibrator cutoff")
        if not market_probability.is_finite() or not 0 < market_probability < 1:
            raise ValueError("invalid market probability")
        yes = market_probability >= D("0.5")
        price = market_probability if yes else 1 - market_probability
        probability, lower, upper = self._estimate_favorite(price, category)
        return (probability, lower, upper) if yes else (1 - probability, 1 - upper, 1 - lower)

    def forecast(self, row: HistoricalObservation) -> tuple[bool, D, D, D]:
        if row.decision_at <= self.label_cutoff:
            raise ValueError("forecast issued before calibrator cutoff")
        yes = row.yes_price >= row.no_price
        price = row.yes_price if yes else row.no_price
        probability, lower, _ = self._estimate_favorite(price, row.category)
        return yes, price, probability, lower

    def side(self, row: HistoricalObservation) -> tuple[str, D, int] | None:
        yes, observed, _, lower = self.forecast(row)
        price = observed + D("0.01")
        unit_cost = price + D("0.07") * price * (1 - price)
        if observed > D("0.98") or price >= 1 or lower - unit_cost < self.spec.min_net_edge:
            return None
        return ("YES" if yes else "NO", observed, row.outcome_yes if yes else 1 - row.outcome_yes)

    def snapshot(self) -> dict[str, Any]:
        return {"identifier": self.identifier, "label_cutoff": self.label_cutoff, "trained_events": self.trained_events, "prior_strength": self.spec.prior_strength, "uncertainty_z": str(self.spec.uncertainty_z), "use_category": self.spec.use_category, "minimum_net_edge": str(self.spec.min_net_edge), "statistics": {key: [n, str(residual)] for key, (n, residual) in sorted(self.statistics.items())}, "uncertainty_is_validated_interval": False}

    @classmethod
    def from_snapshot(cls, value: dict[str, Any]) -> CalibratedFavorite:
        spec = CalibrationSpec(int(value["prior_strength"]), D(value["uncertainty_z"]), bool(value["use_category"]), D(value["minimum_net_edge"]))
        # Reuse fit validation even for serialized input.
        cls.fit([], spec, int(value["label_cutoff"]))
        statistics = {str(key): (int(item[0]), D(item[1])) for key, item in value["statistics"].items()}
        if any(n < 0 or not residual.is_finite() or abs(residual) > n for n, residual in statistics.values()):
            raise ValueError("invalid calibration statistics")
        if value.get("identifier") != spec.identifier or int(value["trained_events"]) < 0:
            raise ValueError("invalid calibration snapshot")
        return cls(spec, int(value["label_cutoff"]), statistics, int(value["trained_events"]))


def market_category(tags: Sequence[str]) -> str:
    normalized = {tag.lower() for tag in tags}
    for category, keys in (("sports", {"sports", "nba", "nfl", "mlb", "soccer", "tennis"}), ("economics", {"economy", "economics", "finance", "interest-rates", "global-rates"}), ("politics", {"politics", "elections", "geopolitics"}), ("crypto", {"crypto", "bitcoin", "ethereum"}), ("weather", {"weather", "climate"})):
        if normalized & keys:
            return category
    return "other"
