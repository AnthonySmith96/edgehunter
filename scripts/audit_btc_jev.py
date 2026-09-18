"""Offline, paired audit of a frozen JEV journal snapshot; never calls a provider.

Default output is stdout. --output creates a new file and refuses overwrites.
Official outcomes can be supplied separately, including decisions that abstained.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from statistics import mean
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
D = Decimal


def _binary(value: Any) -> int | None:
    numeric = D(str(value))
    if not numeric.is_finite() or numeric not in (D(0), D("0.5"), D(1)):
        raise ValueError("Invalid terminal outcome")
    return int(numeric) if numeric in (D(0), D(1)) else None


def _unique(rows: list[dict[str, Any]], kind: str) -> dict[str, dict[str, Any]]:
    selected = [row for row in rows if row.get("type") == kind]
    result = {row["slug"]: row for row in selected}
    if len(result) != len(selected):
        raise ValueError(f"Duplicate {kind} per slug: audit requires explicit reconciliation")
    return result


def _paired(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    result: dict[str, Any] = {"resolved": n}
    for name in ("jev", "market"):
        probabilities = [row[name] for row in rows]
        result[name] = {
            "brier": mean((p - row["y"]) ** 2 for p, row in zip(probabilities, rows)) if n else None,
            "log_loss": -mean(
                row["y"] * math.log(max(p, 1e-12))
                + (1 - row["y"]) * math.log(max(1 - p, 1e-12))
                for p, row in zip(probabilities, rows)
            ) if n else None,
            "correct": sum((p >= 0.5) == bool(row["y"]) for p, row in zip(probabilities, rows)),
            "accuracy": mean((p >= 0.5) == bool(row["y"]) for p, row in zip(probabilities, rows))
            if n else None,
        }
    result["direction_disagreements"] = [
        row["slug"] for row in rows if (row["jev"] >= 0.5) != (row["market"] >= 0.5)
    ]
    return result


def audit(journal: Path, outcome_path: Path | None = None) -> dict[str, Any]:
    raw = journal.read_bytes()
    rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
    decisions = _unique(rows, "JEV_DECISION")
    fills = _unique(rows, "PAPER_FILL")
    settlements = _unique(rows, "SETTLEMENT")
    outcomes: dict[str, int] = {}
    terminal_outcomes: set[str] = set()
    for slug, settlement in settlements.items():
        if slug not in fills or slug not in decisions:
            raise ValueError(f"Settlement without recorded fill/decision: {slug}")
        won = settlement["won"]
        if type(won) is not bool:
            raise ValueError(f"Non-boolean settlement result: {slug}")
        payoff = _binary(settlement["payoff_per_share"])
        terminal_outcomes.add(slug)
        if payoff is None:
            continue
        if won != bool(payoff):
            raise ValueError(f"Won flag conflicts with binary payoff: {slug}")
        outcomes[slug] = payoff if fills[slug]["side"] == "UP" else 1 - payoff
    for slug, label in _unique(rows, "OUTCOME").items():
        if slug not in decisions or str(label["market_id"]) != str(decisions[slug]["market_id"]):
            raise ValueError(f"Journal outcome does not match decision market: {slug}")
        if label.get("official_resolution_status") != "resolved":
            continue
        terminal_outcomes.add(slug)
        outcome = _binary(label["outcome_up"])
        if slug in outcomes and outcomes[slug] != outcome:
            raise ValueError(f"Journal outcome conflicts with settlement: {slug}")
        if outcome is not None:
            outcomes[slug] = outcome
    provenance: dict[str, Any] = {
        "journal": str(journal), "journal_sha256": hashlib.sha256(raw).hexdigest(),
        "journal_bytes": len(raw), "journal_events": len(rows),
    }
    if outcome_path is not None:
        label_raw = outcome_path.read_bytes()
        labels = json.loads(label_raw)
        label_time = datetime.fromisoformat(labels["as_of"])
        seen: set[str] = set()
        for label in labels["outcomes"]:
            slug = label["slug"]
            if slug in seen or slug not in decisions:
                raise ValueError(f"Duplicate or unmatched external outcome: {slug}")
            seen.add(slug)
            if str(label["market_id"]) != str(decisions[slug]["market_id"]):
                raise ValueError(f"External market does not match decision: {slug}")
            if label.get("resolution_status") != "resolved":
                continue
            outcome = _binary(label["outcome_up"])
            epoch = int(slug.rsplit("-", 1)[1])
            decision_time = datetime.fromisoformat(decisions[slug]["recorded_at"])
            if label_time.timestamp() < epoch + 300 or label_time < decision_time:
                raise ValueError(f"Outcome collected before decision/expiry: {slug}")
            if slug in outcomes and outcomes[slug] != outcome:
                raise ValueError(f"Official outcome conflicts with journal settlement: {slug}")
            terminal_outcomes.add(slug)
            if outcome is not None:
                outcomes[slug] = outcome
        provenance.update({
            "outcomes": str(outcome_path), "outcomes_sha256": hashlib.sha256(label_raw).hexdigest(),
            "outcomes_as_of": labels["as_of"],
        })
    invalid_timing = {
        slug for slug, row in decisions.items()
        if row["decision"].get("reason") == "DECISION_WINDOW_EXPIRED_AFTER_INFERENCE"
        or datetime.fromisoformat(row["recorded_at"]).timestamp() >= int(slug.rsplit("-", 1)[1]) + 300
    }
    invalid_usage = {
        slug for slug, row in decisions.items()
        if row["decision"].get("tokens_used", {}).get("input_tokens", 0) <= 0
    }
    invalid = invalid_timing | invalid_usage
    paired = []
    for slug, row in decisions.items():
        if slug not in outcomes or slug in invalid:
            continue
        decision = row["decision"]
        jev = float(decision["probability_up"])
        market = float(decision["state_dossier"]["polymarket_implied_probability_up"])
        if not (math.isfinite(jev) and math.isfinite(market) and 0 <= jev <= 1 and 0 <= market <= 1):
            raise ValueError(f"Invalid probability: {slug}")
        paired.append({"slug": slug, "y": outcomes[slug], "jev": jev, "market": market})
    trades = []
    for slug, settlement in settlements.items():
        fill = fills[slug]
        decision = decisions[slug]["decision"]
        cost = D(fill["total_cost_sim_usd"])
        quantity = D(fill["quantity"])
        if quantity <= 0 or cost <= 0:
            raise ValueError(f"Nonpositive fill cost/quantity: {slug}")
        p_up = float(decision["probability_up"])
        trades.append({
            "slug": slug, "side": fill["side"], "won": settlement["won"],
            "binary_payoff": _binary(settlement["payoff_per_share"]),
            "cost_sim_usd": str(cost), "pnl_sim_usd": settlement["pnl_sim_usd"],
            "settled_at": settlement["settled_at"],
            "probability_bought": p_up if fill["side"] == "UP" else 1 - p_up,
            "break_even_probability": float(cost / quantity),
            "has_kelly_field": "kelly_fraction" in decision,
        })
    wins = [trade for trade in trades if trade["won"]]
    losses = [trade for trade in trades if trade["binary_payoff"] == 0]
    binary_trades = [trade for trade in trades if trade["binary_payoff"] is not None]
    pnl = sum((D(trade["pnl_sim_usd"]) for trade in trades), D(0))
    initial = D("100")
    equity = peak = initial
    drawdown = D(0)
    for trade in sorted(trades, key=lambda value: value["settled_at"]):
        equity += D(trade["pnl_sim_usd"])
        peak = max(peak, equity)
        drawdown = max(drawdown, (peak - equity) / peak)
    groups = {}
    for has_kelly in (False, True):
        group = [trade for trade in trades if trade["has_kelly_field"] == has_kelly]
        groups["kelly_field_present" if has_kelly else "kelly_field_absent"] = {
            "trades": len(group), "wins": sum(trade["won"] for trade in group),
            "pnl_sim_usd": str(sum((D(trade["pnl_sim_usd"]) for trade in group), D(0))),
        }
    histogram = Counter(str(row["decision"]["probability_up"]) for row in decisions.values())
    confidence_bins = []
    for lower, upper in ((0.5, 0.7), (0.7, 0.9), (0.9, 1.00000001)):
        group = [row for row in paired if lower <= max(row["jev"], 1 - row["jev"]) < upper]
        confidence_bins.append({
            "lower_inclusive": lower, "upper_exclusive": min(upper, 1.0),
            "includes_probability_one": upper > 1,
            "count": len(group),
            "mean_confidence": mean(max(row["jev"], 1 - row["jev"]) for row in group) if group else None,
            "actual_accuracy": mean((row["jev"] >= 0.5) == bool(row["y"]) for row in group)
            if group else None,
        })
    return {
        "audit_schema": 1, "created_at": datetime.now(timezone.utc).isoformat(),
        "classification": "DESCRIPTIVE_PROSPECTIVE_AUDIT_NO_PARAMETER_SELECTION",
        "provenance": provenance,
        "event_counts": dict(Counter(row["type"] for row in rows)),
        "first_decision_at": min((row["recorded_at"] for row in decisions.values()), default=None),
        "last_decision_at": max((row["recorded_at"] for row in decisions.values()), default=None),
        "first_kelly_field_at": min((row["recorded_at"] for row in decisions.values()
                                     if "kelly_fraction" in row["decision"]), default=None),
        "all_decisions": {"recorded": len(decisions), "invalid_timing_excluded": len(invalid_timing),
                          "invalid_usage_excluded": len(invalid_usage),
                          "invalid_forecasts_excluded": len(invalid),
                          "nonbinary_outcomes_excluded": sum(slug in terminal_outcomes and slug not in outcomes
                                                             for slug in decisions if slug not in invalid),
                          "unresolved": sum(slug not in terminal_outcomes for slug in decisions if slug not in invalid),
                          **_paired(paired)},
        "settled_fills_only": _paired([row for row in paired if row["slug"] in settlements]),
        "probability_up_histogram": histogram, "direction_confidence_bins": confidence_bins,
        "financial": {
            "initial_capital_sim_usd_assumed": str(initial), "settled_trades": len(trades),
            "open_fills": len(fills) - len(settlements), "wins": len(wins), "losses": len(losses),
            "void_or_nonbinary_settlements": len(trades) - len(binary_trades),
            "win_rate": len(wins) / len(binary_trades) if binary_trades else None,
            "realized_pnl_sim_usd": str(pnl), "realized_equity_sim_usd": str(initial + pnl),
            "gross_winning_pnl_sim_usd": str(sum((D(t["pnl_sim_usd"]) for t in wins), D(0))),
            "gross_losing_pnl_sim_usd": str(sum((D(t["pnl_sim_usd"]) for t in losses), D(0))),
            "turnover_sim_usd": str(sum((D(t["cost_sim_usd"]) for t in trades), D(0))),
            "mean_probability_bought": mean(t["probability_bought"] for t in trades) if trades else None,
            "mean_break_even_probability": mean(t["break_even_probability"] for t in trades)
            if trades else None,
            "realized_equity_peak_sim_usd": str(peak),
            "maximum_realized_equity_drawdown_fraction": float(drawdown),
        },
        "inferred_sizing_cohorts": groups, "trades": trades, "paired_predictions": paired,
        "limitations": [
            "A single short session is not evidence of repeatable financial edge.",
            "All-decisions calibration uses only matched resolved labels; missing outcomes are not filled.",
            "Fill-only calibration is selected by the strategy and must not represent all forecasts.",
            "Market probabilities are recorded rounded normalized asks, not executable returns.",
            "Kelly field cohorts are inferred schema changes, not proof of frozen policy versions.",
            "Initial capital is assumed 100 SIM_USD; drawdown uses realized settlements only.",
            "Historical fill accounting is preserved; liquidity, timing, and fee realism are not validated here.",
        ],
        "financial_edge_proven": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", type=Path, default=ROOT / "data/btc_jev/events.jsonl")
    parser.add_argument("--outcomes", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit(args.journal, args.outcomes)
    encoded = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(encoded)
        print(str(args.output))
    else:
        print(encoded, end="")


if __name__ == "__main__":
    main()
