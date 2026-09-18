import asyncio
import time
from decimal import Decimal

import httpx
import pytest

from edgehunter.intelligence.jev_commander import JevCommander, JevDecision
from edgehunter.intelligence.providers import CostBudget


class MockBook:
    def __init__(self, ask: str, size: str = "100.0", minimum: str = "0.01"):
        self.ask = Decimal(ask)
        self.size = Decimal(size)
        self.minimum = Decimal(minimum)


def test_jev_commander_enters_trade_on_strong_probability(tmp_path):
    async def scenario():
        # Jev returns high UP probability (0.75)
        mock_response = {
            "model": "jev-latest",
            "answers": {
                "terminal_outcome": {
                    "type": "choice",
                    "choice": "UP",
                    "probabilities": {"UP": 0.75, "DOWN": 0.25},
                    "confidence": 0.85,
                }
            },
            "usage": {"input_tokens": 120, "output_tokens": 8},
        }
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda req: httpx.Response(200, json=mock_response))
        )
        budget = CostBudget(tmp_path / "budget.db", Decimal("1.0"))
        commander = JevCommander(
            api_key="TEST_API_KEY",
            budget=budget,
            model="jev-latest",
            client=client,
        )

        up_book = MockBook("0.50")
        down_book = MockBook("0.52")
        tokens = ["token_up_123", "token_down_456"]

        decision: JevDecision = await commander.decide(
            slug="btc-updown-5m-100",
            seconds_to_expiry=150.0,
            spot_price=64200.0,
            open_price=64150.0,
            vol_per_second=0.00015,
            up_book=up_book,
            down_book=down_book,
            tokens=tokens,
            available_cash=Decimal("100.0"),
            available_until=time.time() + 10.0,
        )

        assert decision.action == "ENTER"
        assert decision.side == "UP"
        assert decision.asset_id == "token_up_123"
        assert decision.probability_up == 0.75
        assert decision.edge > 0
        assert decision.quantity > 0
        assert decision.total_cost > 0
        assert decision.jev_model == "jev-latest"
        await commander.close()

    asyncio.run(scenario())


def test_jev_commander_passes_when_no_edge(tmp_path):
    async def scenario():
        # Jev returns 50/50 probability
        mock_response = {
            "model": "jev-latest",
            "answers": {
                "terminal_outcome": {
                    "type": "choice",
                    "choice": "UP",
                    "probabilities": {"UP": 0.50, "DOWN": 0.50},
                    "confidence": 0.10,
                }
            },
            "usage": {"input_tokens": 120, "output_tokens": 8},
        }
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda req: httpx.Response(200, json=mock_response))
        )
        budget = CostBudget(tmp_path / "budget.db", Decimal("1.0"))
        commander = JevCommander(
            api_key="TEST_API_KEY",
            budget=budget,
            model="jev-latest",
            client=client,
        )

        up_book = MockBook("0.55")
        down_book = MockBook("0.55")
        tokens = ["token_up_123", "token_down_456"]

        decision: JevDecision = await commander.decide(
            slug="btc-updown-5m-100",
            seconds_to_expiry=150.0,
            spot_price=64200.0,
            open_price=64200.0,
            vol_per_second=0.00015,
            up_book=up_book,
            down_book=down_book,
            tokens=tokens,
            available_cash=Decimal("100.0"),
            available_until=time.time() + 10.0,
        )

        assert decision.action == "PASS"
        assert decision.side is None
        assert "NO_COSTED_EDGE" in decision.reason
        await commander.close()

    asyncio.run(scenario())


def test_jev_commander_passes_gracefully_on_missing_key(tmp_path):
    async def scenario():
        budget = CostBudget(tmp_path / "budget.db", Decimal("1.0"))
        commander = JevCommander(
            api_key="",
            budget=budget,
            model="jev-latest",
        )
        decision = await commander.decide(
            slug="btc-updown-5m-100",
            seconds_to_expiry=150.0,
            spot_price=64200.0,
            open_price=64200.0,
            vol_per_second=0.00015,
            up_book=MockBook("0.50"),
            down_book=MockBook("0.50"),
            tokens=["t1", "t2"],
            available_cash=Decimal("100.0"),
            available_until=time.time() + 10.0,
        )
        assert decision.action == "PASS"
        assert "KEY_MISSING" in decision.reason
        await commander.close()

    asyncio.run(scenario())


def test_jev_commander_fractional_kelly_sizing(tmp_path):
    async def scenario():
        mock_response = {
            "model": "jev-latest",
            "answers": {
                "terminal_outcome": {
                    "type": "choice",
                    "choice": "UP",
                    "probabilities": {"UP": 0.65, "DOWN": 0.35},
                    "confidence": 0.70,
                }
            },
            "usage": {"input_tokens": 100, "output_tokens": 10},
        }
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda req: httpx.Response(200, json=mock_response))
        )
        budget = CostBudget(tmp_path / "budget.db", Decimal("1.0"))
        commander = JevCommander(
            api_key="TEST_API_KEY",
            budget=budget,
            model="jev-latest",
            sizing_mode="fractional_kelly",
            kelly_fraction=Decimal("0.25"),
            max_stake_usd=Decimal("10.0"),
            min_stake_usd=Decimal("2.0"),
            client=client,
        )
        up_book = MockBook("0.50")
        down_book = MockBook("0.50")
        decision = await commander.decide(
            slug="btc-updown-5m-100",
            seconds_to_expiry=150.0,
            spot_price=64200.0,
            open_price=64180.0,
            vol_per_second=0.00015,
            up_book=up_book,
            down_book=down_book,
            tokens=["tok_u", "tok_d"],
            available_cash=Decimal("100.0"),
            available_until=time.time() + 10.0,
        )
        assert decision.action == "ENTER"
        assert decision.side == "UP"
        assert Decimal("2.0") <= decision.stake_usd <= Decimal("10.0")
        assert decision.kelly_fraction > 0.0
        assert "KELLY" in decision.reason
        await commander.close()

    asyncio.run(scenario())


def test_jev_commander_rejects_heavy_asymmetric_odds(tmp_path):
    async def scenario():
        mock_response = {
            "model": "jev-latest",
            "answers": {
                "terminal_outcome": {
                    "type": "choice",
                    "choice": "UP",
                    "probabilities": {"UP": 0.95, "DOWN": 0.05},
                    "confidence": 0.95,
                }
            },
            "usage": {"input_tokens": 100, "output_tokens": 10},
        }
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda req: httpx.Response(200, json=mock_response))
        )
        budget = CostBudget(tmp_path / "budget.db", Decimal("1.0"))
        commander = JevCommander(
            api_key="TEST_API_KEY",
            budget=budget,
            model="jev-latest",
            max_entry_price=Decimal("0.72"),
            client=client,
        )
        # UP ask is 0.75 (entry = 0.75 + 0.03 = 0.78 > 0.72)
        up_book = MockBook("0.75")
        down_book = MockBook("0.25")
        decision = await commander.decide(
            slug="btc-updown-5m-100",
            seconds_to_expiry=150.0,
            spot_price=64200.0,
            open_price=64100.0,
            vol_per_second=0.00015,
            up_book=up_book,
            down_book=down_book,
            tokens=["tok_u", "tok_d"],
            available_cash=Decimal("100.0"),
            available_until=time.time() + 10.0,
        )
        # Should be rejected by max_entry_price filter
        assert decision.action == "PASS"
        assert decision.side is None
        await commander.close()

    asyncio.run(scenario())


def forecast_response(probability=0.595, model="jev-1.13.1"):
    return {
        "model": model,
        "answers": {"terminal_outcome": {
            "type": "choice", "choice": "UP",
            "probabilities": {"UP": probability, "DOWN": 1 - probability},
            "confidence": 0.8,
        }},
        "usage": {"input_tokens": 120, "output_tokens": 8},
    }


async def decide_with_cash(commander, cash, deadline=None):
    return await commander.decide(
        slug="btc-updown-5m-100", seconds_to_expiry=150,
        spot_price=64200, open_price=64180, vol_per_second=0.00015,
        up_book=MockBook("0.50"), down_book=MockBook("0.50"),
        tokens=["up", "down"], available_cash=Decimal(cash),
        available_until=time.time() + 5 if deadline is None else deadline,
    )


@pytest.mark.parametrize("cash, fraction", [("10", "0.25"), ("100", "0")])
def test_kelly_never_raises_risk_to_reach_minimum(tmp_path, cash, fraction):
    async def scenario():
        client = httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=forecast_response())
        ))
        commander = JevCommander(
            api_key="TEST", budget=CostBudget(tmp_path / "budget.db", Decimal(1)),
            client=client, kelly_fraction=Decimal(fraction),
        )
        try:
            decision = await decide_with_cash(commander, cash)
            assert decision.action == "PASS"
            assert decision.reason == "KELLY_STAKE_BELOW_MINIMUM"
            assert decision.quantity == decision.total_cost == decision.stake_usd == 0
            assert decision.probability_up == 0.595
            assert decision.jev_model == "jev-1.13.1"
            assert decision.tokens_used["input_tokens"] == 120
            assert Decimal(cash) * Decimal(str(decision.kelly_fraction)) < Decimal("2")
        finally:
            await commander.close()
    asyncio.run(scenario())


def test_fixed_sizing_respects_maximum_and_records_resolved_model(tmp_path):
    async def scenario():
        client = httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=forecast_response())
        ))
        commander = JevCommander(
            api_key="TEST", budget=CostBudget(tmp_path / "budget.db", Decimal(1)),
            client=client, sizing_mode="fixed", fixed_stake_usd=Decimal("20"),
            max_stake_usd=Decimal("10"),
        )
        try:
            decision = await decide_with_cash(commander, "100")
            assert decision.action == "ENTER"
            assert decision.stake_usd == Decimal("10")
            assert decision.total_cost <= decision.stake_usd
            assert decision.jev_model == "jev-1.13.1"
        finally:
            await commander.close()
    asyncio.run(scenario())


def test_response_after_deadline_cannot_authorize_entry(tmp_path, monkeypatch):
    async def scenario():
        now = [100.0]

        def respond(request):
            now[0] = 102.0
            return httpx.Response(200, json=forecast_response(0.75))

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        commander = JevCommander(
            api_key="TEST", budget=CostBudget(tmp_path / "budget.db", Decimal(1)),
            client=client,
        )
        monkeypatch.setattr("edgehunter.intelligence.jev_commander.time.time", lambda: now[0])
        try:
            decision = await decide_with_cash(commander, "100", deadline=101.0)
            assert decision.action == "PASS"
            assert decision.reason == "DECISION_WINDOW_EXPIRED_AFTER_INFERENCE"
            assert decision.probability_up == 0.75
            assert decision.jev_model == "jev-1.13.1"
            assert decision.tokens_used["output_tokens"] == 8
            assert decision.total_cost == 0
        finally:
            await commander.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("configuration", [
    {"kelly_fraction": Decimal("-0.1")},
    {"kelly_fraction": Decimal("1.1")},
    {"kelly_fraction": Decimal("NaN")},
    {"slippage_per_share": Decimal("-0.01")},
    {"fee_curve_sensitivity": Decimal("-0.1")},
    {"min_edge": Decimal("-0.1")},
    {"max_call_cost": Decimal("0")},
    {"max_stake_usd": Decimal("Infinity")},
    {"fixed_stake_usd": Decimal("0")},
    {"min_stake_usd": Decimal("11"), "max_stake_usd": Decimal("10")},
    {"max_entry_price": Decimal("1")},
    {"visible_depth_fraction": Decimal("1.1")},
    {"sizing_mode": "misspelled_kelly"},
])
def test_invalid_risk_parameters_are_rejected_before_client_creation(tmp_path, configuration):
    with pytest.raises(ValueError):
        JevCommander(
            api_key="TEST", budget=CostBudget(tmp_path / "budget.db", Decimal(1)),
            **configuration,
        )
