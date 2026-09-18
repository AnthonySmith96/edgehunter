from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from edgehunter.domain import decimal, require_utc
from edgehunter.domain.models import proceeds
from edgehunter.storage.journal import Journal


class TreasuryAction(StrEnum):
    HOLD = "HOLD"
    REINVEST = "REINVEST"
    WITHDRAW = "WITHDRAW"
    ADD_EXTERNAL_CAPITAL = "ADD_EXTERNAL_CAPITAL"


@dataclass(frozen=True)
class TreasuryPolicy:
    authorized_capital: Decimal = Decimal("1000")
    cash_buffer: Decimal = Decimal("100")
    withdrawal_fraction: Decimal = Decimal("0.5")
    minimum_withdrawal: Decimal = Decimal("1")
    estimated_tax_reserve: Decimal | None = None
    estimated_transfer_fee: Decimal | None = None
    validity_seconds: int = 300

    def __post_init__(self) -> None:
        for name in ("authorized_capital", "minimum_withdrawal"):
            object.__setattr__(self, name, decimal(getattr(self, name), positive=True))
        for name in ("cash_buffer", "withdrawal_fraction"):
            object.__setattr__(self, name, decimal(getattr(self, name)))
        for name in ("estimated_tax_reserve", "estimated_transfer_fee"):
            if getattr(self, name) is not None:
                object.__setattr__(self, name, decimal(getattr(self, name)))
        if self.withdrawal_fraction > 1 or not 1 <= self.validity_seconds <= 86400:
            raise ValueError("invalid treasury policy")


@dataclass(frozen=True)
class TreasuryRecommendation:
    action: TreasuryAction
    amount: Decimal
    collateral: str
    as_of: datetime
    expires_at: datetime
    settled_free_cash: Decimal
    reserved_collateral: Decimal
    inventory_cost: Decimal
    contributed_capital: Decimal
    net_business_pnl: Decimal
    estimated_tax_reserve: Decimal | None
    estimated_transfer_fee: Decimal | None
    reasons: tuple[str, ...]
    simulated: bool = True
    execution_allowed: bool = False


def recommend(journal: Journal, policy: TreasuryPolicy | None = None, *, now: datetime,
              validated_capacity: Decimal = Decimal(0), evidence_validated: bool = False) -> TreasuryRecommendation:
    """Return an advisory snapshot. Never move money, reserve profits or lift limits.

    Caller must explicitly supply evidence and a capacity already allowed by its
    strategy mandate. Synthetic backtest winnings alone do not meet this gate.
    Unknown taxes/transfer costs remain unknown, never silently become zero.
    """
    policy = policy or TreasuryPolicy()
    now = require_utc(now)
    capacity = decimal(validated_capacity)
    if not isinstance(evidence_validated, bool):
        raise ValueError("evidence_validated must be boolean")
    snapshot = journal.report()
    cash, reserved, inventory = (Decimal(snapshot[name]) for name in ("cash", "reserved", "inventory_cost"))
    pnl = Decimal(snapshot["net_business_pnl"])
    action, amount = TreasuryAction.HOLD, Decimal(0)
    reasons: tuple[str, ...]
    if not snapshot["ledger_valid"]:
        reasons = ("LEDGER_INVALID",)
    elif snapshot["control"]["halted"] == "true" or snapshot["control"]["paused"] == "true":
        reasons = ("PERSISTED_STOP",)
    elif reserved > 0 or inventory > 0:
        reasons = ("CAPITAL_COMMITTED_RECONCILE_FIRST",)
    elif pnl < 0:
        reasons = ("LOSSES_REQUIRE_REVIEW_NO_RECAPITALIZATION",)
    elif policy.estimated_tax_reserve is None or policy.estimated_transfer_fee is None:
        reasons = ("TAX_OR_TRANSFER_COST_UNKNOWN",)
    else:
        free = max(Decimal(0), cash-policy.cash_buffer-policy.estimated_tax_reserve-policy.estimated_transfer_fee)
        profit = max(Decimal(0), pnl-policy.estimated_tax_reserve-policy.estimated_transfer_fee)
        withdrawal = proceeds(min(free, profit*policy.withdrawal_fraction))
        if withdrawal >= policy.minimum_withdrawal:
            action, amount = TreasuryAction.WITHDRAW, withdrawal
            reasons = ("POSITIVE_REALIZED_BUSINESS_RESULT", "MANUAL_SIMULATED_RECOMMENDATION_ONLY")
        elif evidence_validated and capacity > 0:
            amount = proceeds(min(free, capacity, policy.authorized_capital))
            if amount > 0:
                action = TreasuryAction.REINVEST
                reasons = ("EXPLICIT_VALIDATED_CAPACITY_WITHIN_AUTHORIZED_CAPITAL",)
            else:
                reasons = ("NO_DEPLOYABLE_CASH",)
        else:
            reasons = ("NO_VALIDATED_CAPACITY_OR_WITHDRAWABLE_PROFIT",)
    return TreasuryRecommendation(action, amount, snapshot["collateral"], now,
        now+timedelta(seconds=policy.validity_seconds), cash, reserved, inventory,
        Decimal(snapshot["deposits"]), pnl, policy.estimated_tax_reserve,
        policy.estimated_transfer_fee, reasons)
