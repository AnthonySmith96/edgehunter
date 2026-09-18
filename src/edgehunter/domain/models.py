from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, InvalidOperation
from enum import StrEnum
from typing import Any


class ValidationError(ValueError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def require_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)


def decimal(value: Decimal | str | int, *, positive: bool = False) -> Decimal:
    if isinstance(value, (float, bool)):
        raise ValidationError("money/size must be Decimal, integer or decimal string; floats are forbidden")
    try:
        result = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError("invalid decimal") from exc
    if not result.is_finite() or result < 0 or result > Decimal("1000000000000"):
        raise ValidationError("decimal outside finite nonnegative domain")
    if positive and result <= 0:
        raise ValidationError("value must be positive")
    exponent = result.as_tuple().exponent
    if isinstance(exponent, int) and exponent < -12:
        raise ValidationError("more than 12 decimal places")
    return result


def money(value: Decimal | str | int) -> Decimal:
    """Conservative collateral debit rounded upward to six decimals."""
    result = Decimal(value)
    if not result.is_finite() or result < 0:
        raise ValidationError("invalid collateral amount")
    return decimal(result.quantize(Decimal("0.000001"), rounding=ROUND_CEILING))


def proceeds(value: Decimal) -> Decimal:
    """Credits round downward so sub-micro-unit dust never creates profit."""
    if not value.is_finite() or value < 0:
        raise ValidationError("invalid collateral credit")
    return decimal(value.quantize(Decimal("0.000001"), rounding=ROUND_FLOOR))


def canonical_json(value: Any) -> str:
    def encode(item: Any) -> str:
        if isinstance(item, Decimal):
            return format(item, "f")
        if isinstance(item, datetime):
            return require_utc(item).isoformat()
        raise TypeError(type(item).__name__)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=encode, allow_nan=False)


class Mode(StrEnum):
    REPLAY = "REPLAY"
    SHADOW = "SHADOW"
    PAPER = "PAPER"
    MICRO_LIVE = "MICRO_LIVE"
    LIVE = "LIVE"


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class OrderState(StrEnum):
    PREPARED = "PREPARED"
    SUBMITTING = "SUBMITTING"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    OPEN = "OPEN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCEL_PENDING = "CANCEL_PENDING"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class Level:
    price: Decimal
    quantity: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "price", decimal(self.price, positive=True))
        object.__setattr__(self, "quantity", decimal(self.quantity, positive=True))
        if self.price > 1:
            raise ValidationError("prediction share price must be <= 1")


@dataclass(frozen=True)
class Book:
    asset_id: str
    bids: tuple[Level, ...]
    asks: tuple[Level, ...]
    observed_at: datetime
    contract_hash: str
    metadata_version: str
    available_at: datetime | None = None
    valid: bool = True

    def __post_init__(self) -> None:
        require_utc(self.observed_at)
        if self.available_at is not None:
            require_utc(self.available_at)
        object.__setattr__(self, "bids", tuple(self.bids))
        object.__setattr__(self, "asks", tuple(self.asks))
        if any(a.price < b.price for a, b in zip(self.bids, self.bids[1:])):
            raise ValidationError("bids must be descending")
        if any(a.price > b.price for a, b in zip(self.asks, self.asks[1:])):
            raise ValidationError("asks must be ascending")
        if self.bids and self.asks and self.bids[0].price >= self.asks[0].price:
            raise ValidationError("crossed or locked book")
        if not self.asset_id or not self.contract_hash or not self.metadata_version:
            raise ValidationError("book identity is required")


@dataclass(frozen=True)
class OrderPlan:
    asset_id: str
    market_id: str
    strategy: str
    quantity: Decimal
    limit_price: Decimal
    created_at: datetime
    expires_at: datetime
    contract_hash: str
    metadata_version: str
    tick_size: Decimal = Decimal("0.01")
    fee_rate: Decimal = Decimal("0")
    fee_model: str = "flat_notional"
    cluster_id: str = "default"
    mode: Mode = Mode.REPLAY
    side: Side = Side.BUY
    strategy_version: str = "1"
    min_quantity: Decimal = Decimal("0.000001")
    collateral: str = "SIM_USD"
    wallet: str = "paper"
    venue: str = "paper"
    calibrated: bool = True
    reduce_only: bool = False
    research_only: bool = False

    def __post_init__(self) -> None:
        for name in ("quantity", "limit_price", "tick_size", "min_quantity"):
            object.__setattr__(self, name, decimal(getattr(self, name), positive=True))
        object.__setattr__(self, "fee_rate", decimal(self.fee_rate))
        object.__setattr__(self, "mode", Mode(self.mode))
        object.__setattr__(self, "side", Side(self.side))
        if any(not isinstance(getattr(self, name), bool) for name in ("calibrated", "reduce_only", "research_only")):
            raise ValidationError("calibration, reduction and research flags must be boolean")
        if self.limit_price > 1 or self.tick_size > 1 or self.fee_rate > 1:
            raise ValidationError("price/tick/fee outside bounds")
        if self.fee_model not in ("flat_notional", "polymarket_curve"):
            raise ValidationError("unknown fee model")
        if self.limit_price % self.tick_size != 0:
            raise ValidationError("price violates market tick")
        if self.quantity < self.min_quantity:
            raise ValidationError("quantity below minimum; never round upward")
        if require_utc(self.expires_at) <= require_utc(self.created_at):
            raise ValidationError("expiry must follow creation")
        if any(not getattr(self, name) for name in ("asset_id", "market_id", "strategy", "strategy_version",
                                                    "contract_hash", "metadata_version", "cluster_id", "collateral")):
            raise ValidationError("plan identity fields cannot be empty")

    @property
    def cost_cap(self) -> Decimal:
        # Extra collateral for per-fill fee rounding, never recognized as a cost
        # until charged. A fill exceeding the remaining reserve is rejected.
        # The curve fee is highest at 0.5; a better buy price can have a
        # higher fee than the limit price. Reserve the whole approved range.
        fee_price = min(self.limit_price, Decimal("0.5")) if self.fee_model == "polymarket_curve" else self.limit_price
        return money(self.quantity * self.limit_price) + self.fee_for(self.quantity, fee_price) + Decimal("0.000100")

    def fee_for(self, quantity: Decimal, price: Decimal) -> Decimal:
        factor = (1-price) if self.fee_model == "polymarket_curve" else Decimal(1)
        return money(quantity * self.fee_rate * price * factor)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def plan_hash(self) -> str:
        return hashlib.sha256(canonical_json(self.as_dict()).encode()).hexdigest()

    @classmethod
    def from_json(cls, payload: str) -> OrderPlan:
        data = json.loads(payload)
        for name in ("created_at", "expires_at"):
            data[name] = datetime.fromisoformat(data[name])
        return cls(**data)


@dataclass(frozen=True)
class Fill:
    trade_id: str
    intent_id: str
    quantity: Decimal
    price: Decimal
    fee: Decimal
    timestamp: datetime
    settlement_status: str = "SIMULATED_SETTLED"

    def __post_init__(self) -> None:
        for name in ("quantity", "price"):
            object.__setattr__(self, name, decimal(getattr(self, name), positive=True))
        object.__setattr__(self, "fee", decimal(self.fee))
        require_utc(self.timestamp)
        if self.price > 1 or not self.trade_id or not self.intent_id:
            raise ValidationError("invalid fill")
        if self.settlement_status != "SIMULATED_SETTLED":
            raise ValidationError("only settled simulated fills are supported")
