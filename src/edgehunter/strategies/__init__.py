"""Pure strategy proposals: no credentials, order clients, or mutations."""

from .plugins import (
    Quote,
    StrategyContext,
    StrategyRegistry,
    StrategyResult,
    taker_fee,
)

__all__ = ["StrategyContext", "StrategyResult", "Quote", "StrategyRegistry", "taker_fee"]
