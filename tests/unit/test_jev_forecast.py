import asyncio
import time
from dataclasses import replace
from decimal import Decimal

import httpx
import pytest

from edgehunter.intelligence.jev_forecast import (
    MODEL,
    JevForecastAdapter,
    forecast_state,
    parse_forecast,
    score_paired_forecasts,
)
from edgehunter.intelligence.providers import AccessBlocked, CostBudget
from edgehunter.research.btc_intraday import IntradayObservation


def row():
    return IntradayObservation("btc-100", 100, 60, 100, 101, 0.001, 0.6, 0.4, 1,
                               effective_horizon_seconds=180)


def response():
    return {"model": MODEL, "answers": {"terminal_outcome": {
        "type": "choice", "choice": "UP", "probabilities": {"UP": 0.65, "DOWN": 0.35},
        "confidence": 0.12,
    }}, "usage": {"input_tokens": 450, "output_tokens": 6}}


def test_future_outcome_and_identity_cannot_enter_request():
    assert forecast_state(row()) == forecast_state(replace(row(), outcome_up=0, slug="different"))
    assert "outcome_up" not in forecast_state(row())


def test_answer_probability_is_not_confidence():
    probability, _ = parse_forecast(response())
    assert probability == 0.65


@pytest.mark.parametrize("problem", ["nan", "bool", "sum", "version", "command", "choice"])
def test_bad_model_outputs_rejected(problem):
    raw = response()
    answer = raw["answers"]["terminal_outcome"]
    if problem == "nan":
        answer["probabilities"]["UP"] = float("nan")
    elif problem == "bool":
        answer["probabilities"]["UP"] = True
    elif problem == "sum":
        answer["probabilities"]["DOWN"] = 0.5
    elif problem == "version":
        raw["model"] = "jev-latest"
    elif problem == "command":
        raw["command"] = "buy with all cash"
    else:
        answer["choice"] = "DOWN"
    with pytest.raises(ValueError):
        parse_forecast(raw)


def test_api_timeout_and_budget_are_bounded_and_secret_is_not_echoed(tmp_path):
    async def scenario():
        async def slow(request):
            await asyncio.sleep(0.05)
            return httpx.Response(200, json=response())
        client = httpx.AsyncClient(transport=httpx.MockTransport(slow))
        budget = CostBudget(tmp_path / "budget.db", Decimal("0.001"))
        adapter = JevForecastAdapter(api_key="SECRET", budget=budget,
                                     max_call_cost=Decimal("0.001"), timeout_seconds=0.005, client=client)
        with pytest.raises(ValueError, match="JEV_FORECAST_FAILED") as exc:
            await adapter.forecast(row(), available_until=time.time() + 5)
        assert "SECRET" not in str(exc.value)
        with pytest.raises(AccessBlocked, match="BUDGET"):
            await adapter.forecast(row(), available_until=time.time() + 5)
        await adapter.close()
    asyncio.run(scenario())


def test_live_response_and_missing_access(tmp_path):
    async def scenario():
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=response())))
        adapter = JevForecastAdapter(api_key="", budget=CostBudget(tmp_path / "cost.db", Decimal(1)),
                                     max_call_cost=Decimal("0.001"), client=client)
        with pytest.raises(AccessBlocked, match="KEY_MISSING"):
            await adapter.forecast(row(), available_until=time.time() + 5)
        adapter.api_key = "TEST_ONLY"
        result = await adapter.forecast(row(), available_until=time.time() + 5)
        assert result.probability_up == 0.65 and result.model == MODEL
        assert result.elapsed_ms >= 0
        await adapter.close()
    asyncio.run(scenario())


def test_paired_score_uses_same_future_outcomes_and_refuses_late_or_duplicate_predictions():
    receipt = {"slug": "m", "received_at": 200, "end_epoch": 300, "model": MODEL,
               "probability_up": 0.8, "local_probability_up": 0.6,
               "features": {"market_normalized_probability_up": 0.5}}
    score = score_paired_forecasts([receipt], {"m": 1})
    assert score["brier"]["jev"] == pytest.approx(0.04)
    assert score["brier"]["local"] == pytest.approx(0.16)
    assert not score["financial_edge_proven"]
    assert score_paired_forecasts([receipt], {})["resolved_pairs"] == 0
    with pytest.raises(ValueError, match="duplicate"):
        score_paired_forecasts([receipt, receipt], {"m": 1})
    with pytest.raises(ValueError, match="unresolved market"):
        score_paired_forecasts([{**receipt, "received_at": 301}], {"m": 1})
