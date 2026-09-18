from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# This manifest describes fixed, tested bounds. Increasing them requires a code
# review and fresh evidence, rather than silently widening a running mandate.
PAPER_POLICY: dict[str, Any] = {
    "schema_version": 1, "mode": "PAPER", "paper_trading_enabled": True,
    "live_trading_enabled": False, "live_capability": False, "currency": "SIM_USD",
    "initial_cash": "1000.00", "max_loss_per_idea": "10.00",
    "max_loss_per_cluster": "30.00", "max_total_exposure": "100.00",
    "cumulative_realized_loss_limit": "20.00", "drawdown_limit": "50.00",
    "incremental_provider_budget": "0.00", "book_ttl_seconds": 8,
    "poll_seconds": 30, "market_limit": 12, "control_lease_seconds": 60,
    "timezone": "America/Mexico_City",
}


def load_policy(root: Path) -> dict[str, Any]:
    path = root / "config" / "paper.json"
    value = json.loads(path.read_text(encoding="utf-8")) if path.exists() else dict(PAPER_POLICY)
    expected = {**PAPER_POLICY, "paper_trading_enabled": value.get("paper_trading_enabled")}
    if type(value.get("paper_trading_enabled")) is not bool or value != expected:
        raise ValueError("Paper policy differs from tested fixed bounds; only paper_trading_enabled is editable")
    return dict(value)
