import asyncio
import json

from edgehunter.research.btc_history_download import (
    CATALOG_PAGE_SIZE,
    DownloadConfig,
    _batch_price_records,
    append_jsonl,
    build_observations,
    checkpoint_state,
    download_catalog,
    fetch_trade_histories,
    initialize_output,
    merge_shards,
    select_event,
)


def gamma_event(epoch: int, *, event_id: int = 1, closed: bool = True):
    return {
        "id": str(event_id),
        "slug": f"btc-updown-5m-{epoch}",
        "closed": closed,
        "startDate": "2099-01-01T00:00:00Z",
        "startTime": "2000-01-01T00:00:00Z",
        "series": [{"id": "10684"}],
        "markets": [{
            "id": str(event_id + 100),
            "closed": closed,
            "outcomes": '["Up", "Down"]',
            "outcomePrices": '["1", "0"]',
            "clobTokenIds": '["123", "456"]',
            "conditionId": "0xcondition",
        }],
    }


class FakeHTTP:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    async def json(self, url, params=None):
        self.calls.append((url, dict(params or {})))
        return self.pages[params.get("after_cursor", params.get("cursor"))]


def config(*, start=1_800_000_000, end=1_800_060_000):
    return DownloadConfig(start, end, end, shard_index=0, shard_count=1)


def test_temporal_selection_uses_slug_epoch_and_excludes_end_or_unresolved():
    start = 1_800_000_000
    end = start + 900
    first = select_event(gamma_event(start), start=start, end=end, as_of=end)
    assert first is not None
    assert first["epoch"] == start
    assert first["event_start_time"] == "2000-01-01T00:00:00Z"
    assert select_event(gamma_event(end), start=start, end=end, as_of=end + 300) is None
    assert select_event(gamma_event(start + 600), start=start, end=end, as_of=start + 700) is None
    assert select_event(gamma_event(start, closed=False), start=start, end=end, as_of=end) is None


def test_catalog_paginates_at_gamma_cap_and_checkpoints(tmp_path):
    cfg = config()
    page = [gamma_event(cfg.requested_start + index * 300, event_id=index) for index in range(CATALOG_PAGE_SIZE)]
    tail = [gamma_event(cfg.requested_start + CATALOG_PAGE_SIZE * 300, event_id=1000)]
    http = FakeHTTP({None: {"events": page, "next_cursor": "page-2"},
                     "page-2": {"events": tail}})
    rows = asyncio.run(download_catalog(http, cfg, tmp_path))

    assert [call[1].get("after_cursor") for call in http.calls] == [None, "page-2"]
    assert all(call[1]["limit"] == 100 for call in http.calls)
    assert len(rows) == 101
    assert checkpoint_state(tmp_path / "checkpoint.jsonl") == {
        "catalog_cursor": None,
        "catalog_complete": True,
    }


def test_catalog_resume_uses_last_valid_checkpoint_and_deduplicates(tmp_path):
    cfg = config(end=1_800_090_000)
    output = tmp_path
    output.mkdir(exist_ok=True)
    (output / "config.json").write_text(
        json.dumps(cfg.document(), sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    existing = gamma_event(cfg.requested_start, event_id=1)
    selected = select_event(
        existing, start=cfg.shard_start, end=cfg.shard_end, as_of=cfg.as_of,
    )
    (output / "events.jsonl").write_text(json.dumps(selected) + "\n", encoding="utf-8")
    (output / "checkpoint.jsonl").write_text(
        '{"phase":"catalog","next_cursor":"resume-here","complete":false}\n{"phase":', encoding="utf-8"
    )
    resumed = gamma_event(cfg.requested_start + 300, event_id=2)
    http = FakeHTTP({"resume-here": {"events": [existing, resumed]}})

    rows = asyncio.run(download_catalog(http, cfg, output))

    assert http.calls[0][1]["after_cursor"] == "resume-here"
    assert [row["slug"] for row in rows] == [existing["slug"], resumed["slug"]]
    assert len((output / "events.jsonl").read_text(encoding="utf-8").splitlines()) == 2


def test_trade_cursor_stops_after_both_outcomes_exist_before_earliest_decision():
    epoch = 1_800_000_000
    cfg = config(start=epoch, end=epoch + 600)
    event = select_event(gamma_event(epoch), start=epoch, end=epoch + 600, as_of=epoch + 600)
    assert event is not None
    newer = {
        "data": [{"token_id": "123", "timestamp": epoch + 230, "price": 0.7, "size": 1}],
        "pagination": {"next_cursor": "older"},
    }
    older = {
        "data": [
            {"token_id": "123", "timestamp": epoch + 110, "price": 0.55, "size": 1},
            {"token_id": "456", "timestamp": epoch + 115, "price": 0.45, "size": 1},
        ],
        "pagination": {"next_cursor": "unused"},
    }
    http = FakeHTTP({None: newer, "older": older})

    histories = asyncio.run(fetch_trade_histories(http, event, cfg))

    assert [call[1] for call in http.calls] == [
        {"condition": "0xcondition", "limit": 1000},
        {"condition": "0xcondition", "limit": 1000, "cursor": "older"},
    ]
    assert [[point["timestamp"] for point in history] for history in histories] == [
        [epoch + 230, epoch + 110], [epoch + 115],
    ]


def test_batch_history_is_primary_and_drops_points_after_feature_cutoff():
    epoch = 1_800_000_000
    cfg = config(start=epoch, end=epoch + 600)
    event = select_event(gamma_event(epoch), start=epoch, end=epoch + 600, as_of=epoch + 600)
    assert event is not None

    class BatchHTTP:
        def __init__(self):
            self.body = None

        async def post_json(self, _url, body):
            self.body = body
            return {"history": {
                "123": [{"t": epoch + 50, "p": 0.6}, {"t": epoch + 250, "p": 0.9}],
                "456": [{"t": epoch + 55, "p": 0.4}, {"t": epoch + 250, "p": 0.1}],
            }}

        async def json(self, _url, _params=None):
            raise AssertionError("trade fallback should not run")

    http = BatchHTTP()
    records = asyncio.run(_batch_price_records(http, [event], cfg))

    assert http.body == {
        "markets": ["123", "456"],
        "start_ts": epoch,
        "end_ts": epoch + 300,
        "fidelity": 1,
    }
    assert records[0]["coverage"] == "AVAILABLE"
    assert [[point["timestamp"] for point in item["points"]] for item in records[0]["histories"]] == [
        [epoch + 50], [epoch + 55],
    ]


def test_observation_builder_uses_120_prior_closes_and_predecision_ticks(tmp_path):
    epoch = 1_800_000_000
    cfg = DownloadConfig(epoch, epoch + 600, epoch + 600, horizons=(180,))
    event = select_event(gamma_event(epoch), start=epoch, end=epoch + 600, as_of=epoch + 600)
    assert event is not None
    append_jsonl(tmp_path / "events.jsonl", event)
    append_jsonl(tmp_path / "prices.jsonl", {
        "slug": event["slug"],
        "histories": [
            {"points": [{"timestamp": epoch + 100, "price": 0.61},
                         {"timestamp": epoch + 130, "price": 0.99}]},
            {"points": [{"timestamp": epoch + 110, "price": 0.39},
                         {"timestamp": epoch + 130, "price": 0.01}]},
        ],
    })
    decision = epoch + 120
    aligned_feature = epoch + 60
    for index, timestamp in enumerate(range(aligned_feature - 7200, decision + 60, 60)):
        append_jsonl(tmp_path / "candles.jsonl", {
            "timestamp": timestamp,
            "open": 100.0 + index / 100,
            "close": 100.01 + index / 100,
        })

    rows, exclusions = build_observations(tmp_path, cfg)

    assert exclusions == []
    assert len(rows) == 1
    assert rows[0]["up_price"] == 0.61
    assert rows[0]["down_price"] == 0.39
    assert rows[0]["spot_price"] == 101.2
    assert rows[0]["vol_per_second"] > 0


def test_merge_orders_shards_and_preserves_observation_contract(tmp_path):
    epoch = 1_800_000_000
    for index in range(2):
        cfg = DownloadConfig(epoch, epoch + 600, epoch + 600, shard_index=index, shard_count=2)
        shard = tmp_path / f"shard-{index:03d}-of-002"
        initialize_output(shard, cfg)
        market_epoch = epoch + index * 300
        append_jsonl(shard / "events.jsonl", {"slug": f"btc-updown-5m-{market_epoch}", "epoch": market_epoch})
        append_jsonl(shard / "prices.jsonl", {"slug": f"btc-updown-5m-{market_epoch}", "epoch": market_epoch})
        append_jsonl(shard / "candles.jsonl", {"timestamp": market_epoch, "open": 100 + index})
        append_jsonl(shard / "observations.jsonl", {
            "slug": f"btc-updown-5m-{market_epoch}",
            "epoch": market_epoch,
            "horizon_seconds": 60,
            "open_price": 100.0,
            "spot_price": 101.0,
            "vol_per_second": 0.001,
            "up_price": 0.6,
            "down_price": 0.4,
            "outcome_up": 1,
        })

    result = merge_shards(tmp_path, 2)
    rows = [json.loads(line) for line in (tmp_path / "merged" / "observations.jsonl").read_text().splitlines()]

    assert result["observations"] == 2
    assert [row["epoch"] for row in rows] == [epoch, epoch + 300]
    assert set(rows[0]) == {
        "slug", "epoch", "horizon_seconds", "open_price", "spot_price", "vol_per_second",
        "up_price", "down_price", "outcome_up",
    }
