"""Prospective BTC 5-minute Paper Trading Engine commanded by Jev (TypeSafe AI).

All execution is causal, on paper (SIM_USD), against live public Polymarket books.
Jev is the Executive Commander determining all trade decisions.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import time
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import ROUND_FLOOR, Decimal
from pathlib import Path
from typing import Any

from edgehunter.ingestion.http import PublicHTTP
from edgehunter.intelligence.jev_commander import JevCommander
from edgehunter.ops.lock import InstanceLock
from edgehunter.ops.paper_candidate import resolution_payout
from edgehunter.research.btc_challenger import POLICY as DATA_POLICY
from edgehunter.research.btc_challenger import checked_book
from edgehunter.research.btc_lag import realized_vol_per_second
from edgehunter.venues.polymarket.public import decode_list

D = Decimal
EXPERIMENT = "btc_5m_jev_commander_v1"
EXECUTION_REVISION = "jev_execution_v2_fresh_inputs_kelly_cap"
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
COINBASE = "https://api.exchange.coinbase.com"

DEFAULT_POLICY: dict[str, Any] = {
    "initial_capital_sim_usd": "100",
    "maximum_stake_sim_usd": "10",
    "minimum_stake_sim_usd": "2",
    "fee_curve_sensitivity": "0.10",
    "slippage_per_share": "0.03",
    "decision_horizon_seconds": 160,
    "decision_tolerance_seconds": 60,
    "minimum_edge": "0.04",
    "max_entry_price": "0.72",
    "sizing_mode": "fractional_kelly",
    "kelly_fraction": "0.25",
    "timeout_seconds": 4.0,
}


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def utc_iso(epoch: float | None = None) -> str:
    return datetime.fromtimestamp(time.time() if epoch is None else epoch, UTC).isoformat()


def balances(events: list[dict[str, Any]]) -> dict[str, Decimal]:
    cash = Decimal(DEFAULT_POLICY["initial_capital_sim_usd"])
    realized_pnl = Decimal("0")
    for event in events:
        if event["type"] == "PAPER_FILL":
            cash -= Decimal(str(event["total_cost_sim_usd"]))
        elif event["type"] == "SETTLEMENT":
            payout = Decimal(str(event["payout"]))
            pnl = Decimal(str(event["pnl_sim_usd"]))
            cash += payout
            realized_pnl += pnl
    return {"cash": cash, "realized_pnl": realized_pnl}


class JevJournal:
    """Thread-safe and process-safe append-only journal for JEV trading events."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.events: list[dict[str, Any]] = []
        self.lock = InstanceLock(path.with_suffix(".lock"))
        self.active = False

    def __enter__(self) -> JevJournal:
        self.lock.__enter__()
        self.active = True
        self._recover()
        return self

    def __exit__(self, *args: Any) -> None:
        self.active = False
        self.lock.__exit__(*args)

    def _recover(self) -> None:
        self.events = []
        if not self.path.exists():
            return
        raw = self.path.read_bytes()
        end = raw.rfind(b"\n") + 1
        for line in raw[:end].splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            self.events.append(event)

    def attempted(self, slug: str) -> bool:
        return any(row.get("slug") == slug and row["type"] == "OPPORTUNITY" for row in self.events)

    def append(self, event: dict[str, Any]) -> dict[str, Any]:
        document = {
            **event,
            "recorded_at": utc_iso(),
            "experiment": EXPERIMENT,
            "sequence": len(self.events),
        }
        document["event_id"] = hashlib.sha256(canonical(document)).hexdigest()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("ab") as handle:
            handle.write(canonical(document) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        self.events.append(document)
        return document


async def fetch_market_inputs(http: PublicHTTP, tokens: list[str]) -> tuple[Any, Any, Any, Any]:
    now = time.time()
    candle_params: dict[str, str | int] = {
        "granularity": 60,
        "start": utc_iso(int(now) // 60 * 60 - 7260),
        "end": utc_iso(now),
    }
    result = await asyncio.gather(
        http.json(f"{CLOB}/book", {"token_id": tokens[0]}),
        http.json(f"{CLOB}/book", {"token_id": tokens[1]}),
        http.json(f"{COINBASE}/products/BTC-USD/candles", candle_params),
        http.json(f"{COINBASE}/products/BTC-USD/ticker"),
    )
    return result[0], result[1], result[2], result[3]


def compute_btc_features(
    *, epoch: int, candles: Any, ticker: Any, now: float
) -> tuple[float, float, float]:
    """Compute (spot_price, open_price, vol_per_second) from Coinbase feeds."""
    stamp = datetime.fromisoformat(str(ticker["time"]).replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("NAIVE_TICKER_TIME")
    if not -DATA_POLICY["maximum_future_clock_skew_seconds"] <= now - stamp.timestamp() <= DATA_POLICY["maximum_ticker_age_seconds"]:
        raise ValueError("STALE_OR_FUTURE_TICKER")
    spot = float(ticker["price"])
    if not math.isfinite(spot) or spot <= 0:
        raise ValueError("INVALID_TICKER_PRICE")
    if not isinstance(candles, list):
        raise ValueError("INVALID_CANDLES")
    by_epoch: dict[int, list[float]] = {}
    for c in candles:
        if not isinstance(c, list) or len(c) < 6:
            raise ValueError("INVALID_CANDLE")
        values = [float(v) for v in c[:6]]
        if not all(math.isfinite(v) for v in values):
            raise ValueError("INVALID_CANDLE_VALUES")
        moment = int(values[0])
        if moment != values[0] or moment % 60 or moment in by_epoch:
            raise ValueError("INVALID_OR_DUPLICATE_CANDLE_TIME")
        if (any(v <= 0 for v in values[1:5]) or values[5] < 0
                or values[1] > min(values[3:5]) or values[2] < max(values[3:5])):
            raise ValueError("INVALID_CANDLE_VALUES")
        by_epoch[moment] = values

    # Never invent a neutral opening reference or substitute a default volatility.
    if epoch not in by_epoch:
        raise ValueError("MISSING_OPEN_CANDLE")
    open_p = by_epoch[epoch][3]
    required = list(range(epoch - 7200, epoch, 60))
    contiguous: list[float] = []
    for moment in reversed(required):
        if moment not in by_epoch:
            break
        contiguous.append(by_epoch[moment][4])
    if len(contiguous) < 30:
        raise ValueError("INSUFFICIENT_CONTIGUOUS_VOLATILITY_CANDLES")
    vol = realized_vol_per_second(reversed(contiguous))
    return spot, open_p, vol


async def observe_and_trade(
    http: PublicHTTP,
    commander: JevCommander,
    journal: JevJournal,
    now: float | None = None,
) -> dict[str, Any] | None:
    clock: Callable[[], float]
    if now is None:
        clock = time.time
    else:
        fixed_now = now

        def clock() -> float:
            return fixed_now
    now = clock()
    epoch = int(now) // 300 * 300
    slug = f"btc-updown-5m-{epoch}"
    remaining = epoch + 300 - now
    horizon = DEFAULT_POLICY["decision_horizon_seconds"]

    if journal.attempted(slug) or remaining > horizon:
        return None

    journal.append({
        "type": "OPPORTUNITY",
        "slug": slug,
        "epoch": epoch,
        "remaining_seconds": round(remaining, 1),
        "observed_at": utc_iso(now),
    })

    if remaining < horizon - DEFAULT_POLICY["decision_tolerance_seconds"]:
        journal.append({
            "type": "REJECTION",
            "slug": slug,
            "reason": "MISSED_DECISION_WINDOW",
            "remaining_seconds": remaining,
        })
        return {"slug": slug, "action": "REJECTED", "reason": "MISSED_DECISION_WINDOW"}

    try:
        event = await http.json(f"{GAMMA}/events/slug/{slug}")
        if not isinstance(event, dict) or not event.get("markets"):
            raise ValueError("INVALID_BTC_EVENT")
        market = event["markets"][0]
        tokens = [str(v) for v in decode_list(market.get("clobTokenIds", []))]
        if (len(tokens) != 2 or len(set(tokens)) != 2
                or decode_list(market.get("outcomes", [])) != ["Up", "Down"]
                or market.get("closed") is not False
                or market.get("acceptingOrders") is not True):
            raise ValueError("INVALID_TOKEN_IDS")

        up_raw, down_raw, candles, ticker = await fetch_market_inputs(http, tokens)
        now_eval = clock()
        if epoch + 300 - now_eval < horizon - DEFAULT_POLICY["decision_tolerance_seconds"]:
            raise ValueError("MISSED_DECISION_WINDOW_AFTER_FETCH")
        up_book = checked_book(up_raw, tokens[0], now_eval)
        down_book = checked_book(down_raw, tokens[1], now_eval)
        if abs(up_book.timestamp - down_book.timestamp) > DATA_POLICY["maximum_pair_skew_seconds"]:
            raise ValueError("UNSYNCHRONIZED_BOOKS")
        spot_price, open_price, vol = compute_btc_features(
            epoch=epoch, candles=candles, ticker=ticker, now=now_eval
        )

        current_balances = balances(journal.events)
        decision = await commander.decide(
            slug=slug,
            seconds_to_expiry=epoch + 300 - now_eval,
            spot_price=spot_price,
            open_price=open_price,
            vol_per_second=vol,
            up_book=up_book,
            down_book=down_book,
            tokens=tokens,
            available_cash=current_balances["cash"],
            available_until=epoch + 300 - 15,
        )

        journal.append({
            "type": "JEV_DECISION",
            "slug": slug,
            "market_id": str(market["id"]),
            "up_asset_id": tokens[0],
            "end_epoch": epoch + 300,
            "execution_revision": EXECUTION_REVISION,
            "input_observed_at": utc_iso(now_eval),
            "ticker_time": ticker["time"],
            "decision": decision.to_dict(),
        })

        if decision.action == "ENTER" and decision.side and decision.asset_id:
            # Inference takes time: validate executable price and depth again.
            fresh_raw = await http.json(f"{CLOB}/book", {"token_id": decision.asset_id})
            fill_now = clock()
            if fill_now >= epoch + 300 - 15:
                raise ValueError("DECISION_WINDOW_EXPIRED_BEFORE_FILL")
            fresh_book = checked_book(fresh_raw, decision.asset_id, fill_now)
            if fresh_book.ask > decision.limit_price:
                raise ValueError("PRICE_MOVED_BEYOND_LIMIT")
            if (decision.quantity < fresh_book.minimum
                    or decision.quantity > fresh_book.size * commander.depth_fraction):
                raise ValueError("EXECUTABLE_DEPTH_CHANGED")
            # Paper execution of JEV's executive command
            journal.append({
                "type": "PAPER_FILL",
                "slug": slug,
                "market_id": str(market["id"]),
                "execution_revision": EXECUTION_REVISION,
                "side": decision.side,
                "asset_id": decision.asset_id,
                "quantity": str(decision.quantity),
                "fill_price": str(decision.limit_price),
                "observed_ask": str(decision.chosen_ask),
                "execution_observed_ask": str(fresh_book.ask),
                "execution_book_timestamp": fresh_book.timestamp,
                "fee_sim_usd": str(decision.fee_per_share * decision.quantity),
                "total_cost_sim_usd": str(decision.total_cost),
                "jev_edge": str(decision.edge),
                "jev_probability_up": decision.probability_up,
                "jev_latency_ms": decision.jev_latency_ms,
                "jev_model": decision.jev_model,
                "end_epoch": epoch + 300,
                "filled_at": utc_iso(),
            })
            return {"slug": slug, "action": "ENTER", "side": decision.side, "cost": str(decision.total_cost)}
        else:
            journal.append({
                "type": "NO_FILL",
                "slug": slug,
                "reason": decision.reason,
                "probability_up": decision.probability_up,
                "jev_latency_ms": decision.jev_latency_ms,
            })
            return {"slug": slug, "action": "PASS", "reason": decision.reason}

    except Exception as exc:
        journal.append({
            "type": "ERROR",
            "slug": slug,
            "error_class": type(exc).__name__,
            "reason": str(exc),
        })
        return {"slug": slug, "action": "ERROR", "reason": str(exc)}


async def settle_jev_fills(http: PublicHTTP, journal: JevJournal) -> int:
    """Resolve every forecast, including abstentions, and settle any paper fill."""
    now = time.time()
    settled_slugs = {r["slug"] for r in journal.events if r["type"] == "SETTLEMENT"}
    outcome_slugs = {r["slug"] for r in journal.events if r["type"] == "OUTCOME"}
    fills = {r["slug"]: r for r in journal.events if r["type"] == "PAPER_FILL"}
    forecasts = {r["slug"]: r for r in journal.events if r["type"] == "JEV_DECISION"}
    targets = {**forecasts, **fills}
    settled_count = 0

    for slug, target in targets.items():
        needs_outcome = slug in forecasts and slug not in outcome_slugs
        fill = fills.get(slug)
        needs_settlement = fill is not None and slug not in settled_slugs
        if not needs_outcome and not needs_settlement:
            continue
        try:
            end_epoch = target.get("end_epoch")
            if end_epoch is None:
                end_epoch = int(slug.rsplit("-", 1)[-1]) + 300
            if now < end_epoch + 15:
                continue
            market_data = await http.json(f"{GAMMA}/markets/{target['market_id']}")
            if needs_outcome:
                tokens = decode_list(market_data.get("clobTokenIds", []))
                outcomes = decode_list(market_data.get("outcomes", []))
                if len(tokens) == len(outcomes) == 2 and set(outcomes) == {"Up", "Down"}:
                    up_payoff = resolution_payout(market_data, str(tokens[outcomes.index("Up")]))
                    if up_payoff is not None:
                        journal.append({
                            "type": "OUTCOME", "slug": slug, "market_id": target["market_id"],
                            "outcome_up": format(up_payoff.normalize(), "f"),
                            "official_resolution_status": market_data.get("umaResolutionStatus"),
                        })
            payoff = resolution_payout(market_data, fill["asset_id"]) if needs_settlement and fill else None
            if payoff is not None and fill is not None:
                quantity = Decimal(str(fill["quantity"]))
                cost = Decimal(str(fill["total_cost_sim_usd"]))
                total_payout = (quantity * payoff).quantize(Decimal("0.0001"), rounding=ROUND_FLOOR)
                pnl = total_payout - cost
                journal.append({
                    "type": "SETTLEMENT",
                    "slug": fill["slug"],
                    "market_id": fill["market_id"],
                    "asset_id": fill["asset_id"],
                    "payoff_per_share": str(payoff),
                    "payout": str(total_payout),
                    "pnl_sim_usd": str(pnl),
                    "won": bool(payoff == Decimal("1")),
                    "settled_at": utc_iso(),
                })
                settled_count += 1
        except Exception:
            pass

    return settled_count


def calibration_report(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Score valid forecasts and their market benchmark on exactly the same outcomes."""
    terminal_outcomes = {e["slug"]: float(e["outcome_up"]) for e in events if e["type"] == "OUTCOME"}
    outcomes = {slug: y for slug, y in terminal_outcomes.items() if y in (0, 1)}
    forecasts = {e["slug"]: e["decision"] for e in events if e["type"] == "JEV_DECISION"
                 and e["decision"].get("tokens_used", {}).get("input_tokens", 0) > 0
                 and e["decision"].get("reason") != "DECISION_WINDOW_EXPIRED_AFTER_INFERENCE"}
    rows = [(d, outcomes[slug]) for slug, d in forecasts.items() if slug in outcomes]
    paired = [(d, y) for d, y in rows
              if "polymarket_implied_probability_up" in d.get("state_dossier", {})]

    def score(probabilities: list[tuple[float, float]]) -> dict[str, float | None]:
        n = len(probabilities)
        return {
            "brier_score": sum((p - y) ** 2 for p, y in probabilities) / n if n else None,
            "log_loss": -sum(y * math.log(max(1e-12, p))
                             + (1 - y) * math.log(max(1e-12, 1 - p))
                             for p, y in probabilities) / n if n else None,
        }

    return {
        "scope": "ALL_VALID_FORECASTS_INCLUDING_ABSTENTIONS_OFFICIAL_OUTCOMES",
        "valid_forecasts": len(forecasts), "resolved_forecasts": len(rows),
        "pending_outcomes": sum(slug not in terminal_outcomes for slug in forecasts),
        "excluded_nonbinary_outcomes": sum(slug in terminal_outcomes and slug not in outcomes
                                            for slug in forecasts),
        "jev_all_resolved": score([(float(d["probability_up"]), y) for d, y in rows]),
        "paired_forecasts": len(paired),
        "jev_paired": score([(float(d["probability_up"]), y) for d, y in paired]),
        "market_paired": score([(float(d["state_dossier"]["polymarket_implied_probability_up"]), y)
                                for d, y in paired]),
        "financial_edge_proven": False,
    }


def generate_status_report(journal: JevJournal, commander: JevCommander) -> dict[str, Any]:
    b = balances(journal.events)
    opportunities = sum(1 for e in journal.events if e["type"] == "OPPORTUNITY")
    decisions = [e for e in journal.events if e["type"] == "JEV_DECISION"]
    fills = [e for e in journal.events if e["type"] == "PAPER_FILL"]
    settlements = [e for e in journal.events if e["type"] == "SETTLEMENT"]
    settlement_map = {s["slug"]: s for s in settlements}

    wins = sum(1 for s in settlements if s.get("won") is True)
    losses = len(settlements) - wins
    win_rate = (wins / len(settlements)) * 100.0 if settlements else 0.0

    # Calculate streaks
    longest_win_streak = 0
    longest_loss_streak = 0
    temp_w = 0
    temp_l = 0
    for s in settlements:
        if s.get("won") is True:
            temp_w += 1
            temp_l = 0
            if temp_w > longest_win_streak:
                longest_win_streak = temp_w
        else:
            temp_l += 1
            temp_w = 0
            if temp_l > longest_loss_streak:
                longest_loss_streak = temp_l

    if settlements:
        last_won = settlements[-1].get("won") is True
        count = 0
        for s in reversed(settlements):
            if (s.get("won") is True) == last_won:
                count += 1
            else:
                break
        current_streak = f"{count} {'VICTORIAS' if last_won else 'DERROTAS'} CONSECUTIVAS"
    else:
        current_streak = "SIN OPERACIONES CERRADAS"

    initial_cap = Decimal(DEFAULT_POLICY["initial_capital_sim_usd"])
    total_equity = initial_cap + b["realized_pnl"]
    capital_in_play = sum(
        Decimal(str(f.get("total_cost_sim_usd", "0")))
        for f in fills
        if f["slug"] not in settlement_map
    )
    roi_percent = (b["realized_pnl"] / initial_cap) * 100 if initial_cap else Decimal("0")

    last_decision = decisions[-1]["decision"] if decisions else None

    # Summary of recent trades with their status (WON, LOST, PENDING)
    trades_summary = []
    for fill in fills:
        slug = fill["slug"]
        side = fill.get("side", "UNKNOWN")
        cost = fill.get("total_cost_sim_usd", "0")
        edge = fill.get("jev_edge", "0")
        settlement = settlement_map.get(slug)
        if settlement is not None:
            trades_summary.append({
                "slug": slug,
                "side": side,
                "cost_sim_usd": str(cost),
                "edge": str(edge),
                "status": "WON" if settlement.get("won") else "LOST",
                "pnl_sim_usd": str(settlement.get("pnl_sim_usd", "0")),
                "settled_at": settlement.get("settled_at"),
            })
        else:
            trades_summary.append({
                "slug": slug,
                "side": side,
                "cost_sim_usd": str(cost),
                "edge": str(edge),
                "status": "PENDING_RESOLUTION",
                "pnl_sim_usd": "0",
                "settled_at": None,
            })

    balance_summary = {
        "total_equity_sim_usd": str(total_equity),
        "cash_available_sim_usd": str(b["cash"]),
        "capital_in_play_sim_usd": str(capital_in_play),
        "initial_capital_sim_usd": str(initial_cap),
        "realized_pnl_sim_usd": str(b["realized_pnl"]),
        "net_roi_percent": round(float(roi_percent), 2),
        "record": f"{wins}W - {losses}L",
        "wins": wins,
        "losses": losses,
        "win_rate_percent": round(win_rate, 2),
        "current_streak": current_streak,
        "longest_win_streak": longest_win_streak,
        "longest_loss_streak": longest_loss_streak,
    }

    return {
        "as_of": utc_iso(),
        "experiment": EXPERIMENT,
        "commander_model": commander.model,
        "execution_revision": EXECUTION_REVISION,
        "operational_status": "OBSERVING_AND_READY",
        "balance_summary": balance_summary,
        "cash_sim_usd": str(b["cash"]),
        "realized_pnl_sim_usd": str(b["realized_pnl"]),
        "opportunities_seen": opportunities,
        "jev_evaluations": len(decisions),
        "paper_fills_executed": len(fills),
        "settled_trades": len(settlements),
        "wins": wins,
        "losses": losses,
        "win_rate_percent": round(win_rate, 2),
        "current_streak": current_streak,
        "recent_trades": trades_summary[-10:],
        "last_decision": last_decision,
        "calibration": calibration_report(journal.events),
        "fee_model": "0.10_CURVE_SENSITIVITY_NOT_VERIFIED_ACTUAL_FEE",
        "equity_valuation": "CASH_PLUS_OPEN_POSITIONS_AT_COST_NOT_MARK_TO_MARKET",
        "financial_edge_proven": False,
        "financial_mandate": "PAPER_TRADING_ONLY_SIM_USD",
    }
