"""Offline, reproducible research; never promotes production strategies."""

from .evaluation import HistoricalObservation, Hypothesis, evaluate, temporal_split

__all__ = ["HistoricalObservation", "Hypothesis", "evaluate", "temporal_split"]
