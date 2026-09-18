"""Read-only external contract probe. No secrets, orders or email."""
import asyncio
import json
from pathlib import Path

from edgehunter.venues.polymarket import PolymarketPublic


async def main() -> None:
    client = PolymarketPublic()
    result = {}
    try:
        for name, operation in (("clock", client.clock), ("geoblock", client.geoblock),
                                ("markets", client.markets)):
            try:
                value = await operation()
                result[name] = value
            except Exception as exc:
                result[name] = {"status": "BLOCKED", "error": str(exc)}
        if isinstance(result.get("markets"), list) and result["markets"]:
            try:
                result["snapshot"] = await client.snapshot(result["markets"][0])
            except Exception as exc:
                result["snapshot"] = {"status": "BLOCKED", "error": str(exc)}
        Path("data").mkdir(exist_ok=True)
        Path("data/public_probe.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps({k: ({"count": len(v), "first": v[0].get("question") if v else None}
                              if isinstance(v, list) else v) for k, v in result.items()}, indent=2)[:18000])
    finally:
        await client.close()


asyncio.run(main())
