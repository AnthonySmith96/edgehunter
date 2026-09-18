"""Resume-safe historical downloader for Polymarket BTC Up/Down 5m."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from edgehunter.research.btc_history_download import (  # noqa: E402
    DownloadConfig,
    HistoricalHTTP,
    merge_shards,
    run_download,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data" / "research_btc_history"


def aligned_epoch(value: str | None) -> int:
    if value is None:
        epoch = int(datetime.now(UTC).timestamp())
    elif value.isdecimal():
        epoch = int(value)
    else:
        epoch = int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
    return epoch // 300 * 300


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=30, help="Window length, from 30 through 270 days")
    parser.add_argument("--start-days-ago", type=int, help="Older window edge; overrides --days")
    parser.add_argument("--end-days-ago", type=int, default=0, help="Newer window edge")
    parser.add_argument("--as-of", help="UTC ISO timestamp or epoch; rounded down to five minutes")
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--merge-only", action="store_true")
    return parser.parse_args()


async def download(args: argparse.Namespace) -> dict[str, object]:
    as_of = aligned_epoch(args.as_of)
    start_days = args.start_days_ago if args.start_days_ago is not None else args.days
    if not 30 <= start_days <= 270:
        raise ValueError("start range must be between 30 and 270 days ago")
    if not 0 <= args.end_days_ago < start_days:
        raise ValueError("end-days-ago must be nonnegative and less than the older edge")
    config = DownloadConfig(
        requested_start=as_of - start_days * 86400,
        requested_end=as_of - args.end_days_ago * 86400,
        as_of=as_of,
        shard_index=args.shard_index,
        shard_count=args.shard_count,
    )
    output = args.output / f"shard-{args.shard_index:03d}-of-{args.shard_count:03d}"
    http = HistoricalHTTP(interval=0.03, max_bytes=16_000_000)
    try:
        return await run_download(http, config, output, workers=args.workers)
    finally:
        await http.close()


def main() -> None:
    args = parse_args()
    if args.merge_only:
        result = merge_shards(args.output, args.shard_count)
    else:
        result = asyncio.run(download(args))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
