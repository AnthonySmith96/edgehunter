from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .evaluation import HistoricalObservation

D = Decimal


@dataclass(frozen=True)
class ExternalFavoriteTail:
    """Fixed replication of the externally published Polymarket tail rule."""

    threshold: Decimal = D("0.90")
    max_price: Decimal = D("0.98")
    categories: frozenset[str] = frozenset({"crypto", "politics"})
    identifier: str = "external_flb_favorite90_crypto_politics"

    def __post_init__(self) -> None:
        if not D("0.5") < self.threshold <= self.max_price < 1:
            raise ValueError("invalid favorite tail bounds")
        if not self.categories or "sports" in self.categories:
            raise ValueError("external replication categories changed")

    def side(self, row: HistoricalObservation) -> tuple[str, Decimal, int] | None:
        if row.category not in self.categories:
            return None
        yes = row.yes_price >= row.no_price
        price, outcome = (row.yes_price, row.outcome_yes) if yes else (row.no_price, 1-row.outcome_yes)
        if not self.threshold <= price <= self.max_price:
            return None
        return ("YES" if yes else "NO", price, outcome)
