"""Frozen learned-model experiment. Public observations and virtual cash only.

Execution on current asks is a new prospective experiment: historical sampled
prices do not establish the executable return of this strategy.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import random
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import ROUND_FLOOR, Decimal
from pathlib import Path
from typing import Any

from edgehunter.ingestion.http import PublicHTTP, SourceError
from edgehunter.ops.lock import InstanceLock
from edgehunter.ops.paper_candidate import resolution_payout
from edgehunter.research.btc_intraday import IntradayObservation
from edgehunter.research.btc_lag import realized_vol_per_second
from edgehunter.research.learned_intraday import LearnedSpec, LogisticSnapshot, predict_up
from edgehunter.venues.polymarket.public import decode_list

D = Decimal
EXPERIMENT = "btc_5m_frozen_learned_v5"
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
COINBASE = "https://api.exchange.coinbase.com"
POLICY: dict[str, Any] = {
    "initial_capital_sim_usd": "100", "maximum_stake_sim_usd": "10",
    "fee_curve_sensitivity": "0.10", "slippage_per_share": "0.03",
    "latency_ms": 450, "decision_tolerance_seconds": 10,
    "prospective_decision_horizon_seconds": 160,
    "historical_nominal_horizon_seconds": 60,
    "required_effective_horizon_seconds": 180,
    "maximum_book_age_seconds": 8, "maximum_ticker_age_seconds": 15,
    "maximum_pair_skew_seconds": 2, "maximum_spread": "0.05",
    "maximum_future_clock_skew_seconds": 1, "close_buffer_seconds": 10,
    "visible_depth_fraction": "0.8", "volatility_minutes": 120,
    "features": "MINUTE_OPEN_AT_OLDER_BOOK_TIME_WITH_120_PRIOR_CONTIGUOUS_CLOSES",
    "prediction_price": "CURRENT_MIDPOINT", "settlement": "OFFICIAL_TERMINAL_METADATA",
    "candle_query": "EXPLICIT_WINDOW_END_AT_REQUEST_TIME_AFTER_TWENTY_SECOND_PUBLICATION_BUFFER",
}


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def utc_iso(epoch: float | None = None) -> str:
    return datetime.fromtimestamp(time.time() if epoch is None else epoch, UTC).isoformat()


def finite(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("BOOLEAN_NUMERIC_INPUT")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("NONFINITE_INPUT")
    return result


def positive_decimal(value: Any) -> Decimal:
    result = D(str(value))
    if not result.is_finite() or result <= 0:
        raise ValueError("INVALID_POSITIVE_AMOUNT")
    return result


def frozen_model(document: dict[str, Any]) -> tuple[LearnedSpec, LogisticSnapshot]:
    spec = LearnedSpec(**document["selected_spec"])
    raw = document["selected_model"]
    model = LogisticSnapshot(**{
        **raw, **{key: tuple(finite(v) for v in raw[key])
                  for key in ("means", "scales", "coefficients")},
    })
    if (spec.horizon_seconds != 60 or model.horizon_seconds != spec.horizon_seconds
            or model.ridge != spec.ridge or not math.isfinite(spec.ridge)
            or len(model.means) != 4 or len(model.scales) != 4
            or len(model.coefficients) != 5 or any(v <= 0 for v in model.scales)
            or model.training_rows < 20 or model.training_end_epoch <= 0):
        raise ValueError("INVALID_FROZEN_MODEL")
    return spec, model


def register(source: Path, manifest: Path) -> dict[str, Any]:
    """Exclusive registration; never mutate an experiment with existing identity."""
    source = source.resolve()
    raw = source.read_bytes()
    report = json.loads(raw)
    spec, model = frozen_model(report)
    manifest_root = manifest.resolve().parent.parent
    try:
        source_reference = source.relative_to(manifest_root).as_posix()
    except ValueError:
        source_reference = str(source)
    document = {
        "experiment": EXPERIMENT, "mode": "PAPER_ONLY_PUBLIC_GET",
        "registered_at": utc_iso(), "source_report": source_reference,
        "source_report_sha256": hashlib.sha256(raw).hexdigest(),
        "implementation_sha256": implementation_hash(),
        "source_status": report.get("status"),
        "selected_spec": asdict(spec), "selected_model": asdict(model),
        "policy": POLICY,
        "classification": "NEW_PROSPECTIVE_BOOK_EXECUTION_NOT_HISTORICAL_REPLICATION",
    }
    manifest.parent.mkdir(parents=True, exist_ok=True)
    with manifest.open("xb") as handle:
        handle.write(canonical(document) + b"\n")
        handle.flush()
        os.fsync(handle.fileno())
    return document


def load_manifest(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    document = json.loads(raw)
    if (document.get("experiment") != EXPERIMENT
            or document.get("mode") != "PAPER_ONLY_PUBLIC_GET"
            or document.get("policy") != POLICY):
        raise ValueError("FROZEN_POLICY_MISMATCH")
    if document.get("implementation_sha256") != implementation_hash():
        raise ValueError("FROZEN_IMPLEMENTATION_CHANGED")
    frozen_model(document)
    source_path = Path(document["source_report"])
    if not source_path.is_absolute():
        source_path = path.resolve().parent.parent / source_path
    source_bytes = source_path.read_bytes()
    if hashlib.sha256(source_bytes).hexdigest() != document["source_report_sha256"]:
        raise ValueError("SOURCE_REPORT_CHANGED")
    original = json.loads(source_bytes)
    if any(original[key] != document[key] for key in ("selected_spec", "selected_model")):
        raise ValueError("FROZEN_MODEL_CHANGED")
    return document, hashlib.sha256(raw).hexdigest()


def implementation_hash() -> str:
    root = Path(__file__).parent
    files = ("btc_challenger.py", "learned_intraday.py", "btc_lag.py", "btc_intraday.py")
    return hashlib.sha256(canonical({name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                                     for name in files})).hexdigest()


def balances(events: list[dict[str, Any]]) -> dict[str, Decimal]:
    fills = {row["slug"]: row for row in events if row["type"] == "PAPER_FILL"}
    settled = {row["slug"]: row for row in events if row["type"] == "SETTLEMENT"}
    pnl = sum((D(row["pnl_sim_usd"]) for row in settled.values()), D(0))
    reserved = sum((D(row["total_cost_sim_usd"]) for slug, row in fills.items()
                    if slug not in settled), D(0))
    equity = D(POLICY["initial_capital_sim_usd"]) + pnl
    return {"realized_pnl": pnl, "reserved": reserved, "equity": equity, "cash": equity - reserved}


class ExperimentJournal:
    """Single-writer fsync journal with hash/config binding and torn-tail recovery.

    Use only inside the context manager, which owns the operating-system lock.
    A crash after an OPPORTUNITY records a missed opportunity, never a retry.
    """

    def __init__(self, path: Path, config_hash: str) -> None:
        self.path, self.config_hash = path, config_hash
        self.events: list[dict[str, Any]] = []
        self.lock = InstanceLock(path.with_suffix(".lock"))
        self.active = False

    def __enter__(self) -> ExperimentJournal:
        self.lock.__enter__()
        self.active = True
        try:
            self._recover()
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *args: Any) -> None:
        self.active = False
        self.lock.__exit__(*args)

    def _recover(self) -> None:
        self.events = []
        if not self.path.exists():
            return
        raw = self.path.read_bytes()
        # Even a valid JSON object without its newline may be an interrupted append.
        end = raw.rfind(b"\n") + 1
        if end != len(raw):
            quarantine = self.path.with_name(self.path.name + f".torn-{time.time_ns()}")
            with quarantine.open("xb") as backup:
                backup.write(raw[end:])
                backup.flush()
                os.fsync(backup.fileno())
            with self.path.open("r+b") as handle:
                handle.truncate(end)
                handle.flush()
                os.fsync(handle.fileno())
        for line in raw[:end].splitlines():
            event = json.loads(line)
            stored_id = event.pop("event_id")
            if (event.get("config_sha256") != self.config_hash
                    or event.get("experiment") != EXPERIMENT
                    or event.get("sequence") != len(self.events)
                    or event.get("previous_event_id") != self._previous()
                    or hashlib.sha256(canonical(event)).hexdigest() != stored_id):
                raise ValueError("JOURNAL_CONFIG_OR_INTEGRITY_MISMATCH")
            self._validate(event)
            self.events.append({**event, "event_id": stored_id})

    def _previous(self) -> str | None:
        return str(self.events[-1]["event_id"]) if self.events else None

    def attempted(self, slug: str) -> bool:
        return any(row["type"] == "OPPORTUNITY" and row["slug"] == slug for row in self.events)

    def _validate(self, event: dict[str, Any]) -> None:
        kind, slug = event["type"], event.get("slug")
        same = [row for row in self.events if row.get("slug") == slug]
        kinds = {row["type"] for row in same}
        if kind == "OPPORTUNITY":
            if "OPPORTUNITY" in kinds:
                raise ValueError("MARKET_ALREADY_ATTEMPTED")
        elif kind in ("PREDICTION", "REJECTION", "NO_FILL", "PAPER_FILL", "OUTCOME", "SETTLEMENT"):
            if "OPPORTUNITY" not in kinds or kind in kinds:
                raise ValueError("INVALID_OR_DUPLICATE_EVENT")
        elif kind != "ERROR":
            raise ValueError("UNKNOWN_EVENT_TYPE")
        if kind in ("PAPER_FILL", "NO_FILL", "OUTCOME") and "PREDICTION" not in kinds:
            raise ValueError("PREDICTION_REQUIRED")
        if kind == "PAPER_FILL":
            if "NO_FILL" in kinds or "REJECTION" in kinds:
                raise ValueError("TERMINAL_OPPORTUNITY")
            cost = positive_decimal(event["total_cost_sim_usd"])
            positive_decimal(event["quantity"])
            if cost > min(D(POLICY["maximum_stake_sim_usd"]), balances(self.events)["cash"]):
                raise ValueError("INSUFFICIENT_CASH_OR_STAKE_LIMIT")
        if kind == "PREDICTION":
            if not 0 < finite(event["probability_up"]) < 1 or "REJECTION" in kinds:
                raise ValueError("INVALID_PREDICTION")
        if kind == "SETTLEMENT":
            fills = [row for row in same if row["type"] == "PAPER_FILL"]
            if len(fills) != 1:
                raise ValueError("FILL_REQUIRED_FOR_SETTLEMENT")
            payout = D(str(event["payout_per_share"]))
            expected = D(fills[0]["quantity"]) * payout - D(fills[0]["total_cost_sim_usd"])
            if payout not in (D(0), D("0.5"), D(1)) or D(event["pnl_sim_usd"]) != expected:
                raise ValueError("INVALID_SETTLEMENT_ACCOUNTING")

    def append(self, event: dict[str, Any]) -> dict[str, Any]:
        if not self.active:
            raise RuntimeError("JOURNAL_LOCK_REQUIRED")
        self._validate(event)
        document = {**event, "recorded_at": utc_iso(), "experiment": EXPERIMENT,
                    "config_sha256": self.config_hash, "sequence": len(self.events),
                    "previous_event_id": self._previous()}
        document["event_id"] = hashlib.sha256(canonical(document)).hexdigest()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("ab") as handle:
            handle.write(canonical(document) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        self.events.append(document)
        return document


@dataclass(frozen=True)
class CheckedBook:
    token: str
    timestamp: float
    ask: Decimal
    size: Decimal
    minimum: Decimal
    midpoint: Decimal


def checked_book(payload: Any, token: str, now: float) -> CheckedBook:
    if not isinstance(payload, dict) or str(payload.get("asset_id")) != token:
        raise ValueError("BOOK_TOKEN_MISMATCH")
    stamp = finite(payload["timestamp"]) / 1000
    age = now - stamp
    if not -POLICY["maximum_future_clock_skew_seconds"] <= age <= POLICY["maximum_book_age_seconds"]:
        raise ValueError("STALE_OR_FUTURE_BOOK")
    levels = [(positive_decimal(row["price"]), positive_decimal(row["size"]))
              for row in payload["asks"]]
    if not levels or any(price >= 1 for price, _ in levels):
        raise ValueError("EMPTY_OR_INVALID_ASK")
    ask, size = min(levels)
    bids = [(positive_decimal(row["price"]), positive_decimal(row["size"]))
            for row in payload["bids"]]
    if not bids or any(price >= 1 for price, _ in bids):
        raise ValueError("EMPTY_OR_INVALID_BID")
    bid = max(price for price, _ in bids)
    if bid > ask or ask - bid > D(POLICY["maximum_spread"]):
        raise ValueError("CROSSED_OR_WIDE_BOOK")
    minimum = positive_decimal(payload["min_order_size"])
    return CheckedBook(token, stamp, ask, size, minimum, (ask + bid) / 2)


class MissingCandlesError(ValueError):
    def __init__(self, *, missing: list[int], latest: int | None, cutoff: int) -> None:
        super().__init__("MISSING_CONTIGUOUS_120_CANDLES")
        self.details = {"missing_candle_epochs": missing, "latest_candle_epoch": latest,
                        "feature_cutoff_epoch": cutoff}


def observation_from_public(
    *, epoch: int, books: tuple[CheckedBook, CheckedBook], candles: Any,
    ticker: Any, now: float,
) -> IntradayObservation:
    """Match historical candle construction without any unfinished candle close."""
    stamp = datetime.fromisoformat(str(ticker["time"]).replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("NAIVE_TICKER_TIME")
    age = now - stamp.timestamp()
    if not -POLICY["maximum_future_clock_skew_seconds"] <= age <= POLICY["maximum_ticker_age_seconds"]:
        raise ValueError("STALE_OR_FUTURE_TICKER")
    if finite(ticker["price"]) <= 0:
        raise ValueError("INVALID_TICKER_PRICE")
    cutoff = int(min(book.timestamp for book in books)) // 60 * 60
    if not epoch <= cutoff <= now or not 0 < epoch + 300 - cutoff <= 300:
        raise ValueError("OUTDATED_FEATURE_TIME")
    if abs(books[0].timestamp - books[1].timestamp) > POLICY["maximum_pair_skew_seconds"]:
        raise ValueError("UNSYNCHRONIZED_BOOKS")
    if epoch + 300 - cutoff != POLICY["required_effective_horizon_seconds"]:
        raise ValueError("FEATURE_HORIZON_MISMATCH")
    if not isinstance(candles, list):
        raise ValueError("INVALID_CANDLES")
    by_epoch: dict[int, list[float]] = {}
    for candle in candles:
        if not isinstance(candle, list) or len(candle) < 6:
            raise ValueError("INVALID_CANDLE")
        values = [finite(v) for v in candle[:6]]
        moment = int(values[0])
        if moment != values[0] or moment % 60 or moment in by_epoch:
            raise ValueError("INVALID_OR_DUPLICATE_CANDLE_TIME")
        if (any(v <= 0 for v in values[1:5]) or values[5] < 0
                or values[1] > min(values[3:5]) or values[2] < max(values[3:5])):
            raise ValueError("INVALID_CANDLE_VALUES")
        by_epoch[moment] = values
    required = list(range(cutoff - 7200, cutoff, 60))
    missing = sorted(t for t in {epoch, cutoff, *required} if t not in by_epoch)
    if missing:
        raise MissingCandlesError(missing=missing, latest=max(by_epoch, default=None), cutoff=cutoff)
    return IntradayObservation(
        slug=f"btc-updown-5m-{epoch}", epoch=epoch, horizon_seconds=60,
        open_price=by_epoch[epoch][3], spot_price=by_epoch[cutoff][3],
        vol_per_second=realized_vol_per_second(by_epoch[t][4] for t in required),
        up_price=float(books[0].midpoint), down_price=float(books[1].midpoint), outcome_up=0,
        up_price_timestamp=int(books[0].timestamp), down_price_timestamp=int(books[1].timestamp),
        effective_horizon_seconds=epoch + 300 - cutoff,
    )


def candidate(probability_up: float, books: tuple[CheckedBook, CheckedBook],
              spec: LearnedSpec) -> tuple[int, Decimal, Decimal] | None:
    if not 0 < finite(probability_up) < 1:
        raise ValueError("INVALID_PROBABILITY")
    candidates: list[tuple[Decimal, int, Decimal]] = []
    for index, book in enumerate(books):
        # Same conservative 3-cent slippage and fee sensitivity as development.
        entry = book.ask + D(POLICY["slippage_per_share"])
        if entry > D(str(spec.max_ask)) or entry >= 1:
            continue
        unit = entry + D(POLICY["fee_curve_sensitivity"]) * entry * (1 - entry)
        probability = D(str(probability_up if index == 0 else 1 - probability_up))
        edge = probability - unit
        if edge >= D(str(spec.min_edge)):
            candidates.append((edge, index, entry))
    if not candidates:
        return None
    edge, index, entry = max(candidates)
    return index, entry, edge


def recheck_fill(
    *, book: CheckedBook, probability: float, limit: Decimal, spec: LearnedSpec,
    available_cash: Decimal, close_epoch: int, now: float,
) -> tuple[dict[str, Any] | None, str]:
    if close_epoch - now <= POLICY["close_buffer_seconds"]:
        return None, "TOO_CLOSE_AFTER_LATENCY"
    if not -POLICY["maximum_future_clock_skew_seconds"] <= now - book.timestamp <= POLICY["maximum_book_age_seconds"]:
        return None, "STALE_OR_FUTURE_BOOK_AFTER_LATENCY"
    entry = book.ask + D(POLICY["slippage_per_share"])
    if entry > limit or entry > D(str(spec.max_ask)) or entry >= 1:
        return None, "PRICE_MOVED_AFTER_LATENCY"
    fee = D(POLICY["fee_curve_sensitivity"]) * entry * (1 - entry)
    edge = D(str(finite(probability))) - entry - fee
    if edge < D(str(spec.min_edge)):
        return None, "EDGE_LOST_AFTER_LATENCY"
    # Do not shrink the registered $10 bet after depletion and change the experiment.
    stake = D(POLICY["maximum_stake_sim_usd"])
    if not available_cash.is_finite() or available_cash < stake:
        return None, "INSUFFICIENT_AVAILABLE_CASH"
    quantity = min(stake / (entry + fee), book.size * D(POLICY["visible_depth_fraction"]))
    quantity = quantity.quantize(D("0.000001"), rounding=ROUND_FLOOR)
    if quantity < book.minimum:
        return None, "INSUFFICIENT_DEPTH_OR_MINIMUM"
    return {
        "quantity": str(quantity), "fill_price": str(entry), "observed_ask": str(book.ask),
        "fee_sim_usd": str(quantity * fee), "total_cost_sim_usd": str(quantity * (entry + fee)),
        "modeled_edge_per_share": str(edge), "post_latency_book_timestamp": book.timestamp,
        "execution_model": "TOP_ASK_PLUS_0.03_AND_80_PERCENT_VISIBLE_DEPTH",
    }, "FILLED"


async def fetch_inputs(http: PublicHTTP, tokens: list[str]) -> tuple[Any, Any, Any, Any]:
    now = time.time()
    # Query after the registered twenty-second publication buffer, with an exact end
    # at request time. A fixed future end can cache the incomplete boundary response.
    # Only the current candle's open and strictly earlier closes become features.
    candle_params: dict[str, str | int] = {
        "granularity": 60, "start": utc_iso(int(now) // 60 * 60 - 7260), "end": utc_iso(now),
    }
    result = await asyncio.gather(
        *(http.json(f"{CLOB}/book", {"token_id": token}) for token in tokens),
        http.json(f"{COINBASE}/products/BTC-USD/candles", candle_params),
        http.json(f"{COINBASE}/products/BTC-USD/ticker"),
    )
    return result[0], result[1], result[2], result[3]


async def observe_once(http: PublicHTTP, manifest: dict[str, Any], journal: ExperimentJournal) -> None:
    now = time.time()
    epoch = int(now) // 300 * 300
    slug = f"btc-updown-5m-{epoch}"
    remaining = epoch + 300 - now
    horizon = POLICY["prospective_decision_horizon_seconds"]
    if journal.attempted(slug) or remaining > horizon:
        return
    # Claim before any request: failures/restarts cannot cherry-pick a later signal.
    journal.append({"type": "OPPORTUNITY", "slug": slug, "epoch": epoch,
                    "scheduled_decision_epoch": epoch + 300 - horizon, "observed_at": utc_iso(now)})
    if remaining < horizon - POLICY["decision_tolerance_seconds"]:
        journal.append({"type": "REJECTION", "slug": slug, "reason": "MISSED_DECISION_WINDOW"})
        return
    spec, model = frozen_model(manifest)
    try:
        event = await http.json(f"{GAMMA}/events/slug/{slug}")
        if not isinstance(event, dict) or len(event.get("markets", [])) != 1:
            raise ValueError("INVALID_BTC_EVENT")
        market = event["markets"][0]
        end = datetime.fromisoformat(str(market["endDate"]).replace("Z", "+00:00"))
        tokens = [str(v) for v in decode_list(market["clobTokenIds"])]
        if (market.get("slug") != slug or end.timestamp() != epoch + 300
                or market.get("acceptingOrders") is not True
                or market.get("closed") is True or market.get("negRisk") is True
                or not market.get("resolutionSource")
                or decode_list(market["outcomes"]) != ["Up", "Down"] or len(tokens) != 2):
            raise ValueError("BTC_MARKET_NOT_TRADABLE_OR_MISMATCH")
        up_raw, down_raw, candles, ticker = await fetch_inputs(http, tokens)
        now = time.time()
        if not horizon - POLICY["decision_tolerance_seconds"] <= epoch + 300 - now <= horizon:
            raise ValueError("INPUTS_MISSED_DECISION_WINDOW")
        books = (checked_book(up_raw, tokens[0], now), checked_book(down_raw, tokens[1], now))
        row = observation_from_public(epoch=epoch, books=books, candles=candles, ticker=ticker, now=now)
        probability = predict_up(model, row)
        chosen = candidate(probability, books, spec)
        journal.append({
            "type": "PREDICTION", "slug": slug, "market_id": str(market["id"]),
            "end_epoch": epoch + 300, "up_asset_id": tokens[0], "tokens": tokens,
            "probability_up": probability,
            "features": {key: value for key, value in asdict(row).items() if key != "outcome_up"},
            "ticker_time": ticker["time"], "candidate_side": chosen[0] if chosen else None,
            "decision_at": utc_iso(now),
        })
        if chosen is None:
            journal.append({"type": "NO_FILL", "slug": slug, "reason": "NO_COSTED_EDGE"})
            return
        index, limit, _ = chosen
        await asyncio.sleep(POLICY["latency_ms"] / 1000)
        # Re-fetch both sides and BTC features; price movements can change the model edge.
        up_raw, down_raw, candles, ticker = await fetch_inputs(http, tokens)
        now = time.time()
        post_books = (checked_book(up_raw, tokens[0], now), checked_book(down_raw, tokens[1], now))
        post_row = observation_from_public(
            epoch=epoch, books=post_books, candles=candles, ticker=ticker, now=now,
        )
        post_probability = predict_up(model, post_row)
        fill, reason = recheck_fill(
            book=post_books[index], probability=post_probability if index == 0 else 1 - post_probability,
            limit=limit, spec=spec, available_cash=balances(journal.events)["cash"],
            close_epoch=epoch + 300, now=now,
        )
        if fill is None:
            journal.append({"type": "NO_FILL", "slug": slug, "reason": reason})
            return
        journal.append({
            "type": "PAPER_FILL", "slug": slug, "market_id": str(market["id"]),
            "side": "UP" if index == 0 else "DOWN", "asset_id": tokens[index],
            "end_epoch": epoch + 300, "filled_at": utc_iso(now),
            "post_latency_probability_up": post_probability,
            "post_latency_features": {key: value for key, value in asdict(post_row).items() if key != "outcome_up"},
            "latency_ms": POLICY["latency_ms"], **fill,
        })
    except (SourceError, KeyError, TypeError, ValueError, ArithmeticError) as exc:
        predicted = any(r["type"] == "PREDICTION" and r["slug"] == slug for r in journal.events)
        journal.append({"type": "NO_FILL" if predicted else "REJECTION", "slug": slug,
                        "reason": str(exc), "error_class": type(exc).__name__,
                        **({"source_quality": exc.details} if isinstance(exc, MissingCandlesError) else {})})


async def settle_pending(http: PublicHTTP, journal: ExperimentJournal) -> None:
    outcomes = {r["slug"] for r in journal.events if r["type"] == "OUTCOME"}
    settled = {r["slug"] for r in journal.events if r["type"] == "SETTLEMENT"}
    fills = {r["slug"]: r for r in journal.events if r["type"] == "PAPER_FILL"}
    predictions = [r for r in journal.events if r["type"] == "PREDICTION"]
    for prediction in predictions:
        slug = prediction["slug"]
        if (slug in outcomes and (slug not in fills or slug in settled)) or time.time() < prediction["end_epoch"] + 30:
            continue
        market = await http.json(f"{GAMMA}/markets/{prediction['market_id']}")
        if not isinstance(market, dict) or str(market.get("id")) != prediction["market_id"]:
            continue
        payout_up = resolution_payout(market, prediction["up_asset_id"])
        if payout_up is None:
            continue
        if slug not in outcomes:
            journal.append({"type": "OUTCOME", "slug": slug, "outcome_up": str(payout_up),
                            "market_id": prediction["market_id"], "settled_at": utc_iso()})
        if slug in fills and slug not in settled:
            fill = fills[slug]
            payout = resolution_payout(market, fill["asset_id"])
            if payout is None:
                continue
            pnl = D(fill["quantity"]) * payout - D(fill["total_cost_sim_usd"])
            journal.append({"type": "SETTLEMENT", "slug": slug, "market_id": fill["market_id"],
                            "payout_per_share": str(payout), "pnl_sim_usd": str(pnl),
                            "settled_at": utc_iso(), "official_resolution_status": market.get("umaResolutionStatus")})


def report(journal: ExperimentJournal) -> dict[str, Any]:
    events = journal.events
    predictions = {r["slug"]: r for r in events if r["type"] == "PREDICTION"}
    outcomes = {r["slug"]: r for r in events if r["type"] == "OUTCOME"}
    pairs = [(float(predictions[slug]["probability_up"]), float(row["outcome_up"]))
             for slug, row in outcomes.items() if row["outcome_up"] in ("0", "1")]
    settlements = [r for r in events if r["type"] == "SETTLEMENT"]
    daily: dict[str, float] = {}
    for row in settlements:
        day = utc_iso(int(row["slug"].rsplit("-", 1)[1]))[:10]
        daily[day] = daily.get(day, 0) + float(row["pnl_sim_usd"])
    opportunity_days = sorted({r["observed_at"][:10] for r in events if r["type"] == "OPPORTUNITY"})
    values = [daily.get(day, 0.0) for day in opportunity_days]
    ci: list[float] | None = None
    if len(values) >= 2:
        rng = random.Random(20260918)
        samples = sorted(sum(rng.choices(values, k=len(values))) for _ in range(2000))
        ci = [samples[49], samples[1949]]
    money = balances(events)
    gate = (len(settlements) >= 100 and len(opportunity_days) >= 7
            and ci is not None and ci[0] > 0 and money["realized_pnl"] > 0)
    fill_count = sum(r["type"] == "PAPER_FILL" for r in events)
    return {
        "as_of": utc_iso(), "experiment": EXPERIMENT, "mode": "PAPER_ONLY_PUBLIC_GET",
        "config_sha256": journal.config_hash,
        "opportunities": sum(r["type"] == "OPPORTUNITY" for r in events),
        "predictions": len(predictions), "paper_fills": fill_count,
        "settled_fills": len(settlements), "open_fills": fill_count - len(settlements),
        "wins": sum(D(r["pnl_sim_usd"]) > 0 for r in settlements),
        "initial_capital_sim_usd": POLICY["initial_capital_sim_usd"],
        **{f"{key}_sim_usd": str(value) for key, value in money.items()},
        "bootstrap_day_pnl_ci95": ci, "paper_evidence_gate_passed": gate,
        "financial_edge_proven": False,
        "financial_status": "PAPER_GATE_PASSED_REQUIRES_EXECUTION_VALIDATION" if gate else "INSUFFICIENT_PROSPECTIVE_EVIDENCE",
        "calibration": {"resolved_predictions": len(pairs),
                        "brier_score": sum((p-y)**2 for p, y in pairs) / len(pairs) if pairs else None,
                        "log_loss": -sum(y*math.log(p)+(1-y)*math.log(1-p) for p, y in pairs) / len(pairs) if pairs else None,
                        "scope": "ALL_RECORDED_PREDICTIONS_INCLUDING_ABSTENTIONS"},
        "classification": "NEW_PROSPECTIVE_BOOK_EXECUTION_NOT_HISTORICAL_REPLICATION",
        "fee_model": "0.10_CURVE_SENSITIVITY_NOT_VERIFIED_ACTUAL_FEE",
        "rejection_counts": {reason: sum(r.get("reason") == reason for r in events)
                             for reason in sorted({str(r["reason"]) for r in events if "reason" in r})},
        "latest_settlements": settlements[-10:],
    }


def write_report(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with temporary.open("wb") as handle:
        handle.write(json.dumps(document, indent=2, sort_keys=True, allow_nan=False).encode() + b"\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)
