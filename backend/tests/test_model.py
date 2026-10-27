import math
from datetime import timedelta

import pytest
from pydantic import ValidationError

from betboot.domain import MatchObservation
from betboot.features import team_form
from betboot.models.poisson import InsufficientData, ModelConfig, markets, poisson_mass, predict


@pytest.mark.parametrize("home,away", [(1.5, 1.1), (0.1, 0.1), (8, 4), (20, 20)])
def test_market_probabilities(home, away):
    p = markets(home, away)
    assert p["home"] + p["draw"] + p["away"] == pytest.approx(1, abs=1e-10)
    assert p["btts_yes"] == pytest.approx((1-math.exp(-home))*(1-math.exp(-away)), abs=1e-10)
    for line in (1.5, 2.5, 3.5):
        assert p[f"over_{line}"] + p[f"under_{line}"] == pytest.approx(1)
        expected_under = sum(math.exp(-home-away)*(home+away)**k/math.factorial(k)
                             for k in range(int(line)+1))
        assert p[f"under_{line}"] == pytest.approx(expected_under, abs=1e-10)
    assert p["1X"] == pytest.approx(1-p["away"])
    assert p["X2"] == pytest.approx(1-p["home"])
    assert p["12"] == pytest.approx(1-p["draw"])
    assert all(0 <= v <= 1 for v in p.values())


@pytest.mark.parametrize("rate", [0, -1, float("nan"), float("inf"), 21])
def test_invalid_rates(rate):
    with pytest.raises(ValueError):
        poisson_mass(rate)


def test_baseline_reproducibility_and_no_invented_fields(fixture, history, now):
    result = predict(fixture, history, now)
    assert result == predict(fixture, list(reversed(history)), now)
    assert result["calibrated"] is False
    assert result["recommendation_eligible"] is False
    assert result["data_quality"]["score"] is None
    for p in result["markets"].values():
        assert p["fair_odds"] == pytest.approx(1 / p["probability"])
        assert p["expected_value"] is None
        assert p["uncertainty_interval"] is None


def test_future_observations_and_other_leagues_do_not_leak(fixture, history, now):
    future = history[0].model_copy(update={"external_id": "late", "home_goals": 99,
                                          "collected_at": now + timedelta(seconds=1)})
    other = history[0].model_copy(update={"external_id": "other", "league": "SA"})
    actual = predict(fixture, history + [future, other], now)
    assert actual == predict(fixture, history, now)


def test_duplicate_results_do_not_inflate_sample(fixture, history, now):
    assert predict(fixture, history * 2, now) == predict(fixture, history, now)


def test_retracted_result_cannot_remain_in_training(fixture, history, now):
    revised = history[0].model_copy(update={"status": "CANCELLED", "home_goals": None,
                                           "away_goals": None, "collected_at": now})
    result = predict(fixture, history + [revised], now)
    assert history[0].external_id not in result["training_match_ids"]


def test_abstains_on_small_sample_and_stale_fixture(fixture, history, now):
    with pytest.raises(InsufficientData):
        predict(fixture, history[:4], now)
    with pytest.raises(InsufficientData):
        predict(fixture.model_copy(update={"collected_at": now-timedelta(days=2)}), history, now)
    with pytest.raises(InsufficientData):
        predict(fixture.model_copy(update={"status": "SCHEDULED"}), history, now)
    with pytest.raises(InsufficientData):
        predict(fixture.model_copy(update={"kickoff": now}), history, now)


def test_timezone_required(fixture, history, now):
    with pytest.raises(ValueError):
        predict(fixture, history, now.replace(tzinfo=None))


def test_missing_score_is_not_zero(fixture, now):
    data = fixture.model_dump()
    data.update(status="FINISHED", kickoff=now-timedelta(days=1), home_goals=None, away_goals=0)
    with pytest.raises(ValidationError):
        MatchObservation.model_validate(data)


def test_form_reports_incomplete_windows(fixture, history, now):
    result = team_form(history[:3], fixture.source, fixture.home_id, now)
    assert result["home"]["5"]["observed_matches"] == 3
    assert result["home"]["5"]["complete_window"] is False
    assert result["away"]["5"]["goals_for"] is None
    assert result["all"]["5"]["xg"] is None


def test_configuration_validation():
    with pytest.raises(ValueError):
        ModelConfig(half_life_days=0)
