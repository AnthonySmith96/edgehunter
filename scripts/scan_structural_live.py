"""Bounded public scan for fully collateralized binary complete-set discounts."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from decimal import Decimal as D
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from edgehunter.ingestion.http import SourceError  # noqa: E402
from edgehunter.venues.polymarket.public import PolymarketPublic, decode_list  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports" / "structural_live_scan.json"


async def scan(limit: int) -> dict[str, Any]:
    if not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500")
    source = PolymarketPublic()
    try:
        pages = await asyncio.gather(*(source.markets(min(100, limit-offset), offset)
                                       for offset in range(0, limit, 100)))
        markets = {str(market["id"]): market for page in pages for market in page}
        semaphore = asyncio.Semaphore(6)

        async def inspect(market: dict[str, Any]) -> dict[str, Any]:
            base = {"market_id": str(market["id"]), "question": market.get("question")}
            try:
                outcomes = decode_list(market.get("outcomes", []))
                tokens = [str(item) for item in decode_list(market.get("clobTokenIds", []))]
                if outcomes != ["Yes", "No"] or len(tokens) != 2:
                    return {**base, "status": "INELIGIBLE", "reason": "NOT_BINARY_YES_NO"}
                if market.get("negRisk"):
                    return {**base, "status": "INELIGIBLE", "reason": "NEGATIVE_RISK_UNSUPPORTED"}
                if not market.get("resolutionSource") or not market.get("conditionId"):
                    return {**base, "status": "INELIGIBLE", "reason": "CONTRACT_METADATA_INCOMPLETE"}
                async with semaphore:
                    books = await asyncio.gather(*(source.book(token) for token in tokens))
                asks = []
                capacity = None
                for event in books:
                    payload = event.payload
                    levels = sorted(payload["asks"], key=lambda row: D(str(row["price"])))
                    if not levels:
                        return {**base, "status": "INELIGIBLE", "reason": "EMPTY_ASK"}
                    ask, size = D(str(levels[0]["price"])), D(str(levels[0]["size"]))
                    tick = D(str(payload.get("tick_size", market.get("orderPriceMinTickSize", "0.01"))))
                    minimum = D(str(payload.get("min_order_size", market.get("orderMinSize", "5"))))
                    capacity = size if capacity is None else min(capacity, size)
                    asks.append({"token": payload["asset_id"], "ask": ask, "size": size,
                                 "tick": tick, "minimum": minimum, "event_time": event.event_time})
                gross_cost = sum((item["ask"] for item in asks), D(0))
                adverse_cost = sum((item["ask"]+item["tick"] for item in asks), D(0))
                # Deliberately harsher than the current public fee endpoint: 10% curve sensitivity on both legs.
                fee = sum((D("0.10")*(item["ask"]+item["tick"])*(1-item["ask"]-item["tick"])
                           for item in asks), D(0))
                net_edge = D(1)-adverse_cost-fee
                minimum = max(item["minimum"] for item in asks)
                return {**base, "status": "OPPORTUNITY" if net_edge > 0 and capacity >= minimum else "NO_EDGE",
                        "condition_id": market["conditionId"], "asks": asks,
                        "gross_cost_per_pair": gross_cost, "gross_edge_per_pair": D(1)-gross_cost,
                        "conservative_cost_per_pair": adverse_cost+fee,
                        "conservative_edge_per_pair": net_edge, "capacity_pairs": capacity,
                        "minimum_pairs": minimum, "assumptions": {
                            "payout": "1 SIM_USD for a complete binary pair",
                            "adverse_selection": "one tick per leg",
                            "fee_sensitivity": "0.10*p*(1-p) per leg",
                            "execution": "two legs are not atomic; scan alone never counts a fill",
                        }}
            except (SourceError, KeyError, TypeError, ValueError) as exc:
                return {**base, "status": "ERROR", "reason": type(exc).__name__}

        inspected = await asyncio.gather(*(inspect(market) for market in markets.values()))
        ranked = sorted((item for item in inspected if "conservative_edge_per_pair" in item),
                        key=lambda item: D(str(item["conservative_edge_per_pair"])), reverse=True)
        result = {
            "as_of": datetime.now(UTC).isoformat(), "mode": "PUBLIC_READ_ONLY_SCAN",
            "markets_requested": limit, "markets_returned": len(markets),
            "eligible_books": len(ranked),
            "opportunities": [item for item in ranked if item["status"] == "OPPORTUNITY"],
            "top_near_misses": ranked[:20],
            "status_counts": {status: sum(item["status"] == status for item in inspected)
                              for status in sorted({item["status"] for item in inspected})},
            "financial_edge_proven": False,
        }
        REPORT.write_text(json.dumps(result, indent=2, ensure_ascii=False, default=str)+"\n", encoding="utf-8")
        return result
    finally:
        await source.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--markets", type=int, default=100)
    args = parser.parse_args()
    result = asyncio.run(scan(args.markets))
    print(json.dumps({key: result[key] for key in (
        "as_of", "markets_returned", "eligible_books", "opportunities", "top_near_misses",
        "status_counts")}, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
